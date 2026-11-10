"""Show your work: as each step runs, print the real tool doing it.

``workshop`` is a wrapper, and a wrapper that hides what it does teaches nothing. Every step prints the actual shell command,
Forward SDK call or NAPALM call it is making, in a form you could run or write yourself. The lines are indented and marked so
they read as "here is what is happening underneath", separate from the result.

    $ netlab up --no-config -p clab                                  a command run in a shell
    → Forward SDK  snapshots.upload(zip, note="autocon6:…")          a call through forward-python-sdk
    → NAPALM r4    load_merge_candidate → compare_config → commit   a call through NAPALM (eAPI) to a router

Display only: nothing here changes what a step does.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence

Log = Callable[[str], None] | None


def shell(log: Log, argv: Sequence[str], cwd: str = "") -> None:
    if log is not None:
        log(f"    $ {shlex.join(str(a) for a in argv)}" + (f"    (in {cwd})" if cwd else ""))


def sdk(log: Log, call: str) -> None:
    if log is not None:
        log(f"    → Forward SDK  {call}")


def napalm(log: Log, device: str, call: str) -> None:
    if log is not None:
        log(f"    → NAPALM {device:<4} {call}")


def note(log: Log, text: str) -> None:
    """A plain line of explanation at the same indent: why the call above matters."""
    if log is not None:
        log(f"      {text}")


NAPALM_APPLY = "open a config session · load_merge_candidate · compare_config (the router's diff) · commit_config (discard_config instead if the diff is empty or the router refuses)"
