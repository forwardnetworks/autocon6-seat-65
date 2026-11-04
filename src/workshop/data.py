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
    "vulns": "CVEs: published vulnerabilities that apply to this OS",
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
        data = read(settings.state_dir / "baseline.json")
        if data.get("snapshot"):
            return str(data["snapshot"])
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
    rows = [dict(r) for r in forward_rows]
    for r in rows:
        support = r.pop("support", None)
        r["end of support"] = _support_text(support)
    kinds = sorted({f"{r['vendor']} {r['model']} on {r['version']}".replace("ARISTA", "Arista") for r in rows})
    summary = f"{plural(len(rows), 'device')}: " + "; ".join(kinds) + "." if rows else "Forward found no devices in this snapshot."
    no_lifecycle = all(r["end of support"] == "no data" for r in rows)
    note = ("Forward has no end-of-support dates for these devices: they are lab images, not hardware or a released OS in "
            "Forward's lifecycle table. On a real network this column lists the OS maintenance and support dates.") if rows and no_lifecycle else ""
    return Table("inventory", TOPICS["inventory"], summary, ["name", "vendor", "model", "os", "version", "end of support"], rows, note, raw=forward_rows)


def _support_text(support: Any) -> str:
    if not support:
        return "no data"
    dates = [f"{k}: {v}" for k, v in dict(support).items() if v and "Date" in k]
    return "; ".join(dates) or "no data"


def routes(client, snapshot_id: str, device: str = "", prefix: str = "") -> Table:
    forward_rows = [dict(r) for r in client.nqe.query(_ROUTES, snapshot_id=snapshot_id)]
    total = len({(r["device"], str(r["prefix"])) for r in forward_rows})
    picked = [r for r in forward_rows if (not device or r["device"] == device) and (not prefix or str(r["prefix"]) == prefix)]
    rows = [{k: ("" if v is None else v) for k, v in r.items()} for r in picked]
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
    data = _dump(client.vulnerability_analysis.get_vulnerabilities(snapshot_id=snapshot_id), alias=True)
    found = data.get("vulnerabilities") or []
    found = [{**v, "os_infos": v.get("os_infos") or v.get("osInfos")} for v in found]  # either spelling of the field
    rows = []
    for v in found:
        info = (v.get("os_infos") or [{}])[0]
        rows.append({"cve": v.get("id", ""), "severity": info.get("severity") or "", "cvss v3": info.get("v3_score") or "",
                     "exploited (CISA KEV)": "yes" if v.get("has_cisa_kev_entry") else "",
                     "depends on config": "yes" if info.get("config_dependent") else "no", "advisory": info.get("url") or ""})
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    rows.sort(key=lambda r: (order.get(str(r["severity"]).upper(), 9), str(r["cve"])))
    crit = sum(1 for r in rows if str(r["severity"]).upper() in ("CRITICAL", "HIGH"))
    kev = sum(1 for r in rows if r["exploited (CISA KEV)"])
    cfg = sum(1 for r in rows if r["depends on config"] == "yes")
    summary = (f"{plural(len(rows), 'published CVE')} apply to the OS on these devices: {crit} critical or high, {kev} on CISA's exploited list, "
               f"{cfg} that only matter with certain configuration." if rows else "Forward's CVE index lists nothing for the OS on these devices.")
    note = f"From Forward's CVE index, built {str(data.get('index_created_at') or data.get('indexCreatedAt') or '')[:10]}. A real network would show affected devices per CVE."
    return Table("vulns", TOPICS["vulns"], summary, ["cve", "severity", "cvss v3", "exploited (CISA KEV)", "depends on config", "advisory"],
                 rows[:MAX_ROWS], note, truncated=len(rows) > MAX_ROWS, raw=found)


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


def ask(client, topic: str, snapshot_id: str, *, against: str = "", device: str = "", prefix: str = "") -> Table:
    if topic == "inventory":
        return inventory(client, snapshot_id)
    if topic == "routes":
        return routes(client, snapshot_id, device, prefix)
    if topic == "vulns":
        return vulns(client, snapshot_id)
    if topic == "diff":
        if not against:
            raise DataError("route changes need two snapshots: the baseline and what to compare it with")
        return diff(client, snapshot_id, against, device, prefix)
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
    return "\n".join(lines)
