"""The evidence manifest: what was predicted, against what, with what result.

``workshop predict`` writes one per run (the PR check uploads it as an artifact); ``workshop
deploy`` refuses to write to the network unless a PASS manifest for the merged candidate, the
current baseline and the current requirements exists. Replay manifests can never deploy.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = 1

#: Process exit status for each outcome, shared by every command and the PR check.
EXIT = {"PASS": 0, "FAIL": 1, "ERROR": 2, "INCONCLUSIVE": 3, "STALE": 4}


@dataclass
class Manifest:
    lab_id: str
    network_id: str
    forward_release: str
    workshop_version: str
    candidate_path: str
    candidate_sha256: str
    candidate_text: str
    requirements_path: str
    requirements_sha256: str
    baseline_snapshot_id: str
    baseline_fingerprint: str
    baseline_processed_at: str
    baseline_status: str
    change_set_id: str = ""
    predicted_snapshot_id: str = ""
    status: str = "ERROR"  # overall: PASS | FAIL | ERROR | INCONCLUSIVE | STALE
    reason: str = ""
    results: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "live"  # live | replay -- replay is never deployable
    git: dict[str, Any] = field(default_factory=dict)  # repo, pr, head_sha, base_sha, run_id, actor
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    schema: int = SCHEMA

    @property
    def deployable(self) -> bool:
        return self.mode == "live" and self.status == "PASS" and bool(self.predicted_snapshot_id)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
        return path

    @classmethod
    def read(cls, path: Path) -> Manifest:
        data = json.loads(path.read_text())
        if data.get("schema") != SCHEMA:
            raise ValueError(f"{path}: unsupported evidence schema {data.get('schema')!r}")
        return cls(**data)


_MARK = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "ERROR": "⚠️ ERROR", "INCONCLUSIVE": "❔ INCONCLUSIVE", "STALE": "⏳ STALE"}


def markdown(m: Manifest) -> str:
    """The PR check summary: result first, then the evidence a reviewer needs."""
    lines = [f"## Forward Predict: {_MARK.get(m.status, m.status)}", ""]
    if m.reason:
        lines += [m.reason, ""]
    if m.deployable:
        target = f"--pr {m.git['pr']}" if m.git.get("pr") else "--evidence evidence/evidence.json"
        command = f"workshop deploy {target} --confirm {m.candidate_sha256[:7]}"
        lines += [f"After this pull request is merged, deploy it from your Codespace terminal: `{command}`", ""]
    if m.results:
        lines += ["| Requirement | Result | Expected | Predicted |", "|---|---|---|---|"]
        for r in m.results:
            lines.append(f"| **{r['id']}** — {r['title']} | {_MARK.get(r['status'], r['status'])} | {r['expected']} | {_cell(r['observed'])} |")
        lines.append("")
    lines += [
        "<details><summary>What was checked</summary>",
        "",
        f"- Lab `{m.lab_id}`, Forward network `{m.network_id}` (Forward {m.forward_release})",
        f"- Baseline snapshot `{m.baseline_snapshot_id}` ({m.baseline_status}), config fingerprint `{m.baseline_fingerprint[:12]}`",
        f"- Candidate `{m.candidate_path}` sha256 `{m.candidate_sha256[:12]}`",
        f"- Requirements `{m.requirements_path}` sha256 `{m.requirements_sha256[:12]}`",
        f"- Change set `{m.change_set_id or '-'}`, predicted snapshot `{m.predicted_snapshot_id or '-'}`",
        "",
        "```",
        m.candidate_text.rstrip(),
        "```",
        "</details>",
    ]
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")[:160]
