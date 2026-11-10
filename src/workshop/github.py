"""What the workshop needs from GitHub, through its REST API (PyGithub) with the token the Codespace or the Actions run already has.

No ``gh`` subprocess: every question is one API call with a typed answer, so a failure is an error we can word, not a parse of
someone else's command output.
"""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class MergedPR:
    number: int
    head_sha: str
    merge_sha: str


WORKFLOW = "forward-predict.yml"
ARTIFACT = "forward-predict-evidence"


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        raise GitHubError("no GitHub token here (GITHUB_TOKEN): run this in your Codespace, where GitHub provides one")
    return token


def repo_name(root: Path | None = None) -> str:
    """owner/name of this seat repository: GITHUB_REPOSITORY (set in Codespaces and Actions), else the origin remote."""
    name = os.environ.get("GITHUB_REPOSITORY", "")
    if name:
        return name
    from workshop import gitrepo

    url = gitrepo.origin_url(root or Path.cwd())
    tail = url.rstrip("/").removesuffix(".git").replace(":", "/").split("/")
    if len(tail) < 2:
        raise GitHubError(f"cannot tell which GitHub repository this is from its origin ({url})")
    return f"{tail[-2]}/{tail[-1]}"


@cache
def _repo(name: str):
    from github import Auth, Github, GithubRetry

    return Github(auth=Auth.Token(_token()), timeout=20, retry=GithubRetry(total=3)).get_repo(name)


def repo(root: Path | None = None):
    from github import GithubException

    try:
        return _repo(repo_name(root))
    except GithubException as exc:
        raise GitHubError(f"GitHub refused the repository lookup ({exc.status}): {_message(exc)}") from exc


def _message(exc: Any) -> str:
    data = getattr(exc, "data", None)
    return (data.get("message") if isinstance(data, dict) else str(data or exc))[:200]


def _call(what: str, fn, *args, **kwargs):
    from github import GithubException

    try:
        return fn(*args, **kwargs)
    except GithubException as exc:
        raise GitHubError(f"{what} failed ({exc.status}): {_message(exc)}") from exc


def can_push(root: Path | None = None) -> str:
    """WRITE, MAINTAIN or ADMIN when this person can push to the seat repository; READ or NONE otherwise."""
    perms = repo(root).permissions
    if perms is None:
        return "NONE"
    return "ADMIN" if perms.admin else "MAINTAIN" if getattr(perms, "maintain", False) else "WRITE" if perms.push else "READ" if perms.pull else "NONE"


def merged_pr(number: int) -> MergedPR:
    pr = _call(f"reading PR #{number}", repo().get_pull, number)
    if not pr.merged:
        state = "open" if pr.state == "open" else "closed without merging"
        raise GitHubError(f"PR #{number} is {state}, not merged; approve and merge it first")
    return MergedPR(number, pr.head.sha, pr.merge_commit_sha or "")


_FINAL: dict[str, dict[str, dict[str, str]]] = {}  # head sha -> check name -> {conclusion, started}: a finished check never changes


def checks(sha: str) -> dict[str, dict[str, str]]:
    """Each check run on a commit: name -> {state (SUCCESS, FAILURE, IN_PROGRESS ...), started}. Finished sets are kept, so the
    lab page's polling asks GitHub again only while something is still running."""
    if sha in _FINAL:
        return _FINAL[sha]
    runs = _call(f"reading the checks on {sha[:7]}", lambda: list(repo().get_commit(sha).get_check_runs()))
    out = {r.name: {"state": (r.conclusion or r.status or "").upper(), "started": r.started_at.isoformat() if r.started_at else ""} for r in runs}
    if out and all(r.status == "completed" for r in runs):
        _FINAL[sha] = out
    return out


def check_passed(sha: str, name: str = "forward/predict") -> bool:
    """Whether the named check run concluded success on commit sha."""
    return checks(sha).get(name, {}).get("state") == "SUCCESS"


def pull_requests(limit: int = 10) -> list[dict[str, Any]]:
    """The newest pull requests, with each one's forward/predict and forward/advice check state."""
    out = []
    pulls = _call("listing pull requests", lambda: list(repo().get_pulls(state="all", sort="created", direction="desc")[:limit]))
    for pr in pulls:
        state = "MERGED" if pr.merged_at else pr.state.upper()
        out.append({"number": pr.number, "title": pr.title, "state": state, "url": pr.html_url, "sha": pr.head.sha,
                    "author": pr.user.login if pr.user else "", "branch": pr.head.ref})
    return out


def open_pull(branch: str) -> dict[str, Any] | None:
    r = repo()
    for pr in _call("looking for the pull request", lambda: list(r.get_pulls(state="open", head=f"{r.owner.login}:{branch}"))):
        return {"number": pr.number, "url": pr.html_url}
    return None


def create_pull(branch: str, title: str, body: str) -> dict[str, Any]:
    pr = _call("opening the pull request", repo().create_pull, base="main", head=branch, title=title, body=body)
    return {"number": pr.number, "url": pr.html_url}


def download_evidence(pr: MergedPR, destination: Path) -> Path:
    """The evidence manifest from the successful forward/predict run on the PR's final commit."""
    return _download(pr.number, pr.head_sha, destination, ("success",))


def download_pr_evidence(number: int, head_sha: str, destination: Path) -> Path:
    """The evidence from the finished forward/predict run on a PR's commit, whether it passed or failed. For showing people what the
    check found: deploy never uses this (it only accepts a successful run of a merged PR, via ``download_evidence``)."""
    return _download(number, head_sha, destination, ("success", "failure"))


def _download(number: int, head_sha: str, destination: Path, conclusions: tuple[str, ...]) -> Path:
    import requests

    r = repo()
    runs = _call("listing forward/predict runs", lambda: list(r.get_workflow(WORKFLOW).get_runs(head_sha=head_sha)[:20]))
    good = [run for run in runs if run.conclusion in conclusions]
    if not good:
        raise GitHubError(f"no finished ({'/'.join(conclusions)}) forward/predict run on PR #{number}'s final commit {head_sha[:7]}")
    artifact = next((a for a in _call("listing the run's artifacts", lambda: list(good[0].get_artifacts())) if a.name == ARTIFACT), None)
    if artifact is None:
        raise GitHubError(f"the forward/predict run for PR #{number} kept no {ARTIFACT} artifact")
    got = requests.get(artifact.archive_download_url, headers={"Authorization": f"Bearer {_token()}"}, timeout=60)
    if got.status_code != 200:
        raise GitHubError(f"downloading the evidence failed ({got.status_code})")
    destination.mkdir(parents=True, exist_ok=True)
    for old in destination.glob("*"):
        old.unlink()
    with zipfile.ZipFile(io.BytesIO(got.content)) as z:
        for member in z.namelist():
            if "/" not in member and not member.startswith("."):  # the artifact is flat: never write outside destination
                (destination / member).write_bytes(z.read(member))
    evidence = destination / "evidence.json"
    if not evidence.is_file():
        raise GitHubError(f"the forward/predict run for PR #{number} left no evidence.json")
    return evidence
