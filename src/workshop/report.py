"""The terminal's account of a result: what each requirement means, what had to be true, what Forward saw, and what changed.

``baseline``, ``predict`` and ``verify`` used to print one line per requirement ("PASS  APP-EXISTING  DELIVERED/PERMITTED"), which proves
a result and explains nothing. These helpers print the same facts with their meaning, and the routing changes between two networks, so
what people see in the terminal matches the lab page. Display only: nothing here decides a verdict.
"""

from __future__ import annotations

from typing import Any

from workshop import data


def _path(evidence: dict[str, Any] | None) -> str:
    """The first path Forward found, as ``client -> r1 -> r2 -> r4`` (Forward's path search is the evidence for flow and path requirements)."""
    for p in (evidence or {}).get("paths") or []:
        hops = [h for h in p.get("hops") or [] if h]
        if hops:
            return " -> ".join(hops)
    return ""


def requirement_lines(rows: list[dict[str, Any]], *, compare: bool = False) -> list[str]:
    """One block per requirement. ``compare`` shows predicted against collected (verify)."""
    out: list[str] = []
    for r in rows:
        status = r.get("collected") if compare else r.get("status")
        out.append(f"  {status!s:<5} {r.get('id', '')}: {r.get('title', '')}")
        if r.get("expected"):
            out.append(f"        must be:  {r['expected']}")
        saw = str(r.get("observed", ""))
        rows_seen = (r.get("evidence") or {}).get("rows")
        path = _path(r.get("evidence"))
        if isinstance(rows_seen, list) and "row(s) from" in str(r.get("expected", "")):  # an NQE requirement: show its rows, not a JSON blob
            out.append(f"        Forward saw: {len(rows_seen)} row(s)" + (":" if rows_seen else ""))
            for row in rows_seen[:5]:
                out.append("          - " + "  ".join(str(v) for v in dict(row).values() if v not in (None, ""))[:130])
            if len(rows_seen) > 5:
                out.append(f"          ... and {len(rows_seen) - 5} more")
        else:
            out.append(f"        Forward saw: {saw[:150]}" + (f"   (path {path})" if path and path not in saw else ""))
        if compare:
            same = r.get("predicted") == r.get("collected")
            out.append(f"        predicted {r.get('predicted')}, collected {r.get('collected')}: " + ("as predicted" if same else "DIFFERENT from the prediction"))
    return out


def routing_table(client, before: str, after: str):
    """Forward's routing diff as a ``data.Table``, or None when Forward cannot answer just now (display only)."""
    try:
        return data.diff(client, before, after)
    except Exception:  # noqa: BLE001 - display only; never a reason to change a verdict
        return None


def routing_lines(table, *, limit: int = 8) -> list[str]:
    """What the change did to the routing tables, from Forward's two models."""
    if table is None:
        return ["  (the routing changes are not available just now: run `workshop data diff` in a moment)"]
    out = [f"  {table.summary}"]
    for row in table.rows[:limit]:
        out.append(f"    {row.get('device') or ''!s:<4} {row.get('prefix') or ''!s:<16} {row.get('change') or ''!s:<8} was {row.get('before')}  now {row.get('after')}")
    if len(table.rows) > limit:
        out.append(f"    ... and {len(table.rows) - limit} more: workshop data diff")
    return out


def inventory_line(client, snapshot_id: str) -> str:
    try:
        return "  " + data.inventory(client, snapshot_id).summary
    except Exception as exc:  # noqa: BLE001 - display only
        return f"  (inventory not available just now: {type(exc).__name__})"


def baseline_close(status: str) -> list[str]:
    if status == "PASS":
        return ["", "What this means: every requirement holds on the network today. This snapshot is the starting point every",
                "prediction is compared with. Next: change candidate/r4-bgp.eos, then `workshop predict` (or open a pull request)."]
    if status == "FAIL":
        return ["", "What this means: the network does not meet its own baseline requirements, so a prediction would prove nothing.",
                "Fix what is listed above (or `workshop restore`, then `workshop baseline`) before you predict."]
    return ["", f"What this means: the baseline ended {status}, so Forward could not fully judge the network (see the lines above).",
            "That is not the same as a failure: run `workshop baseline` again, and ask a proctor if it repeats."]


def predict_close(status: str, failing: list[str]) -> list[str]:
    """``failing`` are the requirements that FAILED on the predicted network (not ones Forward could not judge)."""
    if status == "PASS":
        return ["", "What this means: on the network that would result, every requirement still holds. Nothing was sent to any router."]
    if status == "FAIL" and failing:
        return ["", f"What this means: on the network that would result, {len(failing)} requirement(s) break ({', '.join(failing)}).",
                "Nothing was deployed and no router was touched. Read the routing changes above, fix candidate/r4-bgp.eos and predict again."]
    again = {"STALE": "The network or the baseline moved on since the prediction was made: run `workshop baseline`, then predict again.",
             "INCONCLUSIVE": "Forward could not finish judging in time. That is not a failure of your change: predict again.",
             "ERROR": "Something stopped the prediction before it could judge your change (the reason is on the first line): fix that, then predict again."}
    return ["", f"What this means: the prediction ended {status}, so nothing was judged about your change and deployment is blocked.",
            again.get(status, "The reason is on the first line.")]


def verify_close(outcome: str, rows: list[dict[str, Any]]) -> list[str]:
    if outcome == "MATCH":
        count = f"all {len(rows)} requirement{'s' if len(rows) != 1 else ''}" if len(rows) != 1 else "the 1 requirement"
        return ["", f"What this means: {count} came out on the real network exactly as Predict said, and the real flows",
                "agree. What was predicted is what happened. That is the loop closed."]
    differing = [r["id"] for r in rows if r.get("predicted") != r.get("collected")]
    return ["", f"What this means: the real network does not match the prediction ({', '.join(differing) or 'a live probe disagreed'}).",
            "The model and the network disagree, which is the case this method exists to surface. Investigate, and `workshop restore` if it matters."]


def tagline(kind: str, outcome: str, failing: int = 0) -> list[str]:
    """One light line under a result. Never replaces the explanation above it, and says nothing for outcomes that are not good news
    or bad news in the plain sense (STALE, ERROR, INCONCLUSIVE): those need the words, not a joke."""
    lines = {
        ("baseline", "PASS"): "Clean slate. Now go break something, on paper.",
        ("predict", "PASS"): "Green light from the model. Now a human gets to say yes.",
        ("predict", "FAIL"): "Caught before it touched a router. That is the whole point.",
        ("verify", "MATCH"): "Predicted, deployed, verified. No surprises.",
        ("verify", "MISMATCH"): "Surprises are exactly what verify is for.",
    }
    line = lines.get((kind, outcome))
    return ["", line] if line and (kind != "predict" or outcome != "FAIL" or failing) else []


WAITING = (
    "Good time to refill your coffee.",
    "Somewhere a very patient computer is doing real work. Hang tight.",
    "Slow here is the point: a minute of checking beats an outage.",
    "Routers, models and CI all take a moment. That is the job.",
    "Predict never guesses. It computes, and computing takes a moment.",
)


def yolo_lines() -> list[str]:
    """`workshop yolo`: deploys nothing, ever, and says what the real deploy needs."""
    return ["NOT DEPLOYED (ERROR): no prediction, no deploy.", "",
            "YOLO is not a change process. `workshop deploy` goes only when all of these hold:",
            "  1. Predict judged this exact candidate PASS, with a baseline that is still fresh.",
            "  2. A person said yes: the pull request is merged and its forward/predict check is green.",
            "  3. You typed the candidate's 7-character hash with --confirm.",
            "  4. No router changed since the baseline (the drift check).",
            "", "Start here: `workshop status` tells you the next step."]


def matrix_notes(rows, titles: dict[str, str]) -> list[str]:
    """Under the matrix grid: what each column is, and for every combination why it passed or failed."""
    out = ["", "Columns (the first word of each requirement):"]
    out += [f"  {i.split('-')[0]:<8} {i}: {t}" for i, t in titles.items()]
    out += ["", "Reading it:"]
    for r in rows:
        broke = [i for i, status in r.results.items() if status not in ("PASS", None)]
        if r.status == "PASS":
            out.append(f"  retire {r.label}: every requirement holds")
        elif broke:
            out.append(f"  retire {r.label}: {r.status}: breaks {', '.join(broke)}")
        else:
            out.append(f"  retire {r.label}: not judged ({r.status}): Forward did not get as far as the requirements; run `workshop baseline` and try again")
    safe = [r.label for r in rows if r.status == "PASS"]
    out += ["", ("Safe to propose: " + "; ".join(safe)) if safe else "No combination passes every requirement.",
            "Change nothing yet: each line is a prediction only. Take one that passes into candidate/r4-bgp.eos."]
    return out


# ---- the smaller commands ---------------------------------------------------------------------

_EXPECT = {8080: "open (the application)", 8443: "blocked (r1's ACL)", 2222: "blocked (r1's ACL: management-like traffic)"}


def probe_lines(results: list[tuple[int, bool, bool]], service_ip: str) -> list[str]:
    """``results``: (port, reachable, listener_up) from the client. Says what each should be and what it means."""
    out = [f"From the client (10.10.10.10) to the service ({service_ip}), on the real network:"]
    wrong = []
    for port, reachable, listener_up in results:
        state = "open" if reachable else ("blocked" if listener_up else "no answer, and the service is not listening")
        want = _EXPECT.get(port, "")
        good = (reachable and want.startswith("open")) or (not reachable and listener_up and want.startswith("blocked")) or not want
        out.append(f"  TCP {port:<5} {state:<12} should be {want or 'n/a'}" + ("" if good else "   <-- not as expected"))
        if not good:
            wrong.append(port)
    out.append("")
    out.append("What this means: " + ("the live network behaves as expected: the application works and the blocked ports are still blocked."
                                     if not wrong else f"TCP {', '.join(map(str, wrong))} is not as expected. This is the real network, not a prediction."))
    return out


def status_lines(baseline: dict[str, Any] | None, deploy: dict[str, Any] | None, verify: dict[str, Any] | None, candidate_changes: int) -> list[str]:
    """Where this lab is in the loop, in words, with the NAF Framework block each step belongs to."""
    out = ["Where your lab is in the loop:"]
    if baseline:
        report = (baseline.get("report") or {}).get("status", "?")
        out.append(f"  Baseline  done      snapshot {baseline.get('snapshot')}, requirements {report}   [Collector + Observability]")
    else:
        out.append("  Baseline  not yet   run `workshop baseline`   [Collector + Observability]")
    out.append(f"  Change    {'written' if candidate_changes else 'empty':<9} candidate/r4-bgp.eos has {candidate_changes} change line(s)   [Intent]")
    if deploy:
        out.append(f"  Deploy    done      {max(len(deploy.get('lines', [])) - 2, 0)} change(s) applied to r4 at {str(deploy.get('finished_at', ''))[11:19]} UTC   [Executor]")
    else:
        out.append("  Deploy    not yet   needs a merged pull request and `--confirm <sha7>`   [Executor]")
    if verify:
        out.append(f"  Verify    {verify.get('outcome', '?'):<9} post-change snapshot {verify.get('post_snapshot_id', '-')}   [Collector + Observability]")
    else:
        out.append("  Verify    not yet   run `workshop verify` after a deploy   [Collector + Observability]")
    nxt = ("workshop baseline" if not baseline else "edit candidate/r4-bgp.eos, then `workshop predict` or open a pull request" if not candidate_changes
           else "predict it (a pull request, or `workshop predict`)" if not deploy else "workshop verify" if not verify or verify.get("outcome") == "INCOMPLETE"
           else "restore and go again: `workshop restore && workshop baseline`")
    return out + ["", f"Next: {nxt}", "(the bracketed names are NAF Framework blocks: docs/exercises/framework.md)"]


def up_close() -> list[str]:
    return ["", "What this means: netlab built the six nodes (four cEOS routers, a client and a service) and NAPALM wrote each router's",
            "baseline configuration. Look at it: `containerlab inspect --all`, or `docker exec -it clab-autocon6-r1 Cli` (then `enable`,",
            "`show ip bgp summary`). Next: `workshop baseline` gives Forward a model of this network."]
