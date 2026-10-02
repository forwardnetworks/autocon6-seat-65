"""``workshop predict``: the PR gate.

Runs the same on a laptop and on a GitHub-hosted runner (it needs only Forward). In order:

1. The candidate must be in scope (``candidate.load``) -- else ERROR, no Forward call.
2. The newest non-predicted snapshot must be a baseline tagged for this lab, and Forward's copy of
   its router configs must still match the fingerprint in the tag -- else STALE or ERROR.
3. That baseline must meet the *baseline* requirements -- else STALE: the lab is not at baseline.
4. Predict the candidate against it (a retry reuses a finished prediction), then judge the predicted
   snapshot against the *candidate* requirements. Only that verdict can be PASS.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forward_sdk.errors import ForwardError, ForwardTimeoutError

from workshop import __version__, candidate, requirements
from workshop.config import Settings
from workshop.evaluate import Status, evaluate
from workshop.evidence import EXIT, Manifest
from workshop.forward import Forward, SnapshotTag

__all__ = ["EXIT", "Inputs", "change_set_name", "run"]


@dataclass(frozen=True)
class Inputs:
    candidate: Path
    requirements: Path
    baseline_requirements: Path
    git: dict[str, Any]


def change_set_name(lab_id: str, git: dict[str, Any], candidate_sha: str) -> str:
    if git.get("pr") and git.get("head_sha"):
        return f"ac6-{lab_id}-pr{git['pr']}-{str(git['head_sha'])[:7]}"
    return f"ac6-{lab_id}-local-{candidate_sha[:7]}"


def run(settings: Settings, inputs: Inputs) -> Manifest:
    m = Manifest(
        lab_id=settings.lab_id, network_id=settings.network_id, forward_release="", workshop_version=__version__,
        candidate_path=_display_path(inputs.candidate, settings.repo_root),
        candidate_sha256="", candidate_text="", requirements_path=str(inputs.requirements.name), requirements_sha256="",
        baseline_snapshot_id="", baseline_fingerprint="", baseline_processed_at="", baseline_status="", git=dict(inputs.git),
    )
    try:
        cand = candidate.load(inputs.candidate)
        m.candidate_sha256, m.candidate_text = cand.sha256, cand.text
        reqs = requirements.load(inputs.requirements, root=settings.repo_root)
        base_reqs = requirements.load(inputs.baseline_requirements, root=settings.repo_root)
        m.requirements_sha256 = reqs.sha256
    except (candidate.CandidateError, requirements.RequirementError) as exc:
        return _stop(m, "ERROR", str(exc))

    try:
        with Forward(settings) as fwd:
            m.forward_release = fwd.release()
            baseline = fwd.latest_baseline()
            if baseline is None:
                return _stop(m, "STALE", "Forward has no baseline for this lab yet. Run `workshop baseline` in your Codespace.")
            m.baseline_snapshot_id, m.baseline_processed_at = str(baseline.id), str(baseline.processed_at or "")
            tag = SnapshotTag.parse(baseline.note)
            if tag is None or tag.lab != settings.lab_id:
                return _stop(m, "STALE", f"The newest snapshot ({baseline.id}) is not a baseline collected from lab {settings.lab_id}. Run `workshop baseline`.")
            if tag.kind != "baseline":
                return _stop(m, "STALE", f"The newest snapshot ({baseline.id}) is a post-change collection; the network has already changed. Run `workshop baseline` before predicting a new change.")
            m.baseline_fingerprint = tag.fp
            if fwd.fingerprint(str(baseline.id)) != tag.fp:
                return _stop(m, "ERROR", f"Snapshot {baseline.id}'s router configs no longer match the fingerprint recorded when it was collected.")
            base_report = evaluate(base_reqs, fwd.observer(str(baseline.id)), str(baseline.id), root=settings.repo_root)
            m.baseline_status = base_report.status.value
            if base_report.status is not Status.PASS:
                failing = ", ".join(r.id for r in base_report.results if r.status is not Status.PASS)
                return _stop(m, "STALE", f"The baseline does not meet the baseline requirements ({failing}); the lab is not at its starting state. Run `workshop restore`, then `workshop baseline`.")

            name = change_set_name(settings.lab_id, inputs.git, cand.sha256)
            description = json.dumps({"candidate_sha256": cand.sha256, "requirements_sha256": reqs.sha256, "baseline": str(baseline.id), **inputs.git}, sort_keys=True)
            prediction = fwd.predict(cand, str(baseline.id), name, description)
            m.change_set_id, m.predicted_snapshot_id = prediction.change_set_id, prediction.snapshot_id
            if prediction.parent_snapshot_id not in ("", "None", str(baseline.id)):
                return _stop(m, "ERROR", f"Predicted snapshot {prediction.snapshot_id} is based on {prediction.parent_snapshot_id}, not the baseline {baseline.id}.")
            report = evaluate(reqs, fwd.observer(prediction.snapshot_id), prediction.snapshot_id, root=settings.repo_root)
    except ForwardTimeoutError as exc:
        return _stop(m, "INCONCLUSIVE", f"Forward did not finish in time: {exc}")
    except ForwardError as exc:
        return _stop(m, "ERROR", f"Forward error: {type(exc).__name__}: {str(exc)[:300]}")

    m.results = [r.to_json() for r in report.results]
    m.status = report.status.value
    if report.status is Status.PASS:
        m.reason = "Every requirement holds on the predicted network. The change may be approved and deployed."
    elif report.status is Status.FAIL:
        failed = [r for r in report.results if r.status is Status.FAIL]
        m.reason = "On the predicted network this change fails: " + "; ".join(f"**{r.id}** ({r.title})" for r in failed) + ". Nothing has been deployed."
    else:
        m.reason = "The prediction could not be judged with confidence; it is not approvable. Re-run the check."
    return m


def _display_path(path: Path, root: Path) -> str:
    """The candidate's path relative to the repository when it is inside it, else as given."""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _stop(m: Manifest, status: str, reason: str) -> Manifest:
    m.status, m.reason = status, reason
    return m
