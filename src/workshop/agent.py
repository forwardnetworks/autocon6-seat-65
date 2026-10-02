"""``workshop agent``: the loop with advisers, stopped before approval.

    Author drafts -> Predict judges -> on a failure, Reviewer and Troubleshooter advise -> Author again

until the prediction passes or the iteration budget runs out. Each attempt is written to the candidate
file and predicted exactly as ``workshop predict`` would; the PASS it may end on is the same deterministic
verdict. The agent never commits, opens a pull request, approves or deploys: it ends by printing the
commands a person runs next.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from workshop import ai, candidate, predict, requirements
from workshop.config import Settings
from workshop.evidence import Manifest


@dataclass
class Attempt:
    number: int
    candidate: str
    status: str
    reason: str
    review: dict[str, Any] = field(default_factory=dict)
    advice: str = ""


@dataclass
class Outcome:
    status: str  # the last prediction's status, or ERROR when no candidate could be drafted
    attempts: list[Attempt]
    manifest: Manifest | None


def run(settings: Settings, fwd: ai.Advisor, inputs: predict.Inputs, *, baseline_id: str, max_iterations: int,
        log: Callable[[str], None], predict_fn: Callable[[Settings, predict.Inputs], Manifest] = predict.run,
        out_dir: Path, fresh: bool = False) -> Outcome:
    """Run the loop. Unless ``fresh``, a candidate that already holds in-scope changes is attempt 1:
    hand the agent your own failing change and it takes it from there."""
    out_dir.mkdir(parents=True, exist_ok=True)
    goal = ai.intent(settings.repo_root)
    reqs = requirements.load(inputs.requirements, root=settings.repo_root)
    attempts: list[Attempt] = []
    advice, manifest = "", None
    yours = None if fresh else _existing(inputs.candidate)
    for number in range(1, max_iterations + 1):
        if number == 1 and yours:
            log("-- attempt 1: your candidate, as it is")
            lines = yours
        else:
            log(f"-- attempt {number}: Author (Predict config assist) drafts r4's change")
            try:
                proposal = ai.author(fwd, lab_id=settings.lab_id, baseline_id=baseline_id, goal=goal, feedback=advice)
            except ai.AiUnavailable as exc:
                log(f"   {exc}")
                return Outcome("ERROR", attempts, manifest)
            if not proposal.ok:
                log("   the draft is outside the change's scope: " + "; ".join(proposal.problems))
                advice = "Your last draft was rejected because " + "; ".join(proposal.problems) + "."
                attempts.append(Attempt(number, proposal.raw, "ERROR", "draft out of scope", advice=advice))
                continue
            inputs.candidate.write_text(proposal.file_text())
            lines = proposal.lines
        for line in lines[2:]:
            log(f"   {line}")

        log("   Judge (Forward Predict + the requirements) evaluates it ...")
        manifest = predict_fn(settings, inputs)
        manifest.write(out_dir / f"attempt-{number}" / "evidence.json")
        attempt = Attempt(number, "\n".join(lines), manifest.status, manifest.reason)
        attempts.append(attempt)
        log(f"   {manifest.status}: {manifest.reason}")
        if manifest.status in ("PASS", "STALE", "ERROR"):
            break  # a pass is done; stale or error needs a person, not another draft

        log("   Reviewer (diff assists) and Troubleshooter (AI chat on the prediction) advise ...")
        review = ai.review(fwd, manifest)
        attempt.review = review.to_json()
        if review.impact:
            log(f"   impact: {review.impact.splitlines()[0][:200]}")
        try:
            answer = ai.troubleshoot(fwd, manifest, reqs)
            advice = answer.summary
            attempt.advice = advice
            log(f"   advice: {advice.splitlines()[0][:200] if advice else '(none)'}")
        except ai.AiUnavailable as exc:
            advice = "The prediction failed: " + manifest.reason
            log(f"   {exc}")
    (out_dir / "agent.json").write_text(json.dumps([a.__dict__ for a in attempts], indent=2) + "\n")
    return Outcome(manifest.status if manifest else "ERROR", attempts, manifest)


def _existing(path: Path) -> tuple[str, ...] | None:
    try:
        return candidate.load(path).lines
    except (candidate.CandidateError, OSError):
        return None
