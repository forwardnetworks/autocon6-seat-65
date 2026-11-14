"""Every call the workshop makes to Forward, a router, Docker or GitHub, shown as it happens.

The hand-written lines in ``show.py`` explain *why* a step makes a call. This module shows *that* it made it, for every call, so
a new call can never go unannounced: the clients are wrapped, and each method call prints one line before it runs.

    → Forward SDK  nqe.query('select {…}', snapshot_id='1462089')
    → NAPALM r4    compare_config()
    → Docker SDK   clab-autocon6-client.exec_run(['nc', '-z', '-w', '3', '10.20.20.20', '8080'])
    → GitHub API   get_pulls(state='all')

Rules, because this sits in the middle of a working loop:
- Display only. A wrapped call returns exactly what the real one returned and raises exactly what it raised.
- It fails open. If printing breaks, the call still happens.
- Secrets are masked: arguments with a secret-looking name, and any value that equals a secret in the environment.
- A call a ``show.*`` line has just announced is not printed twice (see ``announced``).
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

QUIET = frozenset({"close"})  # lifecycle calls: kept on the lab page's feed, not printed
SLOW = 2.0  # seconds: a call slower than this gets a "took" line after it
MAXARG = 90  # characters per argument
FEED = 300  # events kept for the lab page

_lock = threading.Lock()
_sink: Callable[[str], None] | None = None
_listeners: list[Callable[[dict[str, Any]], None]] = []
_recent: deque[dict[str, Any]] = deque(maxlen=FEED)
_seq = 0
_announced: dict[tuple[str, str], float] = {}

_SECRET_NAME = re.compile(r"token|secret|passw|authorization|api[_-]?key|credential", re.IGNORECASE)
_SECRET_ENV = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|CREDENTIAL", re.IGNORECASE)


def enabled() -> bool:
    return os.environ.get("WORKSHOP_TRACE", "1") not in ("0", "false", "no", "off")


def set_sink(fn: Callable[[str], None] | None) -> None:
    """Where trace lines are printed (the CLI's ``log``). None silences printing; listeners and the feed still get every call."""
    global _sink
    _sink = fn


def add_listener(fn: Callable[[dict[str, Any]], None]) -> None:
    _listeners.append(fn)


def recent(since: int = 0) -> list[dict[str, Any]]:
    """Calls newer than ``since`` (an event id), oldest first: what the lab page shows under 'what just ran'."""
    with _lock:
        return [e for e in _recent if e["id"] > since]


def reset() -> None:
    """For tests."""
    global _sink, _seq
    with _lock:
        _sink, _seq = None, 0
        _listeners.clear()
        _recent.clear()
        _announced.clear()


# ---- what the hand-written show.* lines tell us, so one call is not printed twice ----

def announced(kind: str, call: str) -> None:
    """``show.sdk(log, "devices.file(...)")`` calls this: the next calls to that method, for a few seconds, are already on screen."""
    m = re.match(r"\s*([A-Za-z_][\w.]*)\(", call)
    if m:
        with _lock:
            _announced[(kind, m.group(1))] = time.monotonic()


def _covered(kind: str, path: str) -> bool:
    with _lock:
        t = _announced.get((kind, path))
    return t is not None and time.monotonic() - t < 4.0


# ---- formatting ----

def _secrets() -> list[str]:
    return [v for k, v in os.environ.items() if v and len(v) >= 6 and _SECRET_ENV.search(k)]


def scrub(text: str) -> str:
    for s in _secrets():
        text = text.replace(s, "***")
    return text


def _short(v: Any) -> str:
    if isinstance(v, str):
        if "\n" in v:
            first = next((ln.strip() for ln in v.splitlines() if ln.strip() and not ln.strip().startswith(("//", "#"))), "")
            return f"'{first[:48]}…' ({len(v.splitlines())} lines)"
        r = repr(v)
        return r if len(r) <= MAXARG else r[: MAXARG - 4] + "…'"
    if isinstance(v, dict | list | tuple | set):
        r = repr(v)
        return r if len(r) <= MAXARG else f"<{type(v).__name__} of {len(v)}>"
    if isinstance(v, bytes | bytearray):
        return f"<{len(v)} bytes>"
    r = repr(v)
    return r if len(r) <= MAXARG else f"<{type(v).__name__}>"


def format_args(args: tuple, kwargs: dict) -> str:
    parts = [_short(a) for a in args]
    for k, v in kwargs.items():
        parts.append(f"{k}={'***' if _SECRET_NAME.search(k) else _short(v)}")
    return ", ".join(parts)


# ---- emitting ----

def emit(kind: str, text: str, *, ms: float | None = None, ok: bool = True) -> None:
    global _seq
    try:
        text = scrub(text)
        with _lock:
            _seq += 1
            event = {"id": _seq, "at": time.time(), "kind": kind, "text": text, "ms": ms, "ok": ok}
            _recent.append(event)
        for fn in _listeners:
            try:
                fn(event)
            except Exception:  # noqa: BLE001, S110 - display only
                pass
    except Exception:  # noqa: BLE001, S110 - display only
        pass


def _print(line: str) -> None:
    sink = _sink
    if sink is None or not enabled():
        return
    try:
        sink(scrub(line))
    except Exception:  # noqa: BLE001, S110 - display only
        pass


def _label(kind: str, target: str = "") -> str:
    """'Forward SDK  ' or 'NAPALM r4    ': the same columns as the hand-written show.* lines."""
    head = f"{kind} {target:<4} " if target else f"{kind:<13}"
    return head if len(head) >= 13 else head.ljust(13)


# ---- the wrapper ----

def _run(fn: Callable, kind: str, target: str, path: str, args: tuple, kwargs: dict) -> Any:
    try:
        text = f"{path}({format_args(args, kwargs)})"
        show = not _covered(kind, path) and path.rsplit(".", 1)[-1] not in QUIET
        if show:
            _print(f"    → {_label(kind, target)}{text}")
    except Exception:  # noqa: BLE001 - display only
        text, show = path + "(…)", False
    started = time.monotonic()
    try:
        result = fn(*args, **kwargs)
    except BaseException as exc:
        took = time.monotonic() - started
        emit(kind, (target + " " if target else "") + text, ms=took * 1000, ok=False)
        if show and took >= SLOW:
            _print(f"      … failed after {took:.1f} s: {type(exc).__name__}")
        raise
    took = time.monotonic() - started
    emit(kind, (target + " " if target else "") + text, ms=took * 1000)
    if show and took >= SLOW:
        _print(f"      … took {took:.1f} s")
    return result


class Traced:
    """A transparent proxy over a client. Attribute reads pass through; method calls announce themselves first."""

    __slots__ = ("_after", "_kind", "_o", "_path", "_roots", "_target")

    def __init__(self, obj: Any, kind: str, path: str = "", roots: tuple[str, ...] = (), after: dict[str, Callable] | None = None, target: str = "") -> None:
        object.__setattr__(self, "_o", obj)
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_roots", roots)
        object.__setattr__(self, "_after", after or {})

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._o, name)
        if name.startswith("_"):
            return value
        path = f"{self._path}.{name}" if self._path else name
        try:
            if self._roots and not isinstance(value, type) and type(value).__module__.split(".")[0] in self._roots:
                # an SDK object (a resource, a collection): look inside it. Checked before "callable": Docker's collections define __call__.
                return Traced(value, self._kind, path, self._roots, self._after, self._target)
            if callable(value) and not isinstance(value, type):
                after = self._after.get(path)

                def call(*args: Any, **kwargs: Any) -> Any:
                    result = _run(value, self._kind, self._target, path, args, kwargs)
                    if after is not None:
                        try:
                            return after(result)
                        except Exception:  # noqa: BLE001 - never let display change a result
                            return result
                    return result

                call.__name__ = name
                return call
        except Exception:  # noqa: BLE001 - fail open
            return value
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._o, name, value)

    def __enter__(self) -> Any:
        entered = self._o.__enter__()
        return self if entered is self._o else entered

    def __exit__(self, *exc: object) -> Any:
        return self._o.__exit__(*exc)

    def __repr__(self) -> str:
        return repr(self._o)


# ---- one wrapper per tool ----

def forward(client: Any) -> Any:
    """A Forward SDK client: every ``client.<area>.<method>(...)`` shows."""
    return Traced(client, "Forward SDK", roots=("forward_sdk",))


def napalm(driver: Any, host: str) -> Any:
    """A NAPALM driver for one router: open, load_merge_candidate, compare_config, commit_config, cli, ..."""
    return Traced(driver, "NAPALM", roots=("napalm", "pyeapi"), target=host)


def docker(client: Any) -> Any:
    """A Docker SDK client. A container fetched with ``containers.get(name)`` is wrapped too, so its ``exec_run`` shows."""

    def container(result: Any) -> Any:
        name = getattr(result, "name", None) or "container"
        return Traced(result, "Docker SDK", str(name), roots=("docker",))

    return Traced(client, "Docker SDK", roots=("docker",), after={"containers.get": container})


def github(repo: Any) -> Any:
    """A PyGithub repository: ``get_pulls``, ``get_workflow_runs`` and the rest."""
    return Traced(repo, "GitHub API", roots=("github",))
