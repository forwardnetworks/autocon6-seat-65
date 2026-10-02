"""What deploy needs to know from GitHub, through the gh CLI the Codespace is signed in with."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class MergedPR:
    number: int
    head_sha: str
    merge_sha: str


def _gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GitHubError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()[:300]}")
    return result.stdout


def merged_pr(number: int) -> MergedPR:
    data = json.loads(_gh("pr", "view", str(number), "--json", "state,headRefOid,mergeCommit"))
    if data.get("state") != "MERGED":
        raise GitHubError(f"PR #{number} is {data.get('state', 'unknown').lower()}, not merged; approve and merge it first")
    return MergedPR(number, data["headRefOid"], (data.get("mergeCommit") or {}).get("oid", ""))


def check_passed(sha: str, name: str = "forward/predict") -> bool:
    """Whether the named check run concluded success on commit sha."""
    data = json.loads(_gh("api", f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs", "--jq", "{runs: [.check_runs[] | {name, conclusion}]}"))
    return any(run["name"] == name and run["conclusion"] == "success" for run in data.get("runs", []))


WORKFLOW = "forward-predict.yml"
ARTIFACT = "forward-predict-evidence"


def download_evidence(pr: MergedPR, destination: Path) -> Path:
    """The evidence manifest from the successful forward/predict run on the PR's final commit."""
    runs = json.loads(_gh("run", "list", "--workflow", WORKFLOW, "--commit", pr.head_sha, "--json", "databaseId,conclusion,status", "--limit", "20"))
    good = [r for r in runs if r.get("conclusion") == "success"]
    if not good:
        raise GitHubError(f"no successful forward/predict run on PR #{pr.number}'s final commit {pr.head_sha[:7]}")
    destination.mkdir(parents=True, exist_ok=True)
    for old in destination.glob("*"):
        old.unlink()
    _gh("run", "download", str(good[0]["databaseId"]), "--name", ARTIFACT, "--dir", str(destination))
    evidence = destination / "evidence.json"
    if not evidence.is_file():
        raise GitHubError(f"the forward/predict run for PR #{pr.number} left no evidence.json")
    return evidence
