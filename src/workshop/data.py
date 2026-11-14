"""What Forward knows about the network, as structured data you can read without opening Forward.

Each function returns a ``Table``: a one-sentence ``summary`` in plain language, the ``columns`` and ``rows``
behind it, and the ``raw`` structured answer Forward gave. The same tables feed ``workshop data`` and the lab
page, so a beginner reads the sentence and an advanced user filters the rows and reads the raw JSON.

Everything here is read-only and deterministic: it is Forward's model, not AI. Nothing in the gate reads it.
All calls go through the Forward Python SDK.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MAX_ROWS = 300

_INVENTORY = """foreach d in network.devices
select {name: d.name, vendor: d.platform.vendor, os: d.platform.os, version: d.platform.osVersion,
        model: d.platform.model, support: d.platform.osSupport}"""

_ROUTES = """foreach device in network.devices
foreach vrf in device.networkInstances
where isPresent(vrf.afts.ipv4Unicast)
foreach e in vrf.afts.ipv4Unicast.ipEntries
foreach nh in e.nextHops
select {device: device.name, prefix: e.prefix, protocol: nh.originProtocol, kind: nh.nextHopType, via: nh.ipAddress, interface: nh.interfaceName}"""

TOPICS = {
    "inventory": "Devices: what Forward found, and what it runs",
    "routes": "Routes: every IPv4 route, per device",
    "diff": "Route changes: what the change did to the routing tables",
    "vulns": "CVEs: which published vulnerabilities actually affect your devices",
    "blast": "Blast radius: which addresses traffic from one place can reach",
    "checks": "Posture: Forward's own checks on the network, and which ones a change moves",
}


@dataclass
class Table:
    topic: str
    title: str
    summary: str
    columns: list[str]
    rows: list[dict[str, Any]]
    note: str = ""
    truncated: bool = False
    raw: Any = field(default=None, repr=False)
    query: str = ""  # the exact NQE query, or the SDK call, behind this table: shown so nothing is a black box
    query_kind: str = ""  # "NQE query" | "SDK call"

    def to_json(self, *, with_raw: bool = True) -> dict[str, Any]:
        out = asdict(self)
        if not with_raw:
            out.pop("raw")
        return out


def _dump(response: Any, *, alias: bool = False) -> Any:
    """The SDK returns typed models for some calls and plain dicts for others (the diff rows are not declared in Forward's API).

    ``alias=True`` gives Forward's own field names (camelCase), which is what the page shows as "what Forward returned".
    """
    return response.model_dump(mode="json", by_alias=alias) if hasattr(response, "model_dump") else response


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class DataError(RuntimeError):
    """A question that cannot be answered (no such snapshot, Forward not ready); the message says what to do."""


class NoSuchSnapshot(DataError):
    """That step has not run yet, so there is nothing to look at."""


def snapshot_for(settings, evidence_path, which: str) -> str:
    """baseline | predicted | post | <snapshot id>: the snapshot a question is about."""
    import json

    def read(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    if which == "baseline":
        from workshop import steps

        found = steps.baseline_snapshot(settings)
        if found:
            return found
    elif which == "predicted":
        data = read(Path(evidence_path))
        if data.get("predicted_snapshot_id"):
            return str(data["predicted_snapshot_id"])
    elif which == "post":
        data = read(settings.state_dir / "verify.json")
        if data.get("post_snapshot_id"):
            return str(data["post_snapshot_id"])
    elif re.fullmatch(r"[0-9]{1,12}", which):
        return which
    raise NoSuchSnapshot(f"there is no {which[:30]} snapshot yet: run that step first")


def prediction_pair(settings, evidence_path) -> tuple[str, str]:
    """(before, after) for the latest prediction: its own baseline, and only if it is a prediction of the change in the candidate file now."""
    from workshop import candidate
    from workshop.evidence import Manifest

    try:
        m = Manifest.read(Path(evidence_path))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise NoSuchSnapshot("there is no predicted snapshot yet: run workshop predict (or open a pull request) first") from exc
    if not m.predicted_snapshot_id:
        raise NoSuchSnapshot("that prediction did not produce a predicted network: look at its result first")
    try:
        current = candidate.load(settings.repo_root / "candidate" / "r4-bgp.eos").sha256
    except (candidate.CandidateError, OSError):
        current = ""
    if current != m.candidate_sha256:
        raise DataError("the last prediction was of a different change than the one in candidate/r4-bgp.eos now: predict again to see this one")
    return str(m.baseline_snapshot_id), str(m.predicted_snapshot_id)


# ---- single-snapshot questions --------------------------------------------------------------


def inventory(client, snapshot_id: str) -> Table:
    forward_rows = [dict(r) for r in client.nqe.query(_INVENTORY, snapshot_id=snapshot_id)]
    rows = sorted((dict(r) for r in forward_rows), key=lambda r: str(r.get("name")))  # the table reads r1, r2, r3, r4
    for r in rows:
        support = r.pop("support", None)
        r["end of support"] = _support_text(support)
    kinds = sorted({f"{r['vendor']} {r['model']} on {r['version']}".replace("ARISTA", "Arista") for r in rows})
    summary = f"{plural(len(rows), 'device')}: " + "; ".join(kinds) + "." if rows else "Forward found no devices in this snapshot."
    no_lifecycle = all(r["end of support"] == "no data" for r in rows)
    note = ("Forward has no end-of-support dates for these devices: they are lab images, not hardware or a released OS in "
            "Forward's lifecycle table. On a real network this column lists the OS maintenance and support dates.") if rows and no_lifecycle else ""
    return Table("inventory", TOPICS["inventory"], summary, ["name", "vendor", "model", "os", "version", "end of support"], rows, note, raw=forward_rows, query=_INVENTORY, query_kind="NQE query")


def _support_text(support: Any) -> str:
    if not support:
        return "no data"
    dates = [f"{k}: {v}" for k, v in dict(support).items() if v and "Date" in k]
    return "; ".join(dates) or "no data"


def routes(client, snapshot_id: str, device: str = "", prefix: str = "") -> Table:
    forward_rows = [dict(r) for r in client.nqe.query(_ROUTES, snapshot_id=snapshot_id)]
    total = len({(r["device"], str(r["prefix"])) for r in forward_rows})
    picked = [r for r in forward_rows if (not device or r["device"] == device) and (not prefix or str(r["prefix"]) == prefix)]
    rows = [{k: ("" if v is None else v) for k, v in r.items()}
            for r in sorted(picked, key=lambda r: (str(r["device"]), str(r["prefix"])))]  # r1..r4, each router's routes in prefix order; raw keeps Forward's order
    count = len({(r["device"], str(r["prefix"])) for r in rows})  # an ECMP route has several next hops but is one route
    scope = " and ".join(x for x in (f"on {device}" if device else "", f"for {prefix}" if prefix else "") if x)
    if prefix and rows:
        who = sorted({r["device"] for r in rows})
        summary = f"{prefix} is in the routing table of {plural(len(who), 'device')} ({', '.join(who)}); {_how(rows)}."
    elif prefix:
        summary = f"No device has a route to exactly {prefix}."
    else:
        summary = f"{plural(count, 'route')}{(' ' + scope) if scope else ''}" + (f" (of {total} in the network)." if scope else ".")
    return Table("routes", TOPICS["routes"], summary, ["device", "prefix", "protocol", "kind", "via", "interface"], rows[:MAX_ROWS],
                 truncated=len(rows) > MAX_ROWS, raw=picked)


def _how(rows: list[dict[str, Any]]) -> str:
    kinds = sorted({str(r["protocol"]).lower().replace("_", " ") for r in rows})
    return "learned by " + " and ".join(kinds)


def vulns(client, snapshot_id: str) -> Table:
    """CVEs that match the OS version, and which of them Forward's configuration analysis finds actually affecting each device."""
    response = client.vulnerability_analysis.get_os_vulnerabilities(snapshot_id=snapshot_id)
    found = _dump(response).get("vulnerabilities") or []
    rows = []
    for v in found:
        hit = sorted(r.get("device", "") for r in v.get("device_results") or [] if r.get("vulnerable"))
        config = v.get("depends_on_config")
        rows.append({"cve": v.get("id", ""), "severity": v.get("severity") or "", "cvss v3": v.get("v3_score") or "",
                     "affects": ", ".join(hit) if hit else ("could not be checked" if config is None else "none: configuration not affected"),
                     "published": v.get("published_date") or "", "advisory": v.get("url") or ""})
        rows[-1]["_hit"] = bool(hit)
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    rows.sort(key=lambda r: (not r["_hit"], order.get(str(r["severity"]).upper(), 9), str(r["cve"])))
    affecting = [r for r in rows if r.pop("_hit")]
    devices = sorted({d.strip() for r in affecting for d in r["affects"].split(",")})
    unknown = sum(1 for r in rows if r["affects"] == "could not be checked")
    if not rows:
        summary = "Forward's CVE index lists nothing for the OS on these devices."
    else:
        one = len(rows) == 1
        summary = (f"{plural(len(rows), 'published CVE')} {'matches' if one else 'match'} the OS version; Forward's configuration check finds "
                   f"{len(affecting)} that actually {'affects' if len(affecting) == 1 else 'affect'} these devices")
        summary += f" ({', '.join(devices)})." if affecting else "."
        if unknown:
            summary += f" For {unknown} it could not tell from the configuration."
    note = (f"From Forward's CVE index, built {str(_dump(response, alias=True).get('indexCreatedAt') or _dump(response).get('index_created_at') or '')[:10]}. "
            "Matching the version is not the same as being exposed: Forward reads each device's configuration to decide.")
    return Table("vulns", TOPICS["vulns"], summary, ["cve", "severity", "cvss v3", "affects", "published", "advisory"], rows[:MAX_ROWS], note,
                 truncated=len(rows) > MAX_ROWS, raw=_dump(response, alias=True))


# ---- posture: Forward's own checks ---------------------------------------------------------


def _check_rows(client, snapshot_id: str) -> list[dict[str, Any]]:
    out = []
    for c in client.checks.get_checks(snapshot_id=snapshot_id):
        d = _dump(c, alias=True)
        out.append({"check": d.get("name") or "", "type": (d.get("definition") or {}).get("checkType") or "", "status": d.get("status") or "",
                    "priority": str(d.get("priority") or "").replace("NOT_SET", "").lower(), "what it checks": d.get("description") or ""})
    return out


def checks(client, snapshot_id: str, against: str = "") -> Table:
    """Forward's own checks (predefined, NQE and intent checks) as they stand on one snapshot, and with ``against``, which moved.

    Forward evaluates the checks while it processes a snapshot, so this is a read, not a run. It is Forward's own posture view,
    separate from the workshop's requirements: those are what the gate judges."""
    rows = _check_rows(client, snapshot_id)
    after = {(r["check"], r["type"]): r["status"] for r in _check_rows(client, against)} if against else {}
    for r in rows:
        if against:
            now = after.get((r["check"], r["type"]), "not on that snapshot")
            r["then"] = now
            r["moved"] = "" if now == r["status"] else f"{r['status']} -> {now}"
    order = {"FAIL": 0, "ERROR": 1, "TIMEOUT": 1}
    rows.sort(key=lambda r: (not r.get("moved"), order.get(r["status"], 2), r["check"]))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    tally = ", ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: order.get(kv[0], 2))) or "none"
    moved = [r for r in rows if r.get("moved")]
    if not rows:
        summary = "Forward has no checks on this snapshot."
    elif against:
        summary = f"{plural(len(rows), 'check')} on the first snapshot ({tally}); {plural(len(moved), 'check')} move{'' if len(moved) != 1 else 's'} on the second."
    else:
        summary = f"{plural(len(rows), 'check')} on this snapshot: {tally}."
    cols = ["check", "type", "status"] + (["then", "moved"] if against else []) + ["what it checks"]
    note = ("Forward evaluates these while it processes a snapshot. They are Forward's own checks, a different list from the workshop's "
            "requirements: the requirements are what the gate judges, and these are what Forward would tell you about the network anyway.")
    return Table("checks", TOPICS["checks"], summary, cols, rows[:MAX_ROWS], note, truncated=len(rows) > MAX_ROWS, raw=rows)


# ---- blast radius: from one place, how far can traffic reach? ----------------------------------

DEFAULT_SOURCE = "r1"
DEFAULT_DESTINATION = "10.20.20.0/24"


def _addresses(blocks: str) -> int:
    """How many addresses a Forward header list covers: "10.20.20.1, 10.20.20.2/31, ..." is a comma-separated list of CIDR blocks."""
    import ipaddress

    total = 0
    for block in (b.strip() for b in blocks.split(",") if b.strip()):
        try:
            total += ipaddress.ip_network(block, strict=False).num_addresses
        except ValueError:
            continue
    return total


def blast(client, snapshot_id: str, source: str = "", destination: str = "") -> Table:
    """Forward's blast radius: sample connectivity from one location to a destination subnet.

    ``source`` is a device (``r1``) or one interface (``r1 Ethernet3``). The answer is the packet-header regions that get through, so
    after a change that cuts the service off it is empty: that is the change's blast radius on this path."""
    source, destination = (source or DEFAULT_SOURCE).strip(), (destination or DEFAULT_DESTINATION).strip()
    where = {"type": "InterfaceFilter" if " " in source else "DeviceFilter", "value": source}
    response = client.blast_radius.get_blast_radius(body={"source": where, "dstSubnets": [destination]}, snapshot_id=snapshot_id)
    raw = _dump(response, alias=True)
    if raw.get("invalidSource"):
        raise DataError(f"Forward does not know '{source}' in this snapshot: use a device name (r1) or a device and interface (r1 Ethernet3)")
    rows = []
    for d in raw.get("details") or []:
        headers = d.get("headers") or {}
        to = headers.get("ipv4_dst", "")
        example = ""
        for h in ((d.get("sampleQuery") or {}).get("to") or {}).get("headers") or []:
            example = ", ".join((h.get("values") or {}).get("ipv4_dst") or []) or example
        rows.append({"from": headers.get("ipv4_src") or source, "can reach": to, "addresses": _addresses(to), "for example": example})
    try:
        import ipaddress

        size = ipaddress.ip_network(destination, strict=False).num_addresses
    except ValueError:
        size = 0
    reach = sum(r["addresses"] for r in rows)
    if raw.get("timedOut"):
        summary = f"Forward ran out of time working out what {source} can reach in {destination}."
    elif not rows:
        summary = f"From {source}, Forward finds no packet that can reach any address in {destination}: the whole subnet is cut off from there."
    else:
        of = f" of the {size}" if size else ""
        summary = f"From {source}, traffic can reach {reach}{of} addresses in {destination}."
    note = ("This is reachability, not an application test: it says where packets can get to, using Forward's model of the network. "
            "Run it on the baseline, then on the predicted snapshot, and compare.")
    import json

    body = json.dumps({"source": where, "dstSubnets": [destination]}, indent=2)
    return Table("blast", TOPICS["blast"], summary, ["from", "can reach", "addresses", "for example"], rows[:MAX_ROWS], note,
                 truncated=len(rows) > MAX_ROWS, raw=raw, query=f"client.blast_radius.get_blast_radius(snapshot_id={snapshot_id!r}, body={body})",
                 query_kind="SDK call")


# ---- the before/after question --------------------------------------------------------------


def diff(client, before: str, after: str, device: str = "", prefix: str = "") -> Table:
    """What changed in the routing tables between two snapshots (baseline and predicted, or baseline and the real network)."""
    d = client.snapshot_diff_details
    # Forward computes the route diff when asked and the call returns when it is done, so an empty answer is a real one
    # (the stats call comes first: the per-device and per-prefix reads below use what it computed).
    stats_response = d.get_routing_diff_stats(before_snapshot_id=before, after_snapshot_id=after)
    stats = _dump(stats_response)
    summary_raw = _dump(d.get_routing_diff_summary(before_snapshot_id=before, after_snapshot_id=after), alias=True)
    infos = {i.get("device_name") or i.get("name"): i.get("stats") or {} for i in stats.get("device_infos") or []}
    changed = sorted(n for n, st in infos.items() if any((st.get(k) or 0) for k in ("added", "deleted", "modified")) and (not device or n == device))
    rows: list[dict[str, Any]] = []
    forward_diffs: dict[str, Any] = {}  # what Forward returned for each question, untouched
    if prefix:
        part = d.get_routing_diff_for_prefix(before_snapshot_id=before, after_snapshot_id=after, prefix=prefix)
        forward_diffs[f"prefix {prefix}"] = _dump(part, alias=True)
        rows = [_diff_row(x) for x in (_dump(part).get("diffs") or []) if not device or _dev(x) == device]
    else:
        for name in changed:
            part = d.get_device_routing_diffs(before_snapshot_id=before, after_snapshot_id=after, device_name=name)
            forward_diffs[name] = _dump(part, alias=True)
            rows += [_diff_row(x) for x in _dump(part).get("diffs") or []]
    rows.sort(key=lambda r: (r["prefix"], r["device"]))
    counts = {k: sum(1 for r in rows if r["change"] == k) for k in ("ADDED", "DELETED", "MODIFIED")}
    nothing = not any((st.get(k) or 0) for st in infos.values() for k in ("added", "deleted", "modified"))
    if nothing:
        summary = "Forward reports no routing changes between these two models: this change does not alter any routing table."
    else:
        prefixes = sorted({r["prefix"] for r in rows})
        devs = sorted({r["device"] for r in rows})
        parts = [f"{plural(n, 'route')} {verb}" for n, verb in ((counts["DELETED"], "removed"), (counts["ADDED"], "added"), (counts["MODIFIED"], "changed")) if n]
        where = f" on {', '.join(devs)}" if devs else ""
        what = f" for {', '.join(prefixes[:3])}{' and more' if len(prefixes) > 3 else ''}" if prefixes else ""
        summary = ", ".join(parts) + where + what + "." if parts else "Routes changed, but none match that filter."
    note = f"Snapshot {before} (before) against {after} (after). Forward computes this from the two models; no router is involved."
    return Table("diff", TOPICS["diff"], summary, ["device", "prefix", "change", "before", "after"], rows[:MAX_ROWS], note=note,
                 truncated=len(rows) > MAX_ROWS,
                 raw={"summary": summary_raw, "stats": _dump(stats_response, alias=True), "device_diffs": forward_diffs})


def _dev(item: dict[str, Any]) -> str:
    return str((item.get("a") or item.get("b") or {}).get("deviceName", ""))


def _diff_row(item: dict[str, Any]) -> dict[str, Any]:
    a, b = item.get("a") or {}, item.get("b") or {}
    return {"device": _dev(item), "prefix": a.get("subnet") or b.get("subnet") or "", "change": item.get("diffType", ""),
            "before": _route_text(a), "after": _route_text(b)}


def _route_text(route: dict[str, Any]) -> str:
    if not route:
        return "(no route)"
    via = ", ".join(route.get("nextHopIpAddrs") or []) or "directly attached"
    ports = ", ".join(route.get("outputPorts") or [])
    return f"{route.get('routeType', '').lower()} via {via}" + (f" out {ports}" if ports else "")


def _with_query(table: Table, kind: str, text: str) -> Table:
    table.query, table.query_kind = text, kind
    return table


def ask(client, topic: str, snapshot_id: str, *, against: str = "", device: str = "", prefix: str = "") -> Table:
    """The table for one topic, with the exact query or call behind it attached (so the terminal and the page can show it)."""
    if topic == "inventory":
        return _with_query(inventory(client, snapshot_id), "NQE query", _INVENTORY)
    if topic == "routes":
        return _with_query(routes(client, snapshot_id, device, prefix), "NQE query", _ROUTES)
    if topic == "vulns":
        return _with_query(vulns(client, snapshot_id), "SDK call", f"client.vulnerability_analysis.get_os_vulnerabilities(snapshot_id={snapshot_id!r})")
    if topic == "blast":
        return blast(client, snapshot_id, device, prefix)  # sets its own: the request body
    if topic == "checks":
        return _with_query(checks(client, snapshot_id, against), "SDK call", f"client.checks.get_checks(snapshot_id={snapshot_id!r})")
    if topic == "diff":
        if not against:
            raise DataError("route changes need two snapshots: the baseline and what to compare it with")
        return _with_query(diff(client, snapshot_id, against, device, prefix), "SDK call",
                           f"d = client.snapshot_diff_details\nd.get_routing_diff_stats(before_snapshot_id={snapshot_id!r}, after_snapshot_id={against!r})\n"
                           f"d.get_routing_diff_summary(before_snapshot_id={snapshot_id!r}, after_snapshot_id={against!r})")
    raise DataError(f"unknown topic '{topic}': choose one of {', '.join(TOPICS)}")


def render(table: Table) -> str:
    """A fixed-width text table for the terminal."""
    lines = [table.title, "", table.summary, ""]
    if table.rows:
        cols = table.columns
        widths = [min(max(len(c), *(len(str(r.get(c, ""))) for r in table.rows)), 46) for c in cols]
        lines.append("  ".join(c.upper().ljust(w) for c, w in zip(cols, widths, strict=True)))
        lines += ["  ".join(str(r.get(c, ""))[:46].ljust(w) for c, w in zip(cols, widths, strict=True)).rstrip() for r in table.rows]
    if table.truncated:
        lines.append(f"... showing the first {MAX_ROWS} rows; filter with --device or --prefix")
    if table.note:
        lines += ["", table.note]
    if table.query:
        lines += ["", f"The {table.query_kind or 'query'} behind this:", *(f"  {line}" for line in table.query.strip().splitlines())]
    return "\n".join(lines)
