"""The attendee's proposed change: one EOS CLI file, predicted and deployed as the same bytes.

``candidate/r4-bgp.eos`` may only retire or add BGP ``network`` statements on r4. The scope check
refuses anything else, so a pull request cannot slip an unrelated edit past the gate, and the
normalized lines are exactly what goes to Predict (``set_commands``) and to the router (Netmiko).
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path

DEVICE = "r4"
HEADER = ("router bgp 65004", "address-family ipv4")
_NETWORK = re.compile(r"^(no )?network (\S+)$")


class CandidateError(ValueError):
    """The candidate file is outside the change's allowed scope."""


@dataclass(frozen=True)
class Candidate:
    path: Path
    device: str
    lines: tuple[str, ...]  # normalized: no comments, no blank lines, no indentation
    sha256: str  # of the normalized text -- what Predict and Netmiko receive

    @property
    def text(self) -> str:
        return "\n".join(self.lines) + "\n"

    @property
    def changes(self) -> tuple[str, ...]:
        return self.lines[len(HEADER) :]


def normalize(raw: str) -> tuple[str, ...]:
    out = []
    for line in raw.splitlines():
        stripped = " ".join(line.split())
        if stripped and not stripped.startswith("!"):
            out.append(stripped)
    return tuple(out)


def load(path: str | Path) -> Candidate:
    file = Path(path)
    lines = normalize(file.read_text())
    problems = scope_problems(lines)
    if problems:
        raise CandidateError(f"{file}: " + "; ".join(problems))
    text = "\n".join(lines) + "\n"
    return Candidate(path=file, device=DEVICE, lines=lines, sha256=hashlib.sha256(text.encode()).hexdigest())


def scope_problems(lines: tuple[str, ...]) -> list[str]:
    if tuple(lines[: len(HEADER)]) != HEADER:
        return [f"must start with '{HEADER[0]}' then '{HEADER[1]}'"]
    changes = lines[len(HEADER) :]
    if not changes:
        return ["no changes: add 'no network <prefix>' lines for the advertisements to retire"]
    problems, seen = [], set()
    for line in changes:
        match = _NETWORK.match(line)
        if not match:
            problems.append(f"only '[no] network <prefix>' lines are allowed here, not {line!r}")
            continue
        try:
            prefix = str(ipaddress.ip_network(match.group(2), strict=True))
        except ValueError:
            problems.append(f"{match.group(2)!r} is not a network prefix like 10.20.30.0/24")
            continue
        if prefix in seen:
            problems.append(f"{prefix} appears twice")
        seen.add(prefix)
    return problems
