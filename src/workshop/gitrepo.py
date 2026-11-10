"""The git operations the workshop does for people (reset, the update check, the lab page's pull-request button), through GitPython.

GitPython gives each operation a call and a typed error (``GitCommandError`` carries git's own message). It drives git underneath,
the way netlab drives containerlab, which keeps the Codespace's credential helper working for fetch and push.
"""

from __future__ import annotations

from pathlib import Path


class GitError(RuntimeError):
    pass


def _repo(root: Path):
    from git import InvalidGitRepositoryError, NoSuchPathError, Repo

    try:
        return Repo(root)
    except (InvalidGitRepositoryError, NoSuchPathError) as exc:
        raise GitError(f"{root} is not a git repository") from exc


def _do(what: str, fn, *args, **kwargs):
    from git import GitCommandError

    try:
        return fn(*args, **kwargs)
    except GitCommandError as exc:
        detail = (exc.stderr or exc.stdout or "").strip().removeprefix("stderr:").strip().strip("'")
        raise GitError(f"{what} did not work: {detail[:240] or 'no message'}") from exc


def _origin(r):
    try:
        return r.remotes.origin
    except (AttributeError, IndexError) as exc:
        raise GitError("this repository has no 'origin' remote (it was not cloned from GitHub)") from exc


def origin_url(root: Path) -> str:
    return _origin(_repo(root)).url


def branch(root: Path) -> str:
    r = _repo(root)
    return "HEAD" if r.head.is_detached else r.active_branch.name


def dirty(root: Path) -> bool:
    return _repo(root).is_dirty(untracked_files=True)


def stash(root: Path, message: str) -> None:
    r = _repo(root)
    _do("keeping your changes in git stash", r.git.stash, "push", "--include-untracked", "-m", message)


def stashes(root: Path) -> list[str]:
    return [line for line in _repo(root).git.stash("list").splitlines() if line]


def switch(root: Path, name: str, *, create: bool = False) -> None:
    r = _repo(root)
    if create:
        _do(f"creating branch {name}", r.git.switch, "-c", name)
    else:
        _do(f"switching to {name}", r.git.switch, name)


def has_branch(root: Path, name: str) -> bool:
    return name in [h.name for h in _repo(root).heads]


def pull(root: Path) -> None:
    """Fast-forward the current branch from origin (never a merge commit)."""
    r = _repo(root)
    _do("pulling from GitHub", _origin(r).pull, ff_only=True)


def fetch_main(root: Path) -> None:
    _do("fetching main", _origin(_repo(root)).fetch, "main")


def changed_against_origin_main(root: Path, paths: tuple[str, ...]) -> list[str]:
    """Files under ``paths`` that differ between this checkout's HEAD and origin/main."""
    r = _repo(root)
    out = _do("comparing with origin/main", r.git.diff, "--name-only", "HEAD", "origin/main", "--", *paths)
    return [f for f in out.split() if f]


def commit_only(root: Path, path: str, message: str) -> bool:
    """Stage exactly ``path`` and commit it; False when it has no change to commit."""
    r = _repo(root)
    _do("staging the candidate", r.git.add, "--", path)
    if not r.git.diff("--cached", "--name-only", "HEAD", "--", path).strip():
        return False
    _do("committing", r.git.commit, "-m", message, "--", path)  # the pathspec: nothing else staged rides along
    return True


def push(root: Path) -> None:
    r = _repo(root)
    name = branch(root)
    _do("pushing the branch", _origin(r).push, refspec=f"HEAD:refs/heads/{name}", set_upstream=True)


def ahead_of_main(root: Path) -> int:
    r = _repo(root)
    return int(_do("comparing with main", r.git.rev_list, "--count", "origin/main..HEAD") or 0)
