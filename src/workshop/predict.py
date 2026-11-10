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

from workshop import __version__, candidate, requirements, show
from workshop.config import Settings
from workshop.evaluate import Status, evaluate
from workshop.evidence import EXIT, Manifest
from workshop.forward import Forward, Prediction, SnapshotTag

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


PENDING = "PENDING"  # a manifest between steps: never deployable (only PASS is)


def run(settings: Settings, inputs: Inputs, log=None) -> Manifest:
    """All three steps in a row (the forward/predict check in GitHub Actions runs this). By hand: ``workshop predict changeset``,
    ``workshop predict run``, ``workshop predict check``, the same three functions with what each produced kept in between."""
    m = prepare(settings, inputs, log)
    if m.status != PENDING:
        return m
    m = predict(settings, m, log)
    if m.status != PENDING:
        return m
    return judge(settings, m, inputs.requirements, log)


def prepare(settings: Settings, inputs: Inputs, log=None) -> Manifest:
    """Step 1, ``workshop predict changeset``: check the candidate and the baseline, then create the change set in Forward.

    The candidate must be in scope; the newest non-predicted snapshot must be this lab's baseline, Forward's copy of its configs must
    hash to its note, and it must meet the baseline requirements. Then a change set on that baseline, holding the candidate's lines."""
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
    if log:
        log(f"  the change: {len(cand.changes)} line(s) for {cand.device}, sha256 {cand.sha256[:12]}")
    try:
        with Forward(settings) as fwd:
            m.forward_release = fwd.release()
            show.sdk(log, "snapshots.latest_processed(include_predicted=False)   the baseline to predict against")
            baseline = fwd.latest_baseline()
            if baseline is None:
                return _stop(m, "STALE", "Forward has no baseline for this lab yet. Run the baseline steps in your Codespace (`workshop baseline collect` first).")
            m.baseline_snapshot_id, m.baseline_processed_at = str(baseline.id), str(baseline.processed_at or "")
            tag = SnapshotTag.parse(baseline.note)
            if tag is None or tag.lab != settings.lab_id:
                return _stop(m, "STALE", f"The newest snapshot ({baseline.id}) is not a baseline collected from lab {settings.lab_id}. Run the baseline steps (`workshop baseline collect` first).")
            if tag.kind != "baseline":
                return _stop(m, "STALE", f"The newest snapshot ({baseline.id}) is a post-change collection; the network has already changed. Run the baseline steps (`workshop baseline collect` first) before predicting a new change.")
            m.baseline_fingerprint = tag.fp
            show.sdk(log, 'devices.file("rN", "configuration.txt") for r1-r4   Forward\'s copy of the configs, hashed against the note')
            if fwd.fingerprint(str(baseline.id)) != tag.fp:
                return _stop(m, "ERROR", f"Snapshot {baseline.id}'s router configs no longer match the fingerprint recorded when it was collected.")
            show.note(log, f"baseline requirements on snapshot {baseline.id} first: a change is only predicted against a network that is at its starting point")
            base_report = evaluate(base_reqs, fwd.observer(str(baseline.id)), str(baseline.id), root=settings.repo_root)
            m.baseline_status = base_report.status.value
            if base_report.status is not Status.PASS:
                failing = ", ".join(r.id for r in base_report.results if r.status is not Status.PASS)
                return _stop(m, "STALE", f"The baseline does not meet the baseline requirements ({failing}); the lab is not at its starting state. Run `workshop restore`, then the baseline steps (`workshop baseline collect` first).")
            if log:
                log(f"  ✓ baseline {baseline.id} is this lab's, Forward holds what was collected, and it meets the baseline requirements")
            name = change_set_name(settings.lab_id, inputs.git, cand.sha256)
            description = json.dumps({"candidate_sha256": cand.sha256, "requirements_sha256": reqs.sha256, "baseline": str(baseline.id), **inputs.git}, sort_keys=True)
            made = fwd.change_set(cand, str(baseline.id), name, description, log=log)
    except ForwardTimeoutError as exc:
        return _stop(m, "INCONCLUSIVE", f"Forward did not finish in time: {exc}")
    except ForwardError as exc:
        return _stop(m, "ERROR", f"Forward error: {type(exc).__name__}: {str(exc)[:300]}")
    except (ValueError, KeyError, OSError, RuntimeError) as exc:  # anything else is ERROR with evidence, never a crash (a crash exits 1: FAIL)
        return _stop(m, "ERROR", f"{type(exc).__name__}: {str(exc)[:300]}")
    if isinstance(made, Prediction):
        m.change_set_id, m.predicted_snapshot_id = made.change_set_id, made.snapshot_id
    else:
        m.change_set_id = made
    m.status, m.reason = PENDING, f"change set {m.change_set_id} is ready to predict"
    return m


def predict(settings: Settings, m: Manifest, log=None) -> Manifest:
    """Step 2, ``workshop predict run``: Forward builds the network the change set would produce (a predicted snapshot)."""
    if m.predicted_snapshot_id:
        if log:
            log(f"  already predicted: snapshot {m.predicted_snapshot_id}")
        return m
    try:
        with Forward(settings) as fwd:
            prediction = fwd.run_prediction(m.change_set_id, f"{change_set_name(settings.lab_id, m.git, m.candidate_sha256)} {m.candidate_sha256[:12]}", log=log)
    except ForwardTimeoutError as exc:
        return _stop(m, "INCONCLUSIVE", f"Forward did not finish in time: {exc}")
    except ForwardError as exc:
        return _stop(m, "ERROR", f"Forward error: {type(exc).__name__}: {str(exc)[:300]}")
    except (ValueError, KeyError, OSError, RuntimeError) as exc:  # anything else is ERROR with evidence, never a crash (a crash exits 1: FAIL)
        return _stop(m, "ERROR", f"{type(exc).__name__}: {str(exc)[:300]}")
    if prediction.parent_snapshot_id not in ("", "None", m.baseline_snapshot_id):
        return _stop(m, "ERROR", f"Predicted snapshot {prediction.snapshot_id} is based on {prediction.parent_snapshot_id}, not the baseline {m.baseline_snapshot_id}.")
    m.predicted_snapshot_id = prediction.snapshot_id
    m.reason = f"predicted snapshot {m.predicted_snapshot_id}, built from baseline {m.baseline_snapshot_id}"
    return m


def judge(settings: Settings, m: Manifest, requirements_path: Path, log=None) -> Manifest:
    """Step 3, ``workshop predict check``: ask the candidate requirements of the predicted snapshot. Only this can make it PASS."""
    try:
        reqs = requirements.load(requirements_path, root=settings.repo_root)
    except requirements.RequirementError as exc:
        return _stop(m, "ERROR", str(exc))
    if reqs.sha256 != m.requirements_sha256:
        return _stop(m, "STALE", f"{requirements_path.name} changed since the change set was made: start again with `workshop predict changeset`")
    show.note(log, f"judging {len(reqs.requirements)} requirements on predicted snapshot {m.predicted_snapshot_id}: each is a path search or an NQE query to Forward, compared with the YAML by evaluate.py")
    try:
        with Forward(settings) as fwd:
            report = evaluate(reqs, fwd.observer(m.predicted_snapshot_id), m.predicted_snapshot_id, root=settings.repo_root)
    except ForwardTimeoutError as exc:
        return _stop(m, "INCONCLUSIVE", f"Forward did not finish in time: {exc}")
    except ForwardError as exc:
        return _stop(m, "ERROR", f"Forward error: {type(exc).__name__}: {str(exc)[:300]}")
    except (ValueError, KeyError, OSError, RuntimeError) as exc:  # anything else is ERROR with evidence, never a crash (a crash exits 1: FAIL)
        return _stop(m, "ERROR", f"{type(exc).__name__}: {str(exc)[:300]}")
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
