"""Let GitHub Copilot (agent mode) use Forward's MCP server on this lab's network.

Forward serves its read-only analysis tools over MCP at ``/api/mcp`` (path traces, devices, interfaces, vulnerabilities,
NQE): the same tools Forward AI uses. Authentication is the seat's own API key and secret, sent as HTTP Basic, and every
tool call needs the network ID. ``workshop mcp`` writes both, from the same settings the Forward SDK reads, into two files
VS Code and Copilot pick up by themselves:

- ``.vscode/mcp.json``: the server, with the key in its Authorization header (0600);
- ``.github/copilot-instructions.md``: the network ID, and the rules of the day.

Both are gitignored, so the key can't be committed by accident. The workshop's own code never calls MCP: Copilot is the
client, the way the Forward UI is a client. Nothing here can change the network: every MCP tool is read-only.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

MCP_FILE = Path(".vscode/mcp.json")
INSTRUCTIONS_FILE = Path(".github/copilot-instructions.md")

INSTRUCTIONS = """# This lab, for GitHub Copilot

You are helping an attendee of the AutoCon6 workshop "Know Before You Deploy". `AGENTS.md` has the rules of the repository.

**Forward's MCP tools** (server `forward`) read Forward's model of this attendee's lab network: path traces (`get_paths`),
devices, interfaces, vulnerabilities, and NQE queries (`nqe`, `generate_nqe_query`). Every tool call needs this lab's
network ID:

    networkId: {network_id}

Leave `snapshotId` out and Forward uses the network's latest processed snapshot; pass one to be sure which. After a
prediction, `evidence/evidence.json` holds `baseline_snapshot_id` and `predicted_snapshot_id`. The lab: client 10.10.10.10 behind r1, service 10.20.20.20 behind r4, routers r1 to r4 in an eBGP diamond.

**Rules.** The tools are read-only and only advise: the requirements, judged by `workshop predict check`, decide. Don't
deploy, don't edit routers by hand, don't change `requirements/`, the workflow, or anything outside `candidate/`. When an
answer matters, say how to check it with the workshop's own commands (`workshop path`, `workshop nqe`, `workshop data`).
"""


def basic_auth(username: str, password: str) -> str:
    """The Authorization header value Forward's MCP server takes: HTTP Basic over the API key and secret."""
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def _exclude(repo_root: Path) -> None:
    """Also keep both files out of Git through .git/info/exclude, which no branch's .gitignore can undo."""
    exclude = repo_root / ".git" / "info" / "exclude"
    if not exclude.parent.is_dir():
        return
    have = exclude.read_text() if exclude.is_file() else ""
    missing = [str(p) for p in (MCP_FILE, INSTRUCTIONS_FILE) if str(p) not in have.splitlines()]
    if missing:
        with exclude.open("a") as fh:
            fh.write(("" if have.endswith("\n") or not have else "\n") + "\n".join(missing) + "\n")


def write(repo_root: Path, network_id: str, env: dict[str, str] | None = None) -> list[Path]:
    """Write the MCP server entry and Copilot's instructions for this lab. Returns the files written."""
    env = os.environ if env is None else env
    url = env.get("FORWARD_URL", "").strip().rstrip("/")
    user, secret = env.get("FORWARD_USERNAME", "").strip(), env.get("FORWARD_PASSWORD", "").strip()
    if not (url and user and secret and network_id):
        raise ValueError("needs FORWARD_URL, FORWARD_USERNAME, FORWARD_PASSWORD and FORWARD_NETWORK_ID (your seat's settings)")
    mcp = repo_root / MCP_FILE
    mcp.parent.mkdir(parents=True, exist_ok=True)
    conf: dict = {}
    try:  # keep any MCP servers the attendee added themselves; only ours is replaced
        conf = json.loads(mcp.read_text()) if mcp.is_file() and not mcp.is_symlink() else {}
    except (OSError, ValueError):
        conf = {}
    if not isinstance(conf, dict) or not isinstance(conf.get("servers"), dict):
        conf = {**(conf if isinstance(conf, dict) else {}), "servers": {}}
    conf["servers"]["forward"] = {"type": "http", "url": f"{url}/api/mcp", "headers": {"Authorization": basic_auth(user, secret)}}
    # owner-only before the key goes in (also when an older file had wider permissions), and never through a symlink
    fd = os.open(mcp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(conf, indent=2) + "\n")
    _exclude(repo_root)
    instructions = repo_root / INSTRUCTIONS_FILE
    instructions.parent.mkdir(parents=True, exist_ok=True)
    instructions.write_text(INSTRUCTIONS.format(network_id=network_id))
    return [mcp, instructions]
