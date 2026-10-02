"""Judge a requirement set against one Forward snapshot.

The rules are deliberately strict. A requirement passes only on positive evidence: no paths, an
outcome string this code does not know, or a query error never becomes PASS. A denied flow must be
denied *by policy* on the named device (an ``ACL_DENY`` hop), not merely unreachable.

Evaluation reads Forward through an :class:`Observer`, so the same rules run against a live
snapshot (``forward.SnapshotObserver``) and against recorded responses in tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from workshop.requirements import Requirement, RequirementSet

KNOWN_FORWARDING = {
    "DELIVERED",
    "DELIVERED_TO_INCORRECT_LOCATION",
    "BLACKHOLE",
    "DROPPED",
    "INADMISSIBLE",
    "UNREACHABLE",
    "LOOP",
}
KNOWN_SECURITY = {"PERMITTED", "DENIED"}

# Route evidence: the RIB entry for one prefix on one device, with its next hops. Device and prefix
# come from a validated requirement file, never from a pull request.
ROUTE_NQE = """
foreach d in network.devices
where d.name == "{device}"
foreach ni in d.networkInstances
foreach e in ni.afts.ipv4Unicast.ipEntries
where toString(e.prefix) == "{prefix}"
select {{ device: d.name, vrf: ni.name, prefix: toString(e.prefix),
         nextHops: (foreach nh in e.nextHops select {{ ip: nh.ipAddress, interface: nh.interfaceName, protocol: nh.originProtocol }}) }}
"""


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    INCONCLUSIVE = "INCONCLUSIVE"


class Observer(Protocol):
    """Answers questions about one snapshot."""

    def paths(self, req: Requirement, intent: str) -> dict[str, Any]:
        """Path search for the requirement's flow; the response as Forward's JSON (camelCase)."""

    def nqe(self, query: str) -> list[dict[str, Any]]:
        """Run an NQE query; its rows."""


class ObserverTimeout(Exception):
    """Forward did not answer in time; the result is INCONCLUSIVE, never PASS."""


@dataclass(frozen=True)
class Result:
    id: str
    title: str
    status: Status
    expected: str
    observed: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "expected": self.expected,
            "observed": self.observed,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class Report:
    set_name: str
    requirements_sha256: str
    snapshot_id: str
    results: tuple[Result, ...]

    @property
    def status(self) -> Status:
        return aggregate([r.status for r in self.results])

    def by_id(self, requirement_id: str) -> Result:
        return next(r for r in self.results if r.id == requirement_id)

    def to_json(self) -> dict[str, Any]:
        return {
            "set": self.set_name,
            "requirementsSha256": self.requirements_sha256,
            "snapshotId": self.snapshot_id,
            "status": self.status.value,
            "results": [r.to_json() for r in self.results],
        }


def aggregate(statuses: list[Status]) -> Status:
    """Overall result: any ERROR, else any FAIL, else any INCONCLUSIVE, else PASS. Empty is INCONCLUSIVE."""
    if not statuses:
        return Status.INCONCLUSIVE
    for status in (Status.ERROR, Status.FAIL, Status.INCONCLUSIVE):
        if status in statuses:
            return status
    return Status.PASS


def evaluate(reqset: RequirementSet, observer: Observer, snapshot_id: str, *, root: Path) -> Report:
    results = tuple(_one(req, observer, root) for req in reqset.requirements)
    return Report(reqset.name, reqset.sha256, snapshot_id, results)


def _one(req: Requirement, observer: Observer, root: Path) -> Result:
    try:
        if req.kind == "flow":
            return _flow(req, observer)
        if req.kind == "path":
            return _path(req, observer)
        if req.kind == "route":
            return _route(req, observer)
        return _nqe(req, observer, root)
    except ObserverTimeout as exc:
        return Result(req.id, req.title, Status.INCONCLUSIVE, _expected(req), "Forward did not answer in time", {"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - any failure to observe is an ERROR, never a pass
        return Result(req.id, req.title, Status.ERROR, _expected(req), f"could not evaluate: {type(exc).__name__}", {"error": str(exc)[:500]})


def _expected(req: Requirement) -> str:
    if req.kind == "flow":
        return f"{req.flow_label} {'permitted and delivered' if req.expect == 'permit' else f'denied by ACL on {req.deny_at}'}"
    if req.kind == "path":
        return f"{req.flow_label} via {' -> '.join(req.path)}"
    if req.kind == "route":
        return f"{req.device} routes {req.prefix} via {req.next_hop} (BGP)"
    return f"{req.expect_rows} row(s) from {req.query_file}"


def _summarize(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "forwarding": p.get("forwardingOutcome"),
            "security": p.get("securityOutcome"),
            "hops": [h.get("deviceName") for h in p.get("hops") or []],
            "behaviors": {h.get("deviceName"): h.get("behaviors") or [] for h in p.get("hops") or []},
        }
        for p in paths
    ]


def _paths_of(response: dict[str, Any]) -> list[dict[str, Any]]:
    info = response.get("info") or {}
    return list(info.get("paths") or [])


def _flow(req: Requirement, observer: Observer) -> Result:
    # Ask for the paths most likely to contradict the expectation.
    intent = "PREFER_VIOLATIONS" if req.expect == "permit" else "PREFER_DELIVERED"
    response = observer.paths(req, intent)
    paths = _paths_of(response)
    evidence = {"intent": intent, "paths": _summarize(paths), "queryUrl": response.get("queryUrl")}
    expected = _expected(req)
    if response.get("timedOut"):
        return Result(req.id, req.title, Status.INCONCLUSIVE, expected, "path search timed out", evidence)
    if not paths:
        return Result(req.id, req.title, Status.INCONCLUSIVE, expected, "no path found for this flow", evidence)
    for p in paths:
        if p.get("forwardingOutcome") not in KNOWN_FORWARDING or p.get("securityOutcome") not in KNOWN_SECURITY:
            observed = f"unrecognized outcome {p.get('forwardingOutcome')}/{p.get('securityOutcome')}"
            return Result(req.id, req.title, Status.INCONCLUSIVE, expected, observed, evidence)
    outcomes = sorted({f"{p['forwardingOutcome']}/{p['securityOutcome']}" for p in paths})
    if req.expect == "permit":
        ok = all(p["forwardingOutcome"] == "DELIVERED" and p["securityOutcome"] == "PERMITTED" for p in paths)
        return Result(req.id, req.title, Status.PASS if ok else Status.FAIL, expected, ", ".join(outcomes), evidence)
    # deny: every path must be refused by policy, on the named device
    denied_there = all(
        p["securityOutcome"] == "DENIED"
        and any(h.get("deviceName") == req.deny_at and "ACL_DENY" in (h.get("behaviors") or []) for h in p.get("hops") or [])
        for p in paths
    )
    observed = ", ".join(outcomes)
    if not denied_there:
        observed += f" -- not denied by an ACL on {req.deny_at}"
    return Result(req.id, req.title, Status.PASS if denied_there else Status.FAIL, expected, observed, evidence)


def _path(req: Requirement, observer: Observer) -> Result:
    response = observer.paths(req, "PREFER_DELIVERED")
    paths = _paths_of(response)
    evidence = {"intent": "PREFER_DELIVERED", "paths": _summarize(paths), "queryUrl": response.get("queryUrl")}
    expected = _expected(req)
    if response.get("timedOut"):
        return Result(req.id, req.title, Status.INCONCLUSIVE, expected, "path search timed out", evidence)
    if not paths:
        return Result(req.id, req.title, Status.INCONCLUSIVE, expected, "no path found for this flow", evidence)
    delivered = [p for p in paths if p.get("forwardingOutcome") == "DELIVERED"]
    if not delivered:
        outcomes = sorted({str(p.get("forwardingOutcome")) for p in paths})
        return Result(req.id, req.title, Status.FAIL, expected, f"not delivered ({', '.join(outcomes)})", evidence)
    routes = sorted({" -> ".join(h.get("deviceName") or "?" for h in p.get("hops") or []) for p in delivered})
    ok = routes == [" -> ".join(req.path)]
    return Result(req.id, req.title, Status.PASS if ok else Status.FAIL, expected, "; ".join(routes), evidence)


def _route(req: Requirement, observer: Observer) -> Result:
    rows = observer.nqe(ROUTE_NQE.format(device=req.device, prefix=req.prefix))
    expected = _expected(req)
    hops = [nh for row in rows for nh in row.get("nextHops") or []]
    evidence = {"rows": rows}
    if not rows:
        return Result(req.id, req.title, Status.FAIL, expected, f"{req.device} has no route to {req.prefix}", evidence)
    ok = any(nh.get("ip") == req.next_hop and nh.get("protocol") == "BGP" for nh in hops)
    observed = ", ".join(f"via {nh.get('ip')} ({nh.get('protocol')})" for nh in hops) or "a route with no next hop"
    return Result(req.id, req.title, Status.PASS if ok else Status.FAIL, expected, observed, evidence)


def _nqe(req: Requirement, observer: Observer, root: Path) -> Result:
    rows = observer.nqe((root / req.query_file).read_text())
    ok = len(rows) == req.expect_rows
    observed = f"{len(rows)} row(s)"
    if rows:
        observed += ": " + "; ".join(json.dumps(r, sort_keys=True)[:120] for r in rows[:5])
    return Result(req.id, req.title, Status.PASS if ok else Status.FAIL, _expected(req), observed, {"rows": rows})
