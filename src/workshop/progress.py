"""The challenge card in the Your lab tab: what you have done, and what is next, with hints.

Challenges are private to the attendee, computed from files already in the Codespace (the state
directory, the evidence, the pull requests' check results) -- no extra load on Forward, and nothing
is sent anywhere. Earned challenges are remembered in ``progress.json`` so one stays earned after the
pull request list that proved it scrolls away. There is no score and no ranking; hints are free, and
reveal one level at a time: a nudge, then more, then the command.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workshop.config import Settings


@dataclass(frozen=True)
class Challenge:
    id: str
    title: str
    kind: str  # core | stretch
    minutes: int  # a rough time, for stretch challenges
    hints: tuple[str, str, str]


CHALLENGES: tuple[Challenge, ...] = (
    Challenge("baseline", "Take a baseline", "core", 0, (
        "Forward needs a snapshot of your routers before it can predict anything.",
        "The lab must be up first, then collect and upload.",
        "`workshop up`, then `workshop baseline`. Every line should say PASS.")),
    Challenge("first-prediction", "Get a prediction on a pull request", "core", 0, (
        "A prediction runs when a pull request changes `candidate/r4-bgp.eos`.",
        "Make a branch, edit the candidate, commit, publish the branch, and create the pull request.",
        "Retire one stale advertisement: add `no network 10.20.30.0/24` under `address-family ipv4`.")),
    Challenge("caught", "Catch a break before deploy", "core", 0, (
        "Some changes look fine but break the service. The gate should catch one.",
        "r4's three `network` statements look alike. Only two are stale.",
        "Retire all three, including `10.20.20.0/24`, and open a pull request. A red check is the goal.")),
    Challenge("approved", "Get a passing change approved", "core", 0, (
        "Only a green check can merge.",
        "Keep the live advertisement. Push a fix to the same branch and let the check re-run.",
        "Retire only `10.20.30.0/24` and `10.20.40.0/24`, then merge with Squash and Merge.")),
    Challenge("deployed", "Deploy exactly what was predicted", "core", 0, (
        "Deploy sends the routers the same lines Predict evaluated, and only if nothing has drifted.",
        "The exact command is at the top of the green check's summary.",
        "`workshop deploy --pr <number> --confirm <first 7 characters of the candidate hash>`")),
    Challenge("verified", "Verify: reality matches the prediction", "core", 0, (
        "After the change, collect the network again and compare with what was predicted.",
        "Verify probes the real flows, collects, and re-judges every requirement.",
        "`workshop verify`, and look for MATCH.")),
    Challenge("ask-ai", "Ask Forward AI about your network", "stretch", 5, (
        "The chat in this tab is grounded in a snapshot of your own lab.",
        "Pick one of the suggestion chips, or ask your own question. It takes a minute or two.",
        "Try `workshop ask --prompt baseline-stale` in the terminal, or the first chip in the chat.")),
    Challenge("explain", "Have a failed prediction explained", "stretch", 5, (
        "Forward AI can review a prediction: what the change alters and what it affects.",
        "It needs a local prediction first.",
        "`workshop predict` on a change that fails, then `workshop explain`.")),
    Challenge("propose", "Let Forward AI draft the change", "stretch", 5, (
        "Predict's config assist can write the candidate from the intent in `intent.md`.",
        "It only drafts. You still review it and Predict still judges it.",
        "`workshop propose`, then `workshop propose --write` to keep the draft.")),
    Challenge("own-test", "Write your own behavioral test", "stretch", 10, (
        "r1's ACL also blocks HTTPS (TCP 8443), but no requirement says it must stay blocked.",
        "Copy `requirements/candidate.yml` to `requirements/mine.yml` and add a flow requirement for port 8443 that expects `deny` at r1.",
        "Then `workshop predict --requirements requirements/mine.yml`. See exercise 2 for the full test.")),
    Challenge("matrix", "Predict every way of retiring r4's statements", "stretch", 10, (
        "A regression suite runs the same tests against several changes.",
        "Guess first: which combination passes every test?",
        "`workshop matrix` prints the grid (`--all` tries all seven).")),
    Challenge("agent", "Let the agent iterate to a passing change", "stretch", 10, (
        "The agent drafts, predicts, takes advice from Forward AI, and tries again.",
        "It starts from your current candidate and stops before approval.",
        "Put a broken change in `candidate/r4-bgp.eos` (on a scratch branch), then `workshop agent`.")),
)


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def facts(settings: Settings, *, prs: list[dict[str, Any]], threads: list[dict[str, Any]], evidence: Path) -> set[str]:
    """Which challenge ids are earned right now, from the files and pull requests in this Codespace."""
    s, root = settings.state_dir, settings.repo_root
    earned: set[str] = set()
    baseline = _json(s / "baseline.json")
    if baseline and (baseline.get("report") or {}).get("status") == "PASS":
        earned.add("baseline")
    checked = [p for p in prs if (p.get("predict") or "").upper() in ("SUCCESS", "FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "NEUTRAL")]
    if checked or _exists(evidence):
        earned.add("first-prediction")
    if any((p.get("predict") or "").upper() == "FAILURE" for p in prs):
        earned.add("caught")
    manifest = _json(evidence)
    if manifest and manifest.get("status") == "FAIL" and any(r.get("status") == "FAIL" for r in manifest.get("results", [])):
        earned.add("caught")
    if any(p.get("state") == "MERGED" for p in prs):
        earned.add("approved")
    if _exists(s / "deploy.json"):
        earned.add("deployed")
    if (_json(s / "verify.json") or {}).get("outcome") == "MATCH":
        earned.add("verified")
    if any(turn.get("status") == "done" for t in threads for turn in t.get("turns", [])) or any((s / "ai").glob("*.md")):
        earned.add("ask-ai")
    if (_json(evidence.parent / "ai.json") or {}).get("review") is not None:
        earned.add("explain")
    if _exists(s / "ai" / "proposed.txt"):
        earned.add("propose")
    if any(p.name not in ("baseline.yml", "candidate.yml") for p in (root / "requirements").glob("*.yml")):
        earned.add("own-test")
    if any((s / "matrix").glob("candidate-*.eos")):
        earned.add("matrix")
    attempts = _json(evidence.parent / "agent" / "agent.json")
    if attempts and attempts[-1].get("status") == "PASS":
        earned.add("agent")
    return earned


def card(settings: Settings, *, prs: list[dict[str, Any]], threads: list[dict[str, Any]], evidence: Path) -> dict[str, Any]:
    """The challenge card: every challenge with whether it is earned, plus remembered ones. Earned stays earned."""
    path = settings.state_dir / "progress.json"
    remembered: dict[str, float] = (_json(path) or {}).get("earned", {})
    now = facts(settings, prs=prs, threads=threads, evidence=evidence)
    changed = False
    for cid in now:
        if cid not in remembered:
            remembered[cid], changed = time.time(), True
    if changed:
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"earned": remembered}, indent=1) + "\n")
    rows = [{"id": c.id, "title": c.title, "kind": c.kind, "minutes": c.minutes, "hints": list(c.hints),
             "done": c.id in remembered, "earned_at": remembered.get(c.id)} for c in CHALLENGES]
    core = [r for r in rows if r["kind"] == "core"]
    nxt = next((r["id"] for r in core if not r["done"]), None)
    return {"challenges": rows, "core_done": sum(r["done"] for r in core), "core_total": len(core), "next": nxt,
            "stretch_done": sum(r["done"] for r in rows if r["kind"] == "stretch"),
            "stretch_total": sum(1 for r in rows if r["kind"] == "stretch")}
