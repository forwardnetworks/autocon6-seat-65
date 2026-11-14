"""Every prediction you finish, kept: so iterating on a change leaves a trail you can read.

``workshop predict check`` overwrites ``evidence/evidence.json`` (deploy only ever accepts the newest). This module also keeps a small record of
each finished prediction in the seat's state folder, outside the repository, numbered in order: the candidate's lines, its hash, the verdict and
every requirement's result. ``workshop predict history`` and the lab page read it back and say what changed from one attempt to the next
("APP-EXISTING FAIL -> PASS"). Display only: nothing here is evidence, and deploy never reads it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FINAL = ("PASS", "FAIL", "ERROR", "INCONCLUSIVE", "STALE")
KEEP = 50


def folder(settings) -> Path:
    return settings.state_dir / "predictions"


def _change_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if re.match(r"\s*(no\s+)?network\s+\S+", line)]


def record(settings, m, *, out: str = "evidence", kind: str = "change") -> Path | None:
    """Keep one finished prediction. Unfinished ones (a change set waiting to be predicted) are not attempts."""
    if m.status not in FINAL:
        return None
    d = folder(settings)
    d.mkdir(parents=True, exist_ok=True)
    existing = sorted(d.glob("*.json"))
    n = int(existing[-1].stem) + 1 if existing else 1
    entry = {
        "n": n, "at": m.created_at, "kind": kind, "status": m.status, "reason": m.reason,
        "candidate": m.candidate_path, "sha7": (m.candidate_sha256 or "")[:7], "lines": _change_lines(m.candidate_text),
        "requirements": m.requirements_path, "baseline": str(m.baseline_snapshot_id), "predicted": str(m.predicted_snapshot_id),
        "results": {str(r.get("id")): str(r.get("status")) for r in m.results or []}, "out": out,
    }
    path = d / f"{n:04d}.json"
    path.write_text(json.dumps(entry, indent=1) + "\n")
    for old in existing[: max(0, len(existing) + 1 - KEEP)]:  # a long session keeps the newest KEEP
        old.unlink(missing_ok=True)
    return path


def entries(settings, limit: int = KEEP) -> list[dict[str, Any]]:
    out = []
    for p in sorted(folder(settings).glob("*.json"))[-limit:]:
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    for prev, cur in zip([None, *out], out, strict=False):
        cur["moved"], cur["prev"] = moved(prev, cur), (prev or {}).get("n")
    return out


def moved(prev: dict[str, Any] | None, cur: dict[str, Any]) -> list[str]:
    """What changed from the previous attempt: each requirement whose result moved, and the verdict."""
    if not prev:
        return []
    out = [f"{rid} {prev['results'][rid]} -> {status}" for rid, status in cur["results"].items()
           if rid in prev["results"] and prev["results"][rid] != status]
    return out


def what(lines: list[str]) -> str:
    """'retire 10.20.30.0/24, 10.20.40.0/24' or 'add back 10.20.20.0/24'."""
    retire = [line.split()[-1] for line in lines if line.startswith("no ")]
    add = [line.split()[-1] for line in lines if not line.startswith("no ")]
    parts = ([f"retire {', '.join(retire)}"] if retire else []) + ([f"add back {', '.join(add)}"] if add else [])
    return "; ".join(parts) or "no change lines"


def render(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["No predictions yet. Each `workshop predict check` you finish is kept here, so you can see how your change evolved."]
    lines = [f"{len(items)} prediction(s), oldest first:", ""]
    for e in items:
        head = f"  #{e['n']}  {e['at'][11:16]}  {e['status']:<12} {e['sha7']}  {what(e['lines'])}"
        if e.get("kind") != "change":
            head += f"  ({e['kind']})"
        lines.append(head)
        failing = [rid for rid, s in e["results"].items() if s != "PASS"]
        if failing:
            lines.append(f"        not PASS: {', '.join(failing)}")
        if e.get("moved"):
            lines.append(f"        since #{e['prev']}: {'; '.join(e['moved'])}")
    lines += ["", "Deploy only ever accepts the newest evidence (evidence/evidence.json). This list is your trail, not evidence."]
    return lines
