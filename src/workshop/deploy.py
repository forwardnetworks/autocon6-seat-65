"""``workshop deploy``: put the approved change on the network, and only that change.

It deploys the candidate bytes recorded in a PASS evidence manifest -- never whatever is in the
working tree -- and only after every gate holds. Any failed gate stops before a single line is sent.
"""

from __future__ import annotations

import fcntl
import json
import re
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from workshop import candidate, fingerprint, github, lab, netmiko_ops, requirements
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
            raise Refused("ERROR", "another workshop command is changing this lab; wait for it to finish") from exc
        yield


def gates(settings: Settings, m: Manifest, fwd: Forward, *, check_github: bool = True) -> None:
    if not m.deployable:
        raise Refused("ERROR", f"the evidence is {m.status} ({m.mode}); only a live PASS can be deployed")
    if m.lab_id != settings.lab_id or m.network_id != settings.network_id:
        raise Refused("ERROR", f"the evidence is for lab {m.lab_id} / network {m.network_id}, not this lab")
    merged = candidate.load(settings.repo_root / m.candidate_path)
    if merged.sha256 != m.candidate_sha256:
        raise Refused("STALE", f"{m.candidate_path} changed since it was predicted; open a PR so the new change is predicted")
    reqs = requirements.load(settings.repo_root / "requirements" / "candidate.yml", root=settings.repo_root)
    if reqs.sha256 != m.requirements_sha256:
        raise Refused("STALE", "requirements/candidate.yml changed since the prediction; re-run the check")
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
        raise Refused("STALE", "the routers' configs are not what was collected for the prediction; run `workshop baseline` and re-run the check")


def run(settings: Settings, evidence: Path, confirm: str, log: Callable[[str], None], *, check_github: bool = True) -> dict:
    m = Manifest.read(evidence)
    if confirm != m.candidate_sha256[:7]:
        raise Refused("ERROR", f"to deploy, confirm the candidate you reviewed: --confirm {m.candidate_sha256[:7]}")
    with lab_lock(settings), Forward(settings) as fwd:
        log("checking the gates ...")
        gates(settings, m, fwd, check_github=check_github)
        log("  evidence PASS, candidate and requirements unchanged, baseline current, routers unchanged")
        lines = candidate.normalize(m.candidate_text)
        started = datetime.now(UTC)
        applied = netmiko_ops.apply(settings, candidate.DEVICE, lab.mgmt_ips(settings)[candidate.DEVICE], lines)
        readback = netmiko_ops.show(settings, lab.mgmt_ips(settings)[candidate.DEVICE], "show running-config section router bgp")
        problems = readback_problems(lines, readback)
        record = {
            "evidence": str(evidence), "candidate_sha256": m.candidate_sha256, "baseline_snapshot_id": m.baseline_snapshot_id,
            "predicted_snapshot_id": m.predicted_snapshot_id, "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"), "lines": list(applied.lines), "readback_problems": problems,
        }
        settings.state("deploy.json").write_text(json.dumps(record, indent=2) + "\n")
        if problems:
            raise Refused("ERROR", "the change was sent but the router does not show it as expected: " + "; ".join(problems))
        log(f"  {candidate.DEVICE}: {len(lines) - len(candidate.HEADER)} change(s) applied and read back")
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
