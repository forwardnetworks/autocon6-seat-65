"""``workshop matrix``: predict every way of retiring r4's advertisements and print a pass/fail grid.

A regression suite is a test matrix: the same behavioral tests run against several candidate
changes. This runs the ordinary prediction (``workshop predict``) once per combination of r4's
``network`` statements and shows which tests each combination passes. It is an exploration tool:
it writes no candidate, no evidence and nothing deployable, and it never touches the routers.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable
from dataclasses import dataclass

from workshop import candidate, predict, requirements
from workshop.config import Settings
from workshop.evidence import Manifest

#: The four combinations worth comparing (a stale one, both stale, the live one, everything).
DEFAULT_PICKS = ("one stale", "both stale", "the live one", "all three")
_NETWORK = re.compile(r"^\s*network (\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Row:
    label: str
    retired: tuple[str, ...]
    status: str  # the prediction's overall status
    results: dict[str, str]  # requirement id -> PASS | FAIL | ...
    reason: str


def advertised(settings: Settings) -> list[str]:
    """r4's ``network`` statements in the baseline configuration, in file order."""
    text = (settings.repo_root / "lab" / "baseline" / "r4.eos").read_text()
    return _NETWORK.findall(text)


def combinations(prefixes: list[str], everything: bool) -> list[tuple[str, tuple[str, ...]]]:
    """Labelled combinations to try. The default four are chosen by knowing which statement is live
    (the one that is first in r4's config); ``everything`` tries every non-empty subset."""
    live, stale = prefixes[0], prefixes[1:]
    if everything:
        out = []
        for size in range(1, len(prefixes) + 1):
            for combo in itertools.combinations(prefixes, size):
                out.append((" + ".join(combo), combo))
        return out
    return [
        ("one stale", (stale[0],)),
        ("both stale", tuple(stale)),
        ("the live one", (live,)),
        ("all three", tuple(prefixes)),
    ]


def candidate_text(retired: tuple[str, ...]) -> str:
    lines = [*candidate.HEADER]
    lines[1] = "   " + lines[1]
    lines += [f"      no network {prefix}" for prefix in retired]
    return "\n".join(lines) + "\n"


def run(settings: Settings, log: Callable[[str], None], *, everything: bool = False,
        predict_fn: Callable[[Settings, predict.Inputs], Manifest] = predict.run) -> list[Row]:
    prefixes = advertised(settings)
    if len(prefixes) < 3:
        raise ValueError(f"expected r4 to advertise three prefixes, found {prefixes}")
    work = settings.state("matrix")
    work.mkdir(parents=True, exist_ok=True)
    reqs = settings.repo_root / "requirements" / "candidate.yml"
    base = settings.repo_root / "requirements" / "baseline.yml"
    rows = []
    combos = combinations(prefixes, everything)
    for number, (label, retired) in enumerate(combos, 1):
        path = work / f"candidate-{number}.eos"
        path.write_text(candidate_text(retired))
        log(f"  [{number}/{len(combos)}] predicting: retire {', '.join(retired)} ...")
        manifest = predict_fn(settings, predict.Inputs(path, reqs, base, {}))
        rows.append(Row(label, retired, manifest.status, {r["id"]: r["status"] for r in manifest.results}, manifest.reason))
    return rows


def table(rows: list[Row], ids: list[str]) -> str:
    """A fixed-width grid: one line per combination, one column per test."""
    short = [i.split("-")[0] for i in ids]
    width = max(len(r.label) for r in rows) + 2
    head = "retire".ljust(width) + "".join(s[:8].ljust(9) for s in short) + "verdict"
    lines = [head, "-" * len(head)]
    for r in rows:
        cells = ""
        for i in ids:
            status = r.results.get(i)
            cells += ("pass" if status == "PASS" else "FAIL" if status == "FAIL" else "-" if status is None else status.lower()[:8]).ljust(9)
        lines.append(r.label.ljust(width) + cells + r.status)
    return "\n".join(lines)


def requirement_ids(settings: Settings) -> list[str]:
    return [r.id for r in requirements.load(settings.repo_root / "requirements" / "candidate.yml", root=settings.repo_root).requirements]
