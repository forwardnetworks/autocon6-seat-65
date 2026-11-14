"""The change record: three reports, written as markdown and attached to the ticket (the pull request), like a real change package.

1. **Change analysis** (``predict check``): what Predict found, section by section, the way Forward's Predict analysis page shows it: the
   change, the requirements, routing, reachability per flow, Forward's checks, everything else Predict compared, and Forward AI's reading
   of it, clearly marked as advisory. Written to ``evidence/change-analysis.md`` (the pull request check's artifact) and added to the
   check's summary page.
2. **Pre-change readiness** (``deploy gates``): the baseline, the test result, the approval, every gate that held, any caveats accepted,
   and the backout plan. Posted on the pull request when there is one.
3. **Post-change report** (``verify compare``, and ``revert compare`` for a backout): what was sent, what the router reads back, the live
   probes, Forward's fresh collection judged against the prediction, and the routing diff from the baseline. Posted on the pull request.

Every section says where its data came from: the SDK call, and the part of Forward that shows the same thing. Display only: a report that
cannot be written never changes a verdict, a gate or an exit code.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MARK = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "ERROR": "⚠️ ERROR", "INCONCLUSIVE": "❔ INCONCLUSIVE", "STALE": "⏳ STALE",
        "MATCH": "✅ MATCH", "MISMATCH": "❌ MISMATCH"}


def src(where: str, call: str) -> str:
    """The provenance line under a section: the part of Forward and the SDK call, or, for data that is not Forward's, where it came from."""
    if where.startswith("not Forward: "):
        return f"<sub>📍 Not from Forward: {where[len('not Forward: '):]} · <code>{call}</code></sub>"
    return f"<sub>📍 In Forward: {where} · SDK: <code>{call}</code></sub>"


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def _dump(x: Any) -> Any:
    return x.model_dump(mode="json", by_alias=True, exclude_none=True) if hasattr(x, "model_dump") else x


def _try(log: Callable[[str], None] | None, what: str, fn: Callable[[], Any]) -> tuple[Any, str]:
    """(answer, "") or (None, a one-line reason): one missing section never stops the report."""
    try:
        return fn(), ""
    except Exception as exc:  # noqa: BLE001 - display only
        reason = f"{type(exc).__name__}: {str(exc)[:160]}"
        if "is off for your organization" in str(exc):
            reason = "this analysis is not enabled on this Forward organization"
        if log:
            log(f"  (report: {what} unavailable: {reason})")
        return None, reason


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


# ---- 1. change analysis ---------------------------------------------------------------------


def analysis(client, m, *, baseline_results: list[dict[str, Any]] | None = None, ai: bool = True, log=None) -> str:
    """The Predict analysis page, as markdown. ``client`` is a Forward SDK client; ``m`` the prediction's evidence manifest."""
    b, a = str(m.baseline_snapshot_id), str(m.predicted_snapshot_id)
    sd = client.snapshot_diff_details
    change = [line.strip() for line in (m.candidate_text or "").splitlines() if "network" in line]
    out = [f"# Change analysis: {MARK.get(m.status, m.status)}", "",
           (f"Candidate `{m.candidate_path}` (sha256 `{m.candidate_sha256[:12]}`) on lab `{m.lab_id}`, predicted against baseline snapshot `{b}`. "
            f"Predicted snapshot `{a}`, change set `{m.change_set_id}`. Generated {_now()}."), "",
           "> This is what Predict shows on its analysis page, collected through the SDK. Every section says where in Forward it came from.",
           "> Nothing here touched a router: it is all Forward's model of the network with the change applied.", ""]

    out += ["## 1 · The change", "", "```", *change, "```", "",
            src("Predict › change set (its commands for r4)", f"change_sets (id {m.change_set_id})"), ""]

    out += ["## 2 · The requirements (what the gate judges)", "",
            f"Overall: **{MARK.get(m.status, m.status)}**. {m.reason}".rstrip(), "",
            "| Requirement | Baseline | Predicted | What Forward observed on the predicted network |", "|---|---|---|---|"]
    before = {str(r.get("id")): str(r.get("status")) for r in baseline_results or []}
    for r in m.results or []:
        out.append(f"| **{_cell(r['id'])}** {_cell(r.get('title', ''))} | {MARK.get(before.get(r['id'], ''), before.get(r['id'], '·'))} | "
                   f"{MARK.get(r['status'], r['status'])} | {_cell(r.get('observed', ''))} |")
    out += ["", src("Predict › path analysis and NQE, judged by the workshop's evaluate.py against requirements/candidate.yml",
                    "path_search.get_paths · nqe.query"), ""]

    summary, why = _try(log, "routing summary", lambda: _dump(sd.get_routing_diff_summary(before_snapshot_id=b, after_snapshot_id=a)))
    prefixes, _ = _try(log, "routing prefixes", lambda: _dump(sd.get_routing_diff_prefixes(before_snapshot_id=b, after_snapshot_id=a)))
    out += ["## 3 · Routing", ""]
    stats = (summary or {}).get("diffStats") or []
    if summary is None:
        out += [f"Not available: {why}", ""]
    elif not stats:
        out += ["No route changes anywhere: every device's routing table is the same before and after.", ""]
    else:
        out += [f"{summary.get('uniquePrefixesCount', '?')} prefix(es) change on {summary.get('uniqueDevicesCount', '?')} device(s): "
                + "; ".join(f"{s.get('routesCount')} route(s) {str(s.get('type')).lower()} on {s.get('devicesCount')} device(s)" for s in stats) + ".", "",
                "| Prefix | Device | Change | Before (next hop) | After (next hop) |", "|---|---|---|---|---|"]
        for p in ((prefixes or {}).get("prefixStats") or [])[:20]:
            detail, _ = _try(log, "routing detail", lambda p=p: _dump(sd.get_routing_diff_for_prefix(before_snapshot_id=b, after_snapshot_id=a, prefix=p["subnetAddress"])))
            for d in (detail or {}).get("diffs") or []:
                route = d.get("a") or d.get("b") or {}
                hop = lambda x: (", ".join(x.get("nextHopIpAddrs") or []) or ", ".join(x.get("outputPorts") or []) or x.get("routeType", "")) if x else "(no route)"
                out.append(f"| `{p['subnetAddress']}` | {_cell(route.get('deviceName'))} | {_cell(str(d.get('diffType')).lower())} | "
                           f"{_cell(hop(d.get('a')))} | {_cell(hop(d.get('b')))} |")
        out.append("")
    out += [src("Predict › routing (the route diff)", "snapshot_diff_details.get_routing_diff_summary · get_routing_diff_prefixes · get_routing_diff_for_prefix"), ""]

    out += ["## 4 · Reachability, flow by flow", "",
            "Each requirement that is a flow, as Forward traced it on the baseline and on the predicted network.", "",
            "| Flow requirement | Must be | Baseline | Predicted |", "|---|---|---|---|"]
    base_obs = {str(r.get("id")): r for r in baseline_results or []}
    for r in m.results or []:
        if "path search" not in str(r.get("evidence", "")) and "TCP" not in str(r.get("expected", "")):
            continue
        bo = base_obs.get(r["id"], {})
        out.append(f"| **{_cell(r['id'])}** | {_cell(r.get('expected', ''))} | {_cell(bo.get('observed', '·'))} | {_cell(r.get('observed', ''))} |")
    pairs, why = _try(None, "location connectivity diff", lambda: _dump(sd.get_connectivity_diff_location_pairs(before_snapshot_id=b, after_snapshot_id=a, type="ALL")))
    out += ["", "Forward's location-to-location connectivity diff: " + ("see the raw answer" if pairs else f"not shown ({why})."), "",
            src("Predict › paths (and Reachability, where enabled)", "path_search.get_paths · snapshot_diff_details.get_connectivity_diff_location_pairs"), ""]

    checks, why = _try(log, "check diffs", lambda: _dump(sd.get_check_diffs(before_snapshot_id=b, after_snapshot_id=a,
                                                                            type=["NQE", "Predefined", "Isolation", "Reachability", "Existential"])))
    out += ["## 5 · Forward's own checks", ""]
    moved = []
    for c in (checks or {}).get("checks") or []:
        x, y = c.get("a") or {}, c.get("b") or {}
        if x.get("status") != y.get("status"):
            moved.append((x.get("name") or y.get("name"), x.get("status", "·"), y.get("status", "·")))
    total = len((checks or {}).get("checks") or [])
    if checks is None:
        out += [f"Not available: {why}", ""]
    elif not moved:
        out += [f"{total} check(s) compared; none changes status with this change. (Forward's checks are not the gate: the requirements above are.)", ""]
    else:
        out += ["| Check | Before | After |", "|---|---|---|"] + [f"| {_cell(n)} | {MARK.get(s0, s0)} | {MARK.get(s1, s1)} |" for n, s0, s1 in moved] + [""]
    out += [src("Predict › checks (intent, NQE and predefined)", "snapshot_diff_details.get_check_diffs"), ""]

    out += ["## 6 · Everything else Predict compared", "", "| What | Result |", "|---|---|"]
    files, _ = _try(log, "file diffs", lambda: _dump(sd.get_file_diff_count(before_snapshot_id=b, after_snapshot_id=a)))
    if files is not None:
        kinds = ", ".join(f"{n} {k.lower()}" for k, n in (files.get("types") or {}).items())
        out.append(f"| Collected files that differ | {files.get('count', 0)} ({kinds or 'none'}) |")
    for label, fn in (("ACLs", "get_acl_diff_stats"), ("Interfaces", "get_interface_diff_stats"), ("NAT", "get_nat_diff_stats")):
        got, why = _try(None, label, lambda fn=fn: _dump(getattr(sd, fn)(before_snapshot_id=b, after_snapshot_id=a)))
        devs = (got or {}).get("deviceInfos") or []
        out.append(f"| {label} | " + (f"not available ({why})" if got is None else (f"changes on {len(devs)} device(s)" if devs else "no change")) + " |")
    topo, why = _try(None, "topology", lambda: _dump(sd.get_topology_diffs(before_snapshot_id=b, after_snapshot_id=a)))
    if topo is not None:
        n = len(topo.get("ADDED") or []) + len(topo.get("DELETED") or [])
        out.append(f"| Topology (links) | {'no change' if not n else f'{n} link(s) added or removed'} |")
    out += ["", src("Predict › diffs (files, ACLs, interfaces, NAT, topology)",
                    "snapshot_diff_details.get_file_diff_count · get_acl_diff_stats · get_interface_diff_stats · get_nat_diff_stats · get_topology_diffs"), ""]

    out += ["## 7 · Forward AI's reading of the change (advisory)", ""]
    if not ai:
        out += ["Not requested (`--no-ai`).", ""]
    else:
        pa = client.predict_assist
        overview, _ = _try(log, "AI overview", lambda: _dump(pa.assist_change_set_overview(change_set_id=m.change_set_id)))
        parts = [("What the change is", overview and (f"**{overview.get('name', '')}.** {overview.get('description', '')}")),
                 ("Configuration", (_try(log, "AI config summary", lambda: _dump(pa.assist_config_diff_summary(before_snapshot_id=b, after_snapshot_id=a)))[0] or {}).get("summary")),
                 ("Impact", (_try(log, "AI impact summary", lambda: _dump(pa.assist_impact_diff_summary(before_snapshot_id=b, after_snapshot_id=a)))[0] or {}).get("summary")),
                 ("Failures and regressions", (_try(log, "AI failure summary", lambda: _dump(pa.assist_failure_diff_summary(before_snapshot_id=b, after_snapshot_id=a)))[0] or {}).get("summary"))]
        for title, text in parts:
            out += [f"**{title}.** {text}" if text else f"**{title}.** (no answer)", ""]
        out += ["> ⚠️ AI-generated. It advises; the requirements in section 2 decide. Read it against the sections above: does it agree with the routing",
                "> table and the requirement results? (It summarizes Forward's diffs; it does not run your requirements.)", "",
                src("Predict › AI summaries (Forward AI)", "predict_assist.assist_change_set_overview · assist_config_diff_summary · assist_impact_diff_summary · assist_failure_diff_summary"), ""]

    out += ["## Where this came from", "", "| Section | Forward | SDK |", "|---|---|---|",
            "| The change | Predict › change set | `change_sets` |",
            "| Requirements | Predict › paths and NQE, judged by evaluate.py | `path_search.get_paths`, `nqe.query` |",
            "| Routing | Predict › routing diff | `snapshot_diff_details.get_routing_diff_*` |",
            "| Reachability | Predict › paths | `path_search.get_paths` |",
            "| Checks | Predict › checks | `snapshot_diff_details.get_check_diffs` |",
            "| Other diffs | Predict › diffs | `snapshot_diff_details.get_*_diff_stats` |",
            "| AI reading | Forward AI | `predict_assist.*` |", ""]
    return "\n".join(out)


# ---- 2. pre-change readiness ----------------------------------------------------------------


def pre_change(m, gates_passed: list[str], plan: dict[str, Any] | None, *, caveats: list[str] | None = None, pr: int | None = None) -> str:
    change = [line.strip() for line in (m.candidate_text or "").splitlines() if "network" in line]
    out = [f"# Pre-change readiness: {'⚠️ READY WITH CAVEATS' if caveats else '✅ READY'}", "",
           f"Lab `{m.lab_id}` · candidate sha256 `{m.candidate_sha256[:12]}` · {_now()}", "",
           "## Readiness: the baseline", "",
           f"Baseline snapshot `{m.baseline_snapshot_id}` ({MARK.get(m.baseline_status, m.baseline_status)}), configs fingerprint `{m.baseline_fingerprint[:12]}`.",
           "", src("Snapshots (a headless-collector collection) and the baseline requirements", "snapshots.upload · path_search.get_paths · nqe.query"), "",
           "## Testing: the prediction", "", f"Predict: **{MARK.get(m.status, m.status)}** on predicted snapshot `{m.predicted_snapshot_id}`.", "",
           "| Requirement | Predicted |", "|---|---|", *[f"| {_cell(r['id'])} | {MARK.get(r['status'], r['status'])} |" for r in m.results or []], ""]
    if caveats:
        out += [f"**Accepted with caveats:** {', '.join(caveats)}. Predict says these will break; the change goes ahead on purpose and is recorded as such.", ""]
    out += ["## Approval", ""]
    out += [f"Pull request #{pr}, merged with its forward/predict check green on the predicted commit." if pr else
            "No pull request: a local rehearsal (`--evidence`). In production this line is the CAB approval.", "",
            "## Gates that held, at the moment of this record", "", *[f"- ✓ {g}" for g in gates_passed], "",
            "## The change", "", "```", *change, "```", "",
            "## Backout plan", ""]
    if plan:
        inv = [line for line in plan["lines"] if "network" in line]
        out += ["`workshop revert plan`, then `workshop revert apply --confirm <hash>`: the exact inverse, checked, then a fresh collection and `workshop revert compare`.", "",
                "```", *inv, "```", "", f"Returns the network to fingerprint `{str(plan.get('target_fingerprint', ''))[:12]}`, the baseline above."]
    else:
        out += ["Before the deploy, the backout is the inverse of the change above, against the baseline above: `workshop revert` once deployed, or `workshop restore` (break-glass)."]
    out += ["", "<sub>Produced by `workshop deploy gates`. Nothing has been sent to a router yet.</sub>", ""]
    return "\n".join(out)


# ---- 3. post-change -------------------------------------------------------------------------


def post_change(record: dict[str, Any], result: dict[str, Any], *, routing=None, pr: int | None = None, backout: dict[str, Any] | None = None) -> str:
    outcome = result.get("outcome", "?")
    lines = [line for line in record.get("lines", []) if "network" in line]
    out = [f"# Post-change report: {MARK.get(outcome, outcome)}", "",
           f"Deployed {record.get('finished_at', '')} · candidate sha256 `{str(record.get('candidate_sha256', ''))[:12]}`"
           + (f" · pull request #{pr}" if pr else " · a local rehearsal") + f" · {_now()}", ""]
    if record.get("caveats"):
        out += [f"**Deployed with caveats:** {', '.join(record['caveats'])} (Predict said FAIL).", ""]
    out += ["## What was sent", "", "```", *lines, "```", "", "What r4 computed for the session and committed:", "", "```",
            *(record.get("device_diff") or "(no difference)").splitlines(), "```", "",
            src("not Forward: the router itself, over NAPALM (eAPI config session)", "load_merge_candidate · compare_config · commit_config"), "",
            "## Read back from the router", "",
            "✓ r4 shows every line that was sent." if not record.get("readback_problems") else "✗ " + "; ".join(record["readback_problems"]), "",
            "## Live probes (real packets from the client)", "", "| Port | Expected | Observed |", "|---|---|---|"]
    for p in result.get("probes") or []:
        out.append(f"| TCP {p['port']} | {p['expected']} | {'✓' if p['ok'] else '✗'} {p['observed']} |")
    out += ["", src("not Forward: the client container, over the Docker SDK", "exec_run(['nc', '-z', …])"), "",
            f"## Forward's fresh collection, judged against the prediction: {MARK.get(outcome, outcome)}", "",
            f"Post-change snapshot `{result.get('post_snapshot_id', '')}`.", "",
            "| Requirement | Predicted | Collected |  |", "|---|---|---|---|"]
    for r in result.get("rows") or []:
        same = r.get("predicted") == r.get("collected")
        out.append(f"| {_cell(r['id'])} | {MARK.get(r['predicted'], r['predicted'])} | {MARK.get(r['collected'], r['collected'])} | {'as predicted' if same else '**differs**'} |")
    out += ["", src("Snapshots (a headless-collector collection), then the same path searches and NQE as the prediction", "snapshots.upload · path_search.get_paths · nqe.query"), ""]
    if routing is not None:
        out += ["## What changed in the routing tables (baseline → now)", "", _cell(routing.summary), ""]
        if routing.rows:
            out += ["| Device | Prefix | Change |", "|---|---|---|", *[f"| {_cell(r['device'])} | `{_cell(r['prefix'])}` | {_cell(r['change'])} |" for r in routing.rows[:30]], ""]
        out += [src("Diffs › routing", "snapshot_diff_details.get_routing_diff_stats · get_routing_diff_summary"), ""]
    out += ["## Backout", ""]
    if backout:
        v = backout.get("verified") or {}
        out += [f"Backed out {backout.get('at', '')}: the inverse lines, confirmed `{str(backout.get('sha256', ''))[:7]}`; configs back to the baseline: "
                + ("✓" if backout.get("back_to_baseline") else "✗") + ".",
                f"Forward's confirmation after a fresh collection: {MARK.get(v.get('outcome', ''), v.get('outcome', 'not run yet (`workshop revert compare`)'))}"
                + (f" on snapshot `{v.get('snapshot')}`" if v.get("snapshot") else "") + ".", ""]
    else:
        out += ["Not needed: the change stands. If it were: `workshop revert plan`, `workshop revert apply`, a fresh collection, `workshop revert compare`.", ""]
    out += ["<sub>Produced by `workshop verify compare` (and `workshop revert compare` for a backout).</sub>", ""]
    return "\n".join(out)


# ---- the backout ------------------------------------------------------------------------------


def backout(record: dict[str, Any]) -> str:
    v = record.get("verified") or {}
    outcome = v.get("outcome", "not confirmed yet")
    sent = [line for line in record.get("lines", []) if "network" in line]
    out = [f"# Backout: {MARK.get(outcome, outcome)}", "", f"Backed out {record.get('at', '')} · confirmed `{str(record.get('sha256', ''))[:7]}` · {_now()}", ""]
    if record.get("caveats"):
        out += [f"The change had gone out with caveats ({', '.join(record['caveats'])}); this backout puts back the state Predict said was safe.", ""]
    out += ["## What was sent: the exact inverse of the deploy", "", "```", *sent, "```", "",
            "## The configs, read back over NAPALM", "",
            ("✓ All four routers hash to the original baseline's fingerprint, byte for byte." if record.get("back_to_baseline")
             else "✗ The configs do not match the original baseline."), "",
            src("not Forward: the routers' running configs over NAPALM, hashed", "cli('show running-config')"), "",
            f"## Forward's confirmation after a fresh collection: {MARK.get(outcome, outcome)}", ""]
    if v:
        head = (f"Snapshot `{v.get('snapshot')}` against the original baseline `{v.get('original')}`: baseline requirements "
                f"{MARK.get(v.get('requirements'), v.get('requirements'))}, {v.get('routing_changes', '?')} routing difference(s).")
        out += [head, *[f"- {r}" for r in v.get("reasons") or []], "",
                src("Snapshots (a headless-collector collection), the baseline requirements, and the routing diff",
                    "snapshots.upload · path_search.get_paths · nqe.query · snapshot_diff_details.get_routing_diff_stats"), ""]
    out += ["<sub>Produced by `workshop revert compare`.</sub>", ""]
    return "\n".join(out)


# ---- writing and attaching ------------------------------------------------------------------


def save(settings, name: str, text: str) -> Path:
    d = settings.state_dir / "reports"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text(text)
    return path


def attach(pr: int | None, title: str, text: str, log) -> bool:
    """Post the report on the pull request (the ticket). False, with a line saying why, if it cannot: the report is still on disk."""
    if not pr:
        return False
    try:
        from workshop import github

        github.comment(pr, f"<details open><summary><b>{title}</b></summary>\n\n{text}\n</details>")
        log(f"  → attached to pull request #{pr} as a comment ({title})")
        return True
    except Exception as exc:  # noqa: BLE001 - display only
        log(f"  (could not attach {title} to pull request #{pr}: {type(exc).__name__}: {str(exc)[:120]}; it is saved locally)")
        return False


def baseline_results(settings) -> list[dict[str, Any]]:
    try:
        return (json.loads((settings.state_dir / "baseline.json").read_text()).get("report") or {}).get("results") or []
    except (OSError, ValueError):
        return []
