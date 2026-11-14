"""``workshop revert``: back out exactly what was deployed, to a state there is already evidence for.

A backout is a change too, so it is held to the same standard as a deploy, but its target is not a guess: it is the baseline the deployed change
was predicted against, which ``workshop baseline check`` already judged PASS. So the revert

1. sends the exact inverse of the lines the deploy record says were sent (``no network X`` becomes ``network X``), nothing else;
2. needs you to confirm those lines by their hash, as a deploy does;
3. refuses if r4 no longer holds what was deployed (someone changed it since: that is a job for ``workshop restore``, the break-glass path);
4. after the write, proves the whole network is back to the baseline's fingerprint, byte for byte. A state you have PASS evidence for.

Then Forward confirms it, the same way a deploy is verified: the headless collector takes a fresh snapshot (``workshop baseline collect``,
``upload``, ``wait``: since the network should be the baseline again, that snapshot is your new baseline), and ``workshop revert compare``
judges it: the baseline requirements PASS, the fingerprint is the original baseline's, and Forward's routing diff from the original
baseline to the new snapshot is empty. MATCH means Forward agrees this is the network you started with.

``workshop restore`` stays as it was: no record, no gate, r4 back to the lab's starting advertisements whatever happened.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime

from workshop import candidate, deploy, executor, fingerprint, lab, show
from workshop.config import Settings

Refused = deploy.Refused


def invert(line: str) -> str:
    """``no network 10.20.20.0/24`` -> ``network 10.20.20.0/24``, and the other way round."""
    words = line.split()
    if words[:1] == ["no"] and words[1:2] == ["network"]:
        return " ".join(words[1:])
    if words[:1] == ["network"]:
        return "no " + line
    raise ValueError(f"cannot invert {line!r}: only BGP network statements are deployed here")


def plan(settings: Settings, log: Callable[[str], None] | None = None) -> dict:
    """``workshop revert plan``: what a revert would send, and the state it returns to. Read-only."""
    path = settings.state("deploy.json")
    if not path.is_file():
        raise Refused("ERROR", "nothing has been deployed (no deploy record): there is nothing to revert")
    record = json.loads(path.read_text())
    sent = tuple(record["lines"])
    if sent[: len(candidate.HEADER)] != candidate.HEADER:
        raise Refused("ERROR", "the deploy record does not start with r4's BGP section: it is not a change this command can revert")
    change = sent[len(candidate.HEADER):]
    inverse = candidate.HEADER + tuple(invert(line) for line in change)
    text = "\n".join(inverse) + "\n"
    target = record.get("baseline_fingerprint") or ""
    base_path = settings.state("baseline.json")
    base = json.loads(base_path.read_text()) if base_path.is_file() else {}
    if not target:
        target = base.get("fingerprint", "")
    base_ok = base.get("fingerprint") == target and (base.get("report") or {}).get("status") == "PASS"
    out = {"lines": list(inverse), "sha256": hashlib.sha256(text.encode()).hexdigest(), "deployed": list(sent), "target_fingerprint": target,
           "baseline_snapshot": base.get("snapshot", ""), "baseline_pass": base_ok, "caveats": record.get("caveats") or []}
    if log:
        log("The deploy record says r4 was sent:")
        for line in change:
            log(f"    {line}")
        log("A revert sends exactly the inverse, nothing else:")
        for line in inverse[len(candidate.HEADER):]:
            log(f"    {line}")
        state = (f"baseline snapshot {out['baseline_snapshot']}, judged PASS by `workshop baseline check`" if base_ok
                 else "the baseline the change was predicted against (its PASS record was replaced since; the fingerprint is still the target)")
        log(f"It returns the network to {state}: fingerprint {target[:12]}.")
        if out["caveats"]:
            log(f"(That deploy went out WITH CAVEATS: {', '.join(out['caveats'])}. This puts back the state Predict said was safe.)")
        log(f"To send it: workshop revert apply --confirm {out['sha256'][:7]}")
    return out


def apply(settings: Settings, confirm: str, log: Callable[[str], None]) -> dict:
    """``workshop revert apply``: the checks, the inverse lines over NAPALM, then proof the network is back to the baseline."""
    with deploy.lab_lock(settings):
        p = plan(settings)
        if confirm != p["sha256"][:7]:
            raise Refused("ERROR", f"to revert, confirm the lines you reviewed: --confirm {p['sha256'][:7]} (see `workshop revert plan`)")
        host = lab.mgmt_ips(settings)[candidate.DEVICE]
        show.napalm(log, candidate.DEVICE, "cli('show running-config section router bgp')   does r4 still hold what was deployed?")
        now = executor.show(settings, host, "show running-config section router bgp")
        drift = deploy.readback_problems(tuple(p["deployed"]), now)
        if drift:
            raise Refused("STALE", "r4 no longer holds what was deployed (" + "; ".join(drift) + "): it changed since. Reverting would be a guess. "
                                   "Use `workshop restore` (the break-glass path), then the baseline steps")
        log(f"  ✓ r4 holds exactly what was deployed; you confirmed the revert's lines ({p['sha256'][:7]})")
        lines = tuple(p["lines"])
        show.napalm(log, candidate.DEVICE, f"{len(lines)} lines, the exact inverse of the deploy: {show.NAPALM_APPLY}")
        applied = executor.apply(settings, candidate.DEVICE, host, lines)
        for line in applied.output.splitlines():
            log(f"    {line}")
        live = fingerprint.of(lab.live_configs(settings))
        back = bool(p["target_fingerprint"]) and live == p["target_fingerprint"]
        record = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "lines": list(lines), "sha256": p["sha256"],
                  "reverted": p["deployed"], "caveats": p["caveats"], "fingerprint": live, "back_to_baseline": back,
                  "baseline_snapshot": p["baseline_snapshot"], "target_fingerprint": p["target_fingerprint"]}
        settings.state("revert.json").write_text(json.dumps(record, indent=2) + "\n")
        for name in ("deploy.json", "verify.json", "steps/collect-post.json", "steps/upload-post.json", "steps/probes.json"):
            settings.state(name).unlink(missing_ok=True)  # the deployed change is gone: its records no longer describe the network
        if not back:
            raise Refused("ERROR", f"r4 took the revert, but the network's fingerprint ({live[:12]}) is not the baseline's ({p['target_fingerprint'][:12]}): "
                                   "something else changed too. Run `workshop restore`, then the baseline steps")
        log(f"  ✓ the network is back to the baseline, byte for byte (fingerprint {live[:12]}): a state with PASS evidence")
        return record


def compare(settings: Settings, log: Callable[[str], None]) -> dict:
    """``workshop revert compare``: Forward's confirmation. The fresh snapshot the baseline steps took after the revert is judged (it becomes
    your baseline), its fingerprint must be the original baseline's, and Forward's routing diff from the original baseline must be empty."""
    from workshop import baseline, data, steps
    from workshop.forward import Forward

    path = settings.state("revert.json")
    if not path.is_file():
        raise Refused("ERROR", "nothing has been reverted yet: `workshop revert plan`, then `workshop revert apply`")
    record = json.loads(path.read_text())
    original = str(record.get("baseline_snapshot") or "")
    fresh = str(steps.processed_snapshot(settings, "baseline")["snapshot"])
    if not original or fresh == original:
        raise Refused("ERROR", "there is no snapshot taken after the revert yet: run `workshop baseline collect`, `upload` and `wait`, then compare")
    snapshot_id, fp, report = baseline.judge(settings, log, fresh)  # the same three integrity checks and requirements as `baseline check`
    with Forward(settings) as fwd:
        show.note(log, f"Forward's routing diff: the original baseline ({original}) against the snapshot taken after the revert ({snapshot_id})")
        diff = data.diff(fwd.c, original, snapshot_id)
    same_fp = fp == record.get("target_fingerprint")
    reasons = []
    if report.status.value != "PASS":
        reasons.append(f"the baseline requirements are {report.status.value} on the new snapshot")
    if not same_fp:
        reasons.append(f"its fingerprint ({fp[:12]}) is not the original baseline's ({str(record.get('target_fingerprint'))[:12]})")
    if diff.rows:
        reasons.append(f"Forward finds routing differences from the original baseline ({diff.summary})")
    result = {"outcome": "MATCH" if not reasons else "MISMATCH", "snapshot": snapshot_id, "original": original, "fingerprint": fp,
              "requirements": report.status.value, "routing_changes": len(diff.rows), "reasons": reasons}
    record["verified"] = result
    path.write_text(json.dumps(record, indent=2) + "\n")
    log(f"  {'✓' if report.status.value == 'PASS' else '✗'} the baseline requirements on the new snapshot: {report.status.value}")
    log(f"  {'✓' if same_fp else '✗'} its fingerprint is the original baseline's ({fp[:12]})")
    log(f"  {'✓' if not diff.rows else '✗'} Forward's routing diff from the original baseline: {diff.summary if diff.rows else 'no route differs anywhere'}")
    return result
