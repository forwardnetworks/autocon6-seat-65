"""Forward AI in the change loop: a small team of advisers around a deterministic judge.

Each role is a Forward AI surface, reached through ``workshop.forward``:

* **Analyst** -- an AI chat grounded in a snapshot (``ask``): what is in the network and why.
* **Author** -- Predict's config assist (``author``): drafts r4's EOS lines from the intent, and from
  the Troubleshooter's advice after a failed prediction. The draft must pass ``candidate.scope_problems``.
* **Reviewer** -- Predict's diff assists (``review``): plain-language summaries of what a predicted change
  alters and what it would affect, plus a name and description for the change set.
* **Troubleshooter** -- an AI chat on the *predicted* snapshot, given the failed requirements and the
  Reviewer's summary (``troubleshoot``): which part of the change causes each failure, and what to change.

None of them decides anything. PASS or FAIL comes only from ``workshop.evaluate`` on the predicted
network; approval and deployment stay with a person. Every function here returns advice or raises
``AiUnavailable`` -- callers print it and carry on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from workshop import candidate
from workshop.evidence import Manifest
from workshop.forward import AiAnswer, AiUnavailable

__all__ = ["AiAnswer", "AiUnavailable", "Proposal", "Review", "author", "intent", "review", "troubleshoot"]

AUTHOR_CHANGE_SET = "ac6-{lab}-author"
DRAFT_NOTE = "! Drafted by Forward AI (Predict config assist). Review it before you open a pull request."


class Advisor(Protocol):
    """What the roles need from Forward (``workshop.forward.Forward`` in production)."""

    def ask_ai(self, prompt: str, snapshot_id: str, *, timeout: float = ..., busy_timeout: float = ...) -> AiAnswer: ...
    def author_change_set(self, baseline_id: str, name: str) -> str: ...
    def cli_assist(self, change_set_id: str, device: str, prompt: str) -> str: ...
    def change_overview(self, change_set_id: str) -> dict[str, Any]: ...
    def diff_summaries(self, before_id: str, after_id: str) -> dict[str, str]: ...


def intent(root: Path) -> str:
    """The ``## Intent`` section of ``intent.md``: the sentence both people and the Author work from."""
    text = (root / "intent.md").read_text()
    match = re.search(r"^## Intent\s*\n(.*?)(?=^## |\Z)", text, re.DOTALL | re.MULTILINE)
    if not match or not match.group(1).strip():
        raise ValueError(f"{root / 'intent.md'} has no '## Intent' section")
    return " ".join(match.group(1).split())


# --- Author -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    raw: str  # what the config assist returned
    lines: tuple[str, ...]  # normalized candidate lines (header + changes)
    problems: tuple[str, ...]  # scope problems; empty means it may be written as the candidate

    @property
    def ok(self) -> bool:
        return not self.problems

    def file_text(self) -> str:
        indent = {0: "", 1: "   "}
        body = [indent.get(i, "      ") + line for i, line in enumerate(self.lines)]
        return "\n".join([DRAFT_NOTE, *body]) + "\n"


def author(fwd: Advisor, *, lab_id: str, baseline_id: str, goal: str, feedback: str = "") -> Proposal:
    """Ask the config assist for r4's change and check it against the candidate's scope."""
    prompt = (
        f"{goal} Write only the lines under 'router bgp 65004' / 'address-family ipv4' on r4; "
        "use 'no network <prefix>' to retire an advertisement."
    )
    if feedback:
        prompt += f" A previous attempt failed prediction. Advice from that failure: {feedback}"
    change_set = fwd.author_change_set(baseline_id, AUTHOR_CHANGE_SET.format(lab=lab_id))
    raw = fwd.cli_assist(change_set, candidate.DEVICE, prompt)
    lines = candidate.normalize(_strip_fences(raw))
    if lines and tuple(lines[: len(candidate.HEADER)]) != candidate.HEADER and all(_is_network(line) for line in lines):
        lines = candidate.HEADER + lines  # the assist sometimes omits the context lines
    return Proposal(raw=raw, lines=lines, problems=tuple(candidate.scope_problems(lines)))


def _strip_fences(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("```"))


def _is_network(line: str) -> bool:
    return bool(re.match(r"^(no )?network \S+$", line))


# --- Reviewer -----------------------------------------------------------------------------------


@dataclass
class Review:
    config: str = ""  # what the predicted change alters
    impact: str = ""  # what it would affect
    name: str = ""
    description: str = ""
    notes: list[str] = field(default_factory=list)  # roles that could not answer

    def to_json(self) -> dict[str, Any]:
        return {"config": self.config, "impact": self.impact, "name": self.name, "description": self.description, "notes": self.notes}


def review(fwd: Advisor, m: Manifest) -> Review:
    """Summaries of a predicted change. Each part is independent; a part that fails leaves a note."""
    out = Review()
    if not (m.baseline_snapshot_id and m.predicted_snapshot_id):
        out.notes.append("nothing was predicted, so there is nothing to review")
        return out
    try:
        summaries = fwd.diff_summaries(m.baseline_snapshot_id, m.predicted_snapshot_id)
        out.config, out.impact = summaries.get("config", ""), summaries.get("impact", "")
    except AiUnavailable as exc:
        out.notes.append(str(exc))
    if m.change_set_id:
        try:
            overview = fwd.change_overview(m.change_set_id)
            out.name, out.description = overview.get("name", ""), overview.get("description", "")
        except AiUnavailable as exc:
            out.notes.append(str(exc))
    return out


# --- Troubleshooter -----------------------------------------------------------------------------


def troubleshoot_prompt(m: Manifest, reqs: Any = None) -> str:
    """State the change and what broke as plain facts, then ask one question.

    Measured on fwd.app (docs/api-contract.md): this shape names the offending line; a longer prompt
    carrying summaries and instructions got "I wasn't able to produce a complete answer", and a bare
    "which one broke it" was refused as needing a cross-snapshot diff.
    """
    removed = [line[len("no network "):] for line in m.candidate_text.splitlines() if line.startswith("no network ")]
    added = [line[len("network "):] for line in m.candidate_text.splitlines() if line.startswith("network ")]
    change = []
    if removed:
        change.append("removes the BGP statements " + ", ".join(f"'network {p}'" for p in removed))
    if added:
        change.append("adds " + ", ".join(f"'network {p}'" for p in added))
    by_id = {r.id: r for r in reqs.requirements} if reqs is not None else {}
    broke = [_failure_sentence(r, by_id.get(r["id"])) for r in m.results if r.get("status") != "PASS"]
    verb, noun = ("remove", "removed statements") if removed and not added else ("make", "changed statements")
    return (f"This snapshot predicts a change on r4 that {' and '.join(change) or 'changes its BGP configuration'}. "
            f"With it, {'; '.join(broke) or 'some requirements fail'}. "
            f"Which of the {noun} causes that, and which of them are safe to {verb}?")


def _failure_sentence(result: dict[str, Any], req: Any) -> str:
    kind = getattr(req, "kind", "")
    if kind == "flow" and req.expect == "permit":
        return f"{req.src} can no longer reach {req.dst} on {req.proto.upper()} {req.port}"
    if kind == "flow":
        return f"{req.proto.upper()} {req.port} from {req.src} to {req.dst} is no longer blocked at {req.deny_at}"
    if kind == "route" and "no route" in result.get("observed", ""):
        return result["observed"]
    if kind == "path":
        return f"traffic to {req.dst} no longer takes the path {' -> '.join(req.path)} ({result.get('observed', '')})"
    return f"'{result.get('title', result['id'])}' no longer holds (Forward observed {result.get('observed', '')})"


def troubleshoot(fwd: Advisor, m: Manifest, reqs: Any = None, *, timeout: float = 300) -> AiAnswer:
    """Ask why a prediction failed, on the predicted snapshot itself."""
    if not m.predicted_snapshot_id:
        raise AiUnavailable("there is no predicted snapshot to troubleshoot")
    return fwd.ask_ai(troubleshoot_prompt(m, reqs), m.predicted_snapshot_id, timeout=timeout)


# --- Sample prompts ------------------------------------------------------------------------------


@dataclass(frozen=True)
class SamplePrompt:
    id: str
    role: str
    snapshot: str  # baseline | predicted | post
    prompt: str
    look_for: str
    why: str


def sample_prompts(root: Path) -> dict[str, SamplePrompt]:
    import yaml

    out = {}
    for path in sorted((root / "prompts").glob("*.yml")):
        data = yaml.safe_load(path.read_text())
        p = SamplePrompt(**{k: str(data[k]).strip() for k in SamplePrompt.__dataclass_fields__})
        if p.snapshot not in ("baseline", "predicted", "post"):
            raise ValueError(f"{path}: snapshot must be baseline, predicted or post")
        out[p.id] = p
    return out
