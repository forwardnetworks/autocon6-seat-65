"""Report what happens in a seat's Codespace to the claim service's log (beta activity logging).

Standard library only, so the terminal hook can run it as a plain script (``python3 telemetry.py cmd ...``)
without the project environment. It does nothing unless the seat's Codespace secret ``TELEMETRY_KEY`` and
the service address ``AC6_TELEMETRY_URL`` are both set, never raises, never waits more than two seconds,
and masks anything that looks like a credential before it leaves the machine. The service masks it again.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

TIMEOUT = 2.0
MAX_COMMAND = 500

# Keep these in step with seatd/internal/telemetry/telemetry.go (tests/test_telemetry.py pins the behaviour).
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"), "***"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "***"),
    (re.compile(r"\bS[0-9]{2}-[A-Z0-9]{4}-[A-Z0-9]{4}\b", re.IGNORECASE), "***"),
    (re.compile(r"(authorization:\s*)(bearer|basic|token)?\s*\S+", re.IGNORECASE), r"\1***"),
    (
        re.compile(
            r"([A-Za-z0-9_.-]*(?:password|passwd|pwd|token|secret|api[_-]?key|credential)[A-Za-z0-9_.-]*)([=:]\s*|\s+)(\"[^\"]*\"|'[^']*'|\S+)",
            re.IGNORECASE,
        ),
        r"\1\2***",
    ),
    (re.compile(r"(--(?:pw|pass|password|token|secret|api-key)|-pw)(\s+|=)(\"[^\"]*\"|'[^']*'|\S+)", re.IGNORECASE), r"\1\2***"),
    (re.compile(r"\b[A-Za-z0-9+/_=-]{40,}\b"), "***"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _target() -> tuple[str, str, str] | None:
    url, key, seat = os.environ.get("AC6_TELEMETRY_URL", ""), os.environ.get("TELEMETRY_KEY", ""), os.environ.get("WORKSHOP_LAB_ID", "")
    if not (url and key and seat):
        return None
    return url.rstrip("/") + "/telemetry", key, seat


def payload(events: list[dict]) -> bytes:
    return json.dumps({"events": events}, separators=(",", ":")).encode()


def send(events: list[dict]) -> bool:
    """POST events; True if the service accepted them. Never raises."""
    target = _target()
    if target is None or not events:
        return False
    url, key, seat = target
    request = urllib.request.Request(
        url, data=payload(events), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {seat}.{key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return 200 <= response.status < 300
    except Exception:  # noqa: BLE001 - activity logging must never disturb the lab
        return False


def event(kind: str, cmd: str, **fields) -> dict:
    out = {"kind": kind, "t": int(time.time()), "cmd": redact(cmd)[:MAX_COMMAND]}
    out.update({k: v for k, v in fields.items() if v not in (None, "")})
    return out


def report_workshop(command: str, exit_code: int, started: float, statuses: dict[str, int]) -> None:
    status = next((name for name, code in statuses.items() if code == exit_code), "OK" if exit_code == 0 else str(exit_code))
    send([event("workshop", command, exit=exit_code, ms=int((time.time() - started) * 1000), status=status)])


def _main(argv: list[str]) -> int:
    # python3 telemetry.py cmd <exit> <cwd> <command line...>
    if len(argv) >= 5 and argv[1] == "cmd":
        try:
            code = int(argv[2])
        except ValueError:
            code = None
        cwd = os.path.relpath(argv[3], os.environ.get("AC6_ROOT", "/")) if os.environ.get("AC6_ROOT") and argv[3].startswith(os.environ["AC6_ROOT"]) else argv[3]
        send([event("cmd", " ".join(argv[4:]), exit=code, cwd=cwd)])
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
