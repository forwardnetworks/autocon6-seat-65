"""``workshop reset --to N``: start exercise N again from a known point, whatever state things are in.

What it does, in order (each printed as it happens):
  1. Git: anything you changed is kept with ``git stash`` (``git stash list`` shows it, ``git stash pop`` brings it back),
     then you are on ``main`` with the latest from GitHub (and told to run ``uv sync --frozen`` if its pinned packages changed). Nothing is deleted.
  2. Routers: a netlab redeploy. ``netlab down --cleanup`` removes the lab, ``netlab up`` builds it again and configures every
     router from lab/topology.yml, exactly as at the start. Whatever was changed, by a deploy or by hand, is gone.
  3. The starting point of exercise N: a fresh baseline from exercise 2 on, and the candidate the exercise starts from.
Pull requests on GitHub are left alone: close any you no longer want there.
"""

from __future__ import annotations

from datetime import UTC, datetime

from workshop import baseline, candidate, gitrepo, lab, show
from workshop.config import Settings

R4_PREFIXES = ("10.20.20.0/24", "10.20.30.0/24", "10.20.40.0/24")
STARTS: dict[int, dict] = {
    1: {"what": "exercise 1: the lab at its baseline config, no baseline in Forward yet", "baseline": False, "retire": None},
    2: {"what": "exercise 2: a fresh baseline, an empty change", "baseline": True, "retire": None},
    3: {"what": "exercise 3: a fresh baseline, an empty change", "baseline": True, "retire": None},
    4: {"what": "exercise 4: a fresh baseline, and the change that breaks the service (retire all three) to fix",
        "baseline": True, "retire": list(R4_PREFIXES)},
    5: {"what": "exercise 5: a fresh baseline, and the fixed change (retire the two stale ones) ready to predict and deploy",
        "baseline": True, "retire": ["10.20.30.0/24", "10.20.40.0/24"]},
}


class ResetError(RuntimeError):
    pass


def _lock_text(settings: Settings) -> str:
    try:
        return (settings.repo_root / "uv.lock").read_text()
    except OSError:
        return ""


def write_candidate(settings: Settings, retire: list[str] | None) -> None:
    """candidate/r4-bgp.eos: its comment header, the BGP header, and ``no network`` for each prefix in ``retire`` (none: an
    empty change). Always written, because after exercise 4's merge main's copy already retires the stale two."""
    path = settings.repo_root / "candidate" / "r4-bgp.eos"
    head = [line for line in path.read_text().splitlines() if line.startswith("!")]
    lines = [*head, candidate.HEADER[0], f"   {candidate.HEADER[1]}", *[f"      no network {p}" for p in R4_PREFIXES if p in (retire or [])]]
    path.write_text("\n".join(lines) + "\n")


def run(settings: Settings, to: int, log) -> None:
    start = STARTS[to]
    log(f"resetting to the start of {start['what']}")
    log("")
    log("1. Git: keep your work, then back to main")
    root = settings.repo_root
    lock_before = _lock_text(settings)
    try:
        if gitrepo.dirty(root):
            stamp = datetime.now(UTC).strftime("%H%M%S")
            show.git(log, f"stash push --include-untracked -m 'workshop reset {stamp}'")
            gitrepo.stash(root, f"workshop reset {stamp}")
            log(f"  your changes are kept: `git stash list` shows 'workshop reset {stamp}', `git stash pop` brings them back")
        show.git(log, "switch main")
        gitrepo.switch(root, "main")
    except gitrepo.GitError as exc:
        raise ResetError(str(exc)) from exc
    show.git(log, "pull --ff-only")
    try:
        gitrepo.pull(root)
    except gitrepo.GitError:
        log("  (could not pull from GitHub just now; carrying on with the main you have)")
    if _lock_text(settings) != lock_before:
        log("  the update changes the pinned packages (uv.lock): after this finishes, run `uv sync --frozen` to install them")
    log("")
    log("2. Routers: a netlab redeploy, so every router is exactly as netlab first configured it (3 to 10 minutes)")
    try:
        lab.netlab(settings, log, "down", "--cleanup")
    except lab.LabError as exc:
        log(f"  (netlab down did not finish cleanly: {str(exc).splitlines()[0][:120]}; building it again anyway)")
    lab.up(settings, log)
    log("")
    log("3. The starting point of the exercise")
    write_candidate(settings, start["retire"])
    if start["retire"]:
        log(f"  candidate/r4-bgp.eos now retires: {', '.join(start['retire'])}   (not committed)")
    else:
        log("  candidate/r4-bgp.eos holds no change yet")
    if start["baseline"]:
        snap, _fp, report = baseline.run(settings, log)
        status = report.status.value if report else "?"
        log(f"  fresh baseline: snapshot {snap}, requirements {status}")
        if status != "PASS":
            raise ResetError(f"the fresh baseline is {status}, not PASS: run `workshop reset --to {to}` again, or ask a proctor")
    log("")
    if to == 5:
        log("  A fresh baseline makes any earlier prediction stale. To deploy this change now: the predict steps, then")
        log("  `workshop deploy gates|render|apply|readback --evidence evidence/evidence.json --confirm <sha7> --no-github` (check prints the sha7).")
    log(f"Ready: you are at the start of exercise {to}. Open docs/exercises/ for it, or the Your lab page.")
