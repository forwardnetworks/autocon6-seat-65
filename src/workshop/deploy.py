"""``workshop deploy``: put the approved change on the network, and only that change.

It deploys the candidate bytes recorded in a PASS evidence manifest -- never whatever is in the
working tree -- and only after every gate holds. Any failed gate stops before a single line is sent.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from workshop import candidate, executor, fingerprint, github, lab, requirements, show
from workshop.config import Settings
from workshop.evidence import Manifest
from workshop.forward import Forward

WINDOW_MAX = timedelta(hours=3)


def event_tz():
    """The event's time zone (agenda.json, via the generated guide.json): a Codespace's clock runs in UTC, the room doesn't."""
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(json.loads((Path(__file__).parent / "ui_assets" / "guide.json").read_text())["pace"]["tz"])
    except Exception:  # noqa: BLE001 - no agenda: this machine's own zone
        return datetime.now().astimezone().tzinfo


def window(at: str, now: datetime | None = None) -> datetime:
    """A change window's start: ``HH:MM`` (today, in the event's time zone) or an ISO time with its offset (the lab page
    sends one). Raises ValueError for a time that has passed or is more than three hours away."""
    now = (now or datetime.now(UTC)).astimezone(event_tz())
    at = at.strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", at):
        hour, minute = (int(x) for x in at.split(":"))
        if hour > 23 or minute > 59:
            raise ValueError(f"'{at}' is not a time of day")
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        try:
            when = datetime.fromisoformat(at)
        except ValueError:
            raise ValueError(f"'{at}' is not a time: say HH:MM") from None
        when = (when if when.tzinfo else when.replace(tzinfo=event_tz())).astimezone(event_tz())
    if when <= now:
        raise ValueError(f"the window {when:%H:%M} has already started: pick a later time, or apply now")
    if when - now > WINDOW_MAX:
        raise ValueError(f"the window {when:%H:%M} is more than {int(WINDOW_MAX.total_seconds() // 3600)} hours away: today's lab won't wait that long")
    return when


def wait_until(when: datetime, log, *, clock=time.time, sleep=time.sleep) -> None:
    """Wait for the window, saying how long is left once a minute (and nothing else: nothing is sent while waiting)."""
    while (left := when.timestamp() - clock()) > 0:
        log(f"  {datetime.fromtimestamp(clock(), event_tz()):%H:%M:%S}: {int(left // 60)} min {int(left % 60)} s to the window; nothing sent yet")
        sleep(min(60.0, left))


class Refused(RuntimeError):
    """A gate failed; nothing was sent to the network."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status  # STALE | ERROR


def confirmed(given: str | None, sha256: str) -> bool:
    """Does ``--confirm`` name this hash? The first 7 characters or more (readback prints 12), any case, with stray quotes or
    angle brackets from a copied placeholder ignored. Never fewer than 7: the point is that a person read the hash."""
    g = (given or "").strip().strip("'\"<>").lower()
    return len(g) >= 7 and sha256.lower().startswith(g)


def predict_again(m: Manifest, *, baseline: bool) -> str:
    """How to get a prediction made against today's network: the baseline steps first when the routers changed, then the
    pull request's check again (a merged PR keeps its run: re-running it predicts against the newest baseline), or the local
    predict steps."""
    first = "First the four baseline steps: `workshop baseline collect`, `upload`, `wait`, `check`. Then " if baseline else "Then "
    pr = (m.git or {}).get("pr")
    if pr:
        return (first + f"re-run PR #{pr}'s check: on GitHub, PR #{pr} > Checks > forward/predict > Re-run all jobs. When it is green, "
                f"`workshop deploy gates --pr {pr} --confirm {m.candidate_sha256[:7]}` again.")
    return first + "predict again: `workshop predict changeset`, `run`, `check`, and deploy the new evidence."


def confirm_refusal(given: str | None, sha256: str, what: str, command: str) -> str:
    """The refusal for a missing or wrong ``--confirm``: why, and the exact command to run (checked by the person, then copied)."""
    sha7 = sha256[:7]
    g = (given or "").strip()
    if not g:
        why = f"say which {what} you reviewed with --confirm and its hash: {sha7}."
    elif g.strip("'\"<>").lower() in ("sha7", "hash", "n"):
        why = f"--confirm takes the hash itself ({sha7}), not the word in the instructions."
    elif g.isdigit() and len(g) >= 5:
        why = f"--confirm {g} looks like your Forward network's or a snapshot's number; --confirm takes the {what}'s hash: {sha7}."
    else:
        why = (f"--confirm {g} is not this {what}'s hash ({sha7}). If you changed it after reading the hash, read it again "
               f"(`workshop status` or the step that printed it) before you confirm.")
    return f"{why}\n  Check it is the one you reviewed, then: {command} --confirm {sha7}"


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
    """The requirements the prediction judged FAIL, in the evidence's order (not ones it could not judge)."""
    return [str(r.get("id")) for r in m.results or [] if str(r.get("status")) == "FAIL"]


def unjudged(m: Manifest) -> list[str]:
    """Requirements that are neither PASS nor FAIL: nobody knows what they would do."""
    return [f"{r.get('id')} ({r.get('status')})" for r in m.results or [] if str(r.get("status")) not in ("PASS", "FAIL")]


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
    if unjudged(m):
        return ("Predict could not judge " + ", ".join(unjudged(m)) + ": a caveat must be a failure Predict named, not one nobody knows. "
                "Predict again until every requirement is PASS or FAIL")
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
                               "(it changed since the prediction, or the prediction used another file). " + predict_again(m, baseline=False))
    if check_github and m.git.get("pr"):
        pr = github.merged_pr(int(m.git["pr"]))
        if pr.head_sha != m.git.get("head_sha"):
            raise Refused("STALE", f"PR #{pr.number} was merged at {pr.head_sha[:7]}, but the evidence is for {str(m.git.get('head_sha'))[:7]}")
        if not github.check_passed(pr.head_sha):
            raise Refused("ERROR", f"the forward/predict check did not pass on PR #{pr.number}'s final commit")
    live = fingerprint.of(lab.live_configs(settings))
    if live != m.baseline_fingerprint:
        raise Refused("STALE", "the routers' configs are not the ones this prediction was made from (rebuilt with `workshop up`, or "
                               "changed since). " + predict_again(m, baseline=True))
    latest = fwd.latest_baseline()
    if latest is None or str(latest.id) != m.baseline_snapshot_id:
        raise Refused("STALE", f"a newer snapshot ({getattr(latest, 'id', None)}) exists than the one predicted against "
                               f"({m.baseline_snapshot_id}). " + predict_again(m, baseline=False))


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


def check(settings: Settings, evidence: Path, confirm: str | None, log: Callable[[str], None], *, check_github: bool = True,
          caveats: list[str] | None = None, again: str | None = None) -> Manifest:
    """``workshop deploy gates``: every gate, read-only. Nothing is sent to a router."""
    m = Manifest.read(evidence)
    if not confirmed(confirm, m.candidate_sha256):
        raise Refused("ERROR", confirm_refusal(confirm, m.candidate_sha256, "candidate", again or "workshop deploy apply --evidence " + str(evidence)))
    with Forward(settings) as fwd:
        log("checking the gates (any one failing stops the deploy before anything is written) ...")
        gates(settings, m, fwd, check_github=check_github, caveats=caveats)
    if caveats:
        log("  !! DEPLOY WITH CAVEATS: Predict judged this change FAIL. You are deploying it anyway, on purpose, to see whether Predict was right.")
        log(f"  !! Expect these to break on the real network: {', '.join(caveats)}. Back it out afterwards: `workshop revert plan`.")
    for gate in GATES_PASSED(m, check_github, caveats):
        log(f"  ✓ {gate}")
    return m


def apply(settings: Settings, evidence: Path, confirm: str | None, log: Callable[[str], None], *, check_github: bool = True,
          caveats: list[str] | None = None, again: str | None = None) -> dict:
    """``workshop deploy apply``: the gates once more (they must hold at the moment of the write, not a minute ago), then the
    evidence's lines to r4 over NAPALM. Writes deploy.json. ``workshop deploy readback`` confirms it on the router."""
    with lab_lock(settings):
        m = check(settings, evidence, confirm, log, check_github=check_github, caveats=caveats, again=again)
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
        try:  # the whole network after the write: a revert refuses unless it still matches (any drift, on any router)
            post_fingerprint = fingerprint.of(lab.live_configs(settings))
        except Exception:  # noqa: BLE001 - only the revert's drift check uses it; without it the revert falls back to r4's lines
            post_fingerprint = ""
        record = {
            "evidence": str(evidence), "candidate_sha256": m.candidate_sha256, "baseline_snapshot_id": m.baseline_snapshot_id,
            "post_fingerprint": post_fingerprint,
            "predicted_snapshot_id": m.predicted_snapshot_id, "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"), "lines": list(applied.lines), "device_diff": applied.output,
            "readback_problems": None, "predicted_status": m.status, "caveats": list(caveats or []),
            "baseline_fingerprint": m.baseline_fingerprint,
        }
        settings.state("deploy.json").write_text(json.dumps(record, indent=2) + "\n")
        for name in ("steps/collect-post.json", "steps/upload-post.json", "steps/probes.json", "verify.json"):
            settings.state(name).unlink(missing_ok=True)  # a new deploy: an earlier verify's collection says nothing about it
        return record


def apply_unpredicted(settings: Settings, confirm: str, log: Callable[[str], None], *, ticket: bool = True) -> dict:
    """``workshop deploy apply --without-predict``: the way changes often go out today, kept to prove a point (owner decision
    2026-11-15). Predict is overridden: no prediction, no requirement judged before the write. What still holds: you confirm
    the hash of exactly what is sent, the lab lock, and no router changed since your baseline. Forward judges the result
    afterwards (``workshop verify``), and the record goes into a change ticket (a GitHub issue)."""
    with lab_lock(settings):
        try:
            cand = candidate.load(settings.repo_root / "candidate" / "r4-bgp.eos")
        except candidate.CandidateError as exc:
            raise Refused("ERROR", f"the candidate is out of scope (only r4's BGP advertisements): {exc}") from None
        if not confirmed(confirm, cand.sha256):
            raise Refused("ERROR", confirm_refusal(confirm, cand.sha256, "candidate", "workshop deploy apply --without-predict"))
        if len(cand.lines) <= len(candidate.HEADER):
            raise Refused("ERROR", "candidate/r4-bgp.eos holds no change to send")
        try:
            base = json.loads(settings.state("baseline.json").read_text())
        except (OSError, ValueError):
            raise Refused("ERROR", "no baseline yet: take one first (`workshop baseline collect`, `upload`, `wait`, `check`)") from None
        log("checking what still holds without a prediction ...")
        live = fingerprint.of(lab.live_configs(settings))
        if live != base.get("fingerprint"):
            raise Refused("STALE", "the routers' configs are not your baseline's: run the baseline steps first")
        log(f"  ✓ you confirmed candidate {cand.sha256[:7]}, the file you mean to send")
        log(f"  ✓ all four routers still match your baseline ({base.get('snapshot')})")
        log("  !! PREDICT OVERRIDDEN: nobody has asked Forward what this change will do. It goes out on trust, the way many changes")
        log("  !! do today. Forward will tell you afterwards (`workshop verify`), on the real network.")
        lines = list(cand.lines)
        show.napalm(log, candidate.DEVICE, f"{len(lines)} lines, never predicted: {show.NAPALM_APPLY}")
        started = datetime.now(UTC)
        applied = executor.apply(settings, candidate.DEVICE, lab.mgmt_ips(settings)[candidate.DEVICE], lines)
        if applied.output.strip():
            log(f"  {candidate.DEVICE} computed this diff for the session and committed it:")
            for line in applied.output.splitlines():
                log(f"    {line}")
        number = None
        if ticket:
            try:
                from workshop import github

                body = ("**Change request CHG-AC6-0001: retire the stale BGP advertisements on r4.**\n\n"
                        "**Deployed without a prediction (Predict overridden).** No requirement was judged before the change; "
                        "Forward judges the real network afterwards, and the post-change report is added here.\n\n"
                        f"Config sent to r4 (sha256 `{cand.sha256[:12]}`):\n\n```\n" + "\n".join(cand.lines) + "\n```\n\n"
                        f"Baseline: Forward snapshot `{base.get('snapshot')}`.")
                number = github.open_ticket(f"CHG-AC6-0001 · {settings.lab_id} · deployed WITHOUT a prediction", body)
                log(f"  → change ticket opened: GitHub issue #{number}")
            except Exception as exc:  # noqa: BLE001 - the ticket is the record, not a gate
                log(f"  (could not open the change ticket: {type(exc).__name__}: {str(exc)[:120]}; the record is saved locally)")
        try:
            post_fingerprint = fingerprint.of(lab.live_configs(settings))
        except Exception:  # noqa: BLE001
            post_fingerprint = ""
        record = {
            "evidence": "", "predicted": False, "ticket": number, "candidate_sha256": cand.sha256,
            "baseline_snapshot_id": str(base.get("snapshot", "")), "baseline_fingerprint": base.get("fingerprint", ""),
            "post_fingerprint": post_fingerprint, "predicted_snapshot_id": "", "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"), "lines": list(applied.lines), "device_diff": applied.output,
            "readback_problems": None, "predicted_status": "NONE (Predict overridden)", "caveats": [],
        }
        settings.state("deploy.json").write_text(json.dumps(record, indent=2) + "\n")
        for name in ("steps/collect-post.json", "steps/upload-post.json", "steps/probes.json", "verify.json"):
            settings.state(name).unlink(missing_ok=True)
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
    judged = "the lines you sent (never predicted)" if record.get("predicted") is False else "the lines Predict judged"
    log(f"  ✓ {candidate.DEVICE} shows all {len(lines) - len(candidate.HEADER)} change(s): r4 holds exactly {judged} (hash {record['candidate_sha256'][:12]})")
    return record


def run(settings: Settings, evidence: Path, confirm: str | None, log: Callable[[str], None], *, check_github: bool = True,
        caveats: list[str] | None = None, again: str | None = None) -> dict:
    """apply, then readback (the lab page's catch-up runs this; the exercises run the steps)."""
    apply(settings, evidence, confirm, log, check_github=check_github, caveats=caveats, again=again)
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
