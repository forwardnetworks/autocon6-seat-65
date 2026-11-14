"""``workshop deploy``: put the approved change on the network, and only that change.

It deploys the candidate bytes recorded in a PASS evidence manifest -- never whatever is in the
working tree -- and only after every gate holds. Any failed gate stops before a single line is sent.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from workshop import candidate, executor, fingerprint, github, lab, requirements, show
from workshop.config import Settings
from workshop.evidence import Manifest
from workshop.forward import Forward


class Refused(RuntimeError):
    """A gate failed; nothing was sent to the network."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status  # STALE | ERROR


@contextmanager
def lab_lock(settings: Settings):
    path = settings.state("lab.lock")
    with path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refused("ERROR", "another workshop command is changing this lab (a deploy, or the lab page's catch-up); wait for it to finish, then run this again") from exc
        yield


def failing(m: Manifest) -> list[str]:
    """The requirements a prediction did not pass, in the evidence's order."""
    return [str(r.get("id")) for r in m.results or [] if str(r.get("status")) != "PASS"]


def caveats_problem(m: Manifest, caveats: list[str]) -> str:
    """Why a deploy *with caveats* is not allowed for this evidence, or "" if it is.

    Deploying with caveats is the one way past a FAIL, for the exercise that deploys a change Predict said would break and then backs
    it out. It is narrow on purpose: only a live FAIL (a specific, known failure; never ERROR, INCONCLUSIVE or STALE, which mean nobody
    knows), only a local prediction (a red pull request cannot merge), and only when the person names every failing requirement,
    exactly, so they have read what they are accepting. Like a change approved with caveats: the risk is named and owned, not hidden."""
    if m.mode != "live" or not m.predicted_snapshot_id:
        return f"the evidence is {m.mode} with no prediction; caveats apply only to a live prediction"
    if m.status != "FAIL":
        return f"the evidence is {m.status}: caveats are only for a FAIL, where Predict says exactly what breaks (nobody knows what an {m.status} would do)"
    if (m.git or {}).get("pr"):
        return "this evidence is a pull request's: a red check cannot merge, so it cannot deploy. Caveats are for a local prediction (--evidence)"
    want = failing(m)
    if sorted(set(caveats)) != sorted(set(want)):
        return f"name every failing requirement you are accepting, exactly: --with-caveats {','.join(want)}"
    return ""


def gates(settings: Settings, m: Manifest, fwd: Forward, *, check_github: bool = True, caveats: list[str] | None = None) -> None:
    if caveats:
        problem = caveats_problem(m, caveats)
        if problem:
            raise Refused("ERROR", f"not deployable with caveats: {problem}")
    elif not m.deployable:
        hint = (f" (to deploy it anyway, on purpose, and see Predict proved right: --with-caveats {','.join(failing(m))})"
                if m.status == "FAIL" and m.mode == "live" and not (m.git or {}).get("pr") else "")
        raise Refused("ERROR", f"the evidence is {m.status} ({m.mode}); only a live PASS can be deployed. Fix the change, predict again, and deploy the evidence that passes{hint}")
    if m.lab_id != settings.lab_id or m.network_id != settings.network_id:
        raise Refused("ERROR", f"the evidence is for lab {m.lab_id} / network {m.network_id}, not this lab")
    sent = "\n".join(candidate.normalize(m.candidate_text)) + "\n"
    if hashlib.sha256(sent.encode()).hexdigest() != m.candidate_sha256:
        raise Refused("ERROR", "the evidence's candidate text does not match its own hash: it was edited after Predict; predict again")
    merged = candidate.load(settings.repo_root / m.candidate_path)
    if merged.sha256 != m.candidate_sha256:
        raise Refused("STALE", f"{m.candidate_path} changed since it was predicted; open a PR so the new change is predicted")
    reqs = requirements.load(settings.repo_root / "requirements" / "candidate.yml", root=settings.repo_root)
    if reqs.sha256 != m.requirements_sha256:
        raise Refused("STALE", "the evidence was judged against different requirements than requirements/candidate.yml on this branch "
                               "(it changed since the prediction, or the prediction used another file): re-run the check")
    if check_github and m.git.get("pr"):
        pr = github.merged_pr(int(m.git["pr"]))
        if pr.head_sha != m.git.get("head_sha"):
            raise Refused("STALE", f"PR #{pr.number} was merged at {pr.head_sha[:7]}, but the evidence is for {str(m.git.get('head_sha'))[:7]}")
        if not github.check_passed(pr.head_sha):
            raise Refused("ERROR", f"the forward/predict check did not pass on PR #{pr.number}'s final commit")
    latest = fwd.latest_baseline()
    if latest is None or str(latest.id) != m.baseline_snapshot_id:
        raise Refused("STALE", f"a newer snapshot ({getattr(latest, 'id', None)}) exists than the one predicted against ({m.baseline_snapshot_id}); re-run the check")
    live = fingerprint.of(lab.live_configs(settings))
    if live != m.baseline_fingerprint:
        raise Refused("STALE", "the routers' configs are not what was collected for the prediction; run the baseline steps (`workshop baseline collect` first) and re-run the check")


def GATES_PASSED(m: Manifest, check_github: bool, caveats: list[str] | None = None) -> list[str]:
    """What ``gates`` has just proved, one line each (``gates`` raises on the first that fails)."""
    verdict = (f"the prediction was FAIL, and you accepted its caveats by name: {', '.join(caveats)} (a deploy WITH CAVEATS: Predict says these will break)"
               if caveats else "the prediction was PASS")
    out = [f"you confirmed candidate {m.candidate_sha256[:7]}, the one Predict judged",
           f"{verdict} (evidence for lab {m.lab_id}, network {m.network_id})",
           "candidate/r4-bgp.eos holds the same lines that were predicted (comments and spacing aside), and requirements/candidate.yml is byte for byte the same"]
    if check_github and (m.git or {}).get("pr"):
        out.append(f"PR #{m.git['pr']} is merged, and its forward/predict check passed on the commit that was predicted")
    out += [f"your baseline ({m.baseline_snapshot_id}) is still the newest snapshot in Forward",
            "all four routers still match the baseline's fingerprint (nothing changed by hand)"]
    return out


def check(settings: Settings, evidence: Path, confirm: str, log: Callable[[str], None], *, check_github: bool = True,
          caveats: list[str] | None = None) -> Manifest:
    """``workshop deploy gates``: every gate, read-only. Nothing is sent to a router."""
    m = Manifest.read(evidence)
    if confirm != m.candidate_sha256[:7]:
        raise Refused("ERROR", f"to deploy, confirm the candidate you reviewed: --confirm {m.candidate_sha256[:7]}")
    with Forward(settings) as fwd:
        log("checking the gates (any one failing stops the deploy before anything is written) ...")
        gates(settings, m, fwd, check_github=check_github, caveats=caveats)
    if caveats:
        log("  !! DEPLOY WITH CAVEATS: Predict judged this change FAIL. You are deploying it anyway, on purpose, to see whether Predict was right.")
        log(f"  !! Expect these to break on the real network: {', '.join(caveats)}. Back it out afterwards: `workshop revert plan`.")
    for gate in GATES_PASSED(m, check_github, caveats):
        log(f"  ✓ {gate}")
    return m


def apply(settings: Settings, evidence: Path, confirm: str, log: Callable[[str], None], *, check_github: bool = True,
          caveats: list[str] | None = None) -> dict:
    """``workshop deploy apply``: the gates once more (they must hold at the moment of the write, not a minute ago), then the
    evidence's lines to r4 over NAPALM. Writes deploy.json. ``workshop deploy readback`` confirms it on the router."""
    with lab_lock(settings):
        m = check(settings, evidence, confirm, log, check_github=check_github, caveats=caveats)
        lines = candidate.normalize(m.candidate_text)
        show.napalm(log, candidate.DEVICE, f"{len(lines)} lines, the exact bytes Predict judged: {show.NAPALM_APPLY}")
        started = datetime.now(UTC)
        applied = executor.apply(settings, candidate.DEVICE, lab.mgmt_ips(settings)[candidate.DEVICE], lines)
        if applied.output.strip():
            log(f"  {candidate.DEVICE} computed this diff for the session and committed it:")
            for line in applied.output.splitlines():
                log(f"    {line}")
        else:
            log(f"  {candidate.DEVICE}: no difference, the router already matches the candidate")
        record = {
            "evidence": str(evidence), "candidate_sha256": m.candidate_sha256, "baseline_snapshot_id": m.baseline_snapshot_id,
            "predicted_snapshot_id": m.predicted_snapshot_id, "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"), "lines": list(applied.lines), "device_diff": applied.output,
            "readback_problems": None, "predicted_status": m.status, "caveats": list(caveats or []),
            "baseline_fingerprint": m.baseline_fingerprint,
        }
        settings.state("deploy.json").write_text(json.dumps(record, indent=2) + "\n")
        for name in ("steps/collect-post.json", "steps/upload-post.json", "steps/probes.json", "verify.json"):
            settings.state(name).unlink(missing_ok=True)  # a new deploy: an earlier verify's collection says nothing about it
        return record


def readback(settings: Settings, log: Callable[[str], None]) -> dict:
    """``workshop deploy readback``: read r4's BGP section back and check every line of the change is there."""
    path = settings.state("deploy.json")
    if not path.is_file():
        raise Refused("ERROR", "nothing deployed yet: run `workshop deploy apply` first")
    record = json.loads(path.read_text())
    lines = tuple(record["lines"])
    show.napalm(log, candidate.DEVICE, "cli('show running-config section router bgp')   what the router holds now")
    text = executor.show(settings, lab.mgmt_ips(settings)[candidate.DEVICE], "show running-config section router bgp")
    problems = readback_problems(lines, text)
    record["readback_problems"] = problems
    path.write_text(json.dumps(record, indent=2) + "\n")
    if problems:
        raise Refused("ERROR", "the change was sent but the router does not show it as expected: " + "; ".join(problems))
    log(f"  ✓ {candidate.DEVICE} shows all {len(lines) - len(candidate.HEADER)} change(s): r4 holds exactly the lines Predict judged (hash {record['candidate_sha256'][:12]})")
    return record


def run(settings: Settings, evidence: Path, confirm: str, log: Callable[[str], None], *, check_github: bool = True,
        caveats: list[str] | None = None) -> dict:
    """apply, then readback (the lab page's catch-up runs this; the exercises run the steps)."""
    apply(settings, evidence, confirm, log, check_github=check_github, caveats=caveats)
    record = readback(settings, log)
    log("")
    approved = "the pull request merged and its check green" if check_github and Manifest.read(evidence).git.get("pr") else "no pull request was involved (a local rehearsal)"
    log(f"What this means: the gates allowed it (evidence PASS, {approved}, the baseline still current, no router edited by hand since),")
    log("and r4 now holds exactly what Predict judged. Whether the real network behaves as predicted is what the verify steps check next (`workshop verify probe` first).")
    return record


def readback_problems(lines: tuple[str, ...], readback: str) -> list[str]:
    present = {m.group(1) for m in re.finditer(r"^\s*network (\S+)", readback, re.MULTILINE)}
    problems = []
    for line in lines[len(candidate.HEADER):]:
        prefix = line.split()[-1]
        if line.startswith("no ") and prefix in present:
            problems.append(f"{prefix} is still advertised")
        if not line.startswith("no ") and prefix not in present:
            problems.append(f"{prefix} is not configured")
    return problems
