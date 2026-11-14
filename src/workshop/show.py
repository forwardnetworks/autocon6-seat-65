"""Show your work: as each step runs, print the real tool doing it.

``workshop`` is a wrapper, and a wrapper that hides what it does teaches nothing. Every step prints the actual shell command,
Forward SDK call or NAPALM call it is making, in a form you could run or write yourself. The lines are indented and marked so
they read as "here is what is happening underneath", separate from the result.

    $ netlab up -p clab                                              a command run in a shell
    → Forward SDK  snapshots.upload(zip, note="autocon6:…")          a call through forward-python-sdk
    → NAPALM r4    load_merge_candidate → compare_config → commit   a call through NAPALM (eAPI) to a router

Display only: nothing here changes what a step does.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence

from workshop import trace

Log = Callable[[str], None] | None


def shell(log: Log, argv: Sequence[str], cwd: str = "") -> None:
    if log is not None:
        log(f"    $ {shlex.join(str(a) for a in argv)}" + (f"    (in {cwd})" if cwd else ""))


def sdk(log: Log, call: str) -> None:
    if log is not None:
        trace.announced("Forward SDK", call)
        log(f"    → Forward SDK  {call}")


def netlab(log: Log, args: list[str]) -> None:
    if log is not None:
        log(f"    → netlab       netsim.cli.{args[0]}.run({args[1:]!r})   the code behind `netlab {' '.join(args)}`")


def git(log: Log, call: str) -> None:
    if log is not None:
        log(f"    → git          {call}   (GitPython)")


def docker(log: Log, container: str, call: str) -> None:
    if log is not None:
        trace.announced("Docker SDK", f"{container}.{call}")
        log(f"    → Docker SDK   {container}: {call}")


def napalm(log: Log, device: str, call: str) -> None:
    if log is not None:
        trace.announced("NAPALM", call)
        log(f"    → NAPALM {device:<4} {call}")


def note(log: Log, text: str) -> None:
    """A plain line of explanation at the same indent: why the call above matters."""
    if log is not None:
        log(f"      {text}")


NAPALM_APPLY = "open a config session · load_merge_candidate · compare_config (the router's diff) · commit_config (discard_config instead if the diff is empty or the router refuses)"


class Heartbeat:
    """A wait that prints nothing looks hung. ``beat()`` (call it as often as you like) prints one line at most every ``every``
    seconds: what is being waited for and for how long. ``quiet()`` returns that line from a thread when nothing else has
    printed for a while (for a subprocess whose own output stalls)."""

    def __init__(self, log: Log, what: str, *, every: float = 20.0, usual: float = 0, give_up: float = 0, clock=None) -> None:
        """``usual``: past this many seconds, say it is taking longer than usual (not "nothing is stuck"); ``give_up``: past
        this, say when to ask for help. A heartbeat must never promise more than it knows."""
        import time

        self.log, self.what, self.every, self.usual, self.give_up = log, what, every, usual, give_up
        self.clock = clock or time.monotonic
        self.start = self.last = self.clock()

    def beat(self, detail: str = "") -> None:
        now = self.clock()
        if self.log is None or now - self.last < self.every:
            return
        self.last = now
        secs = now - self.start
        if self.give_up and secs > self.give_up:
            tail = "This is much longer than usual. If nothing changes in the next few minutes, raise a hand (or run `workshop doctor` in a second terminal)."
        elif self.usual and secs > self.usual:
            tail = f"Longer than usual (normally under {self.usual / 60:.0f} minutes): a busy Codespace is slower. Still working."
        else:
            tail = "Nothing is stuck."
        self.log(f"  … still {self.what} ({secs:.0f} s){': ' + detail if detail else ''}. {tail}")

    def heard(self) -> None:
        """Something else just printed: the next beat can wait a full interval."""
        self.last = self.clock()
