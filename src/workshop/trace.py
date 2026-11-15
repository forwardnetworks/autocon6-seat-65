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
  Email addresses are masked too (Forward returns its users' emails, such as a snapshot's ``creator``).
- A call a ``show.*`` line has just announced is not printed twice (see ``announced``).

Under each Forward SDK call, the HTTP it made (``HttpHooks``, the SDK's own observation hooks): method, path, request body,
status, time, size and the start of the response, masked like everything else. It is always on the lab page's feed; the
terminal shows one line per request only with ``WORKSHOP_TRACE=http``, and a step the lab page runs prints it as
``MARK`` lines the page folds under the call (``WORKSHOP_HTTP_MARKS=1``, set by the page).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, unquote

from forward_sdk.telemetry import Hooks

QUIET = frozenset({"close", "processing_queue", "progress"})  # lifecycle and polling calls: kept on the feed, not printed (a wait polls every few seconds)
SLOW = 2.0  # seconds: a call slower than this gets a "took" line after it
MAXARG = 90  # characters per argument
FEED = 300  # events kept for the lab page
HTTP_BODY = 2048  # characters of a request body shown
HTTP_LINES, HTTP_BYTES = 40, 3072  # the start of a response shown
HTTP_PARSE = 256 * 1024  # a JSON response larger than this is shown as raw text, not re-indented
HTTP_PER_CALL = 12  # requests kept per call (an NQE query can page many times); the rest are counted
MARK = "::workshop-http::"  # a machine-readable line the lab page reads and folds under the call above it

_lock = threading.Lock()
_sink: Callable[[str], None] | None = None
_listeners: list[Callable[[dict[str, Any]], None]] = []
_recent: deque[dict[str, Any]] = deque(maxlen=FEED)
_seq = 0
_announced: dict[tuple[str, str], float] = {}
_local = threading.local()  # the SDK calls running on this thread (a stack), so each HTTP request attaches to its call
_hinted = False

_SECRET_NAME = re.compile(r"token|secret|passw|authorization|api[_-]?key|credential", re.IGNORECASE)
_SECRET_ENV = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|CREDENTIAL", re.IGNORECASE)


def enabled() -> bool:
    return os.environ.get("WORKSHOP_TRACE", "1") not in ("0", "false", "no", "off")


def http_mode() -> str:
    """'marks' under the lab page, 'lines' with WORKSHOP_TRACE=http, '' (the feed only) otherwise."""
    if not enabled():
        return ""
    if os.environ.get("WORKSHOP_HTTP_MARKS") == "1":
        return "marks"
    return "lines" if os.environ.get("WORKSHOP_TRACE", "").strip().lower() == "http" else ""


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
    global _sink, _seq, _hinted
    with _lock:
        _sink, _seq, _hinted = None, 0, False
        _local.__dict__.clear()
        _listeners.clear()
        _recent.clear()
        _announced.clear()


# ---- what the hand-written show.* lines tell us, so one call is not printed twice ----

def announced(kind: str, call: str) -> None:
    """``show.sdk(log, "devices.file(...)")`` calls this: the next calls to that method, for a few seconds, are already on screen."""
    m = re.match(r"\s*([A-Za-z_][\w.-]*)\(", call)
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


_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def scrub(text: str) -> str:
    for s in _secrets():
        text = text.replace(s, "***")
    return _EMAIL.sub("***@***", text)


def _short(v: Any) -> str:
    if isinstance(v, str):
        v = scrub(v)  # before any cut, so a cut never leaves half of something masked
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

def emit(kind: str, text: str, *, ms: float | None = None, ok: bool = True, http: list[dict[str, Any]] | None = None, http_more: int = 0) -> None:
    global _seq
    try:
        text = scrub(text)
        with _lock:
            _seq += 1
            event = {"id": _seq, "at": time.time(), "kind": kind, "text": text, "ms": ms, "ok": ok}
            if http:
                event["http"] = [_public(h) for h in http]
                if http_more:
                    event["http_more"] = http_more
            _recent.append(event)
        for fn in _listeners:
            try:
                fn(event)
            except Exception:  # noqa: BLE001, S110 - display only
                pass
    except Exception:  # noqa: BLE001, S110 - display only
        pass


def _print(line: str, *, clean: bool = False) -> None:
    sink = _sink
    if sink is None or not enabled():
        return
    try:
        sink(line if clean else scrub(line))
    except Exception:  # noqa: BLE001, S110 - display only
        pass


def _label(kind: str, target: str = "") -> str:
    """'Forward SDK  ' or 'NAPALM r4    ': the same columns as the hand-written show.* lines."""
    head = f"{kind} {target:<4} " if target else f"{kind:<13}"
    return head if len(head) >= 13 else head.ljust(13)


# ---- the wrapper ----

def _run(fn: Callable, kind: str, target: str, path: str, args: tuple, kwargs: dict) -> Any:
    call: dict[str, Any] = {"http": [], "more": 0, "pending": None, "print": False}
    try:
        text = f"{path}({format_args(args, kwargs)})"
        quiet = path.rsplit(".", 1)[-1] in QUIET
        show = not _covered(kind, path) and not quiet
        call["print"] = not quiet  # a call a show.* line announced still gets its HTTP lines, under that line
        if show:
            _print(f"    → {_label(kind, target)}{text}")
    except Exception:  # noqa: BLE001 - display only
        text, show = path + "(…)", False
    stack = _calls()
    stack.append(call)
    started = time.monotonic()
    try:
        result = fn(*args, **kwargs)
    except BaseException as exc:
        took = time.monotonic() - started
        _unstack(stack, call, f"no response: the call raised {type(exc).__name__}")
        emit(kind, (target + " " if target else "") + text, ms=took * 1000, ok=False, http=call["http"], http_more=call["more"])
        if show and took >= SLOW:
            _print(f"      … failed after {took:.1f} s: {type(exc).__name__}")
        raise
    took = time.monotonic() - started
    _unstack(stack, call, "streamed: the SDK reads this body as it arrives, so the trace does not")
    emit(kind, (target + " " if target else "") + text, ms=took * 1000, http=call["http"], http_more=call["more"])
    if show and took >= SLOW:
        _print(f"      … took {took:.1f} s")
    return result


def _calls() -> list[dict[str, Any]]:
    stack = getattr(_local, "calls", None)
    if stack is None:
        stack = _local.calls = []
    return stack


def _unstack(stack: list[dict[str, Any]], call: dict[str, Any], why: str) -> None:
    """The call is over. A request still waiting for its response never got one the hooks saw (a stream, or a failure): say so."""
    try:
        _finish(call, why)
        if stack and stack[-1] is call:
            stack.pop()
        elif call in stack:
            stack.remove(call)
    except Exception:  # noqa: BLE001, S110 - display only
        pass


# ---- the HTTP under each Forward SDK call ----

def _public(h: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in h.items() if not k.startswith("_")}


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    return f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def mask(value: Any) -> Any:
    """A JSON value with every secret-named key's value replaced by ``***``."""
    if isinstance(value, dict):
        return {k: ("***" if isinstance(k, str) and _SECRET_NAME.search(k) else mask(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    return value


def _clip(text: str, lines: int, chars: int) -> str:
    rows = text.splitlines()
    out = "\n".join(rows[:lines])
    cut = len(rows) > lines
    if len(out) > chars:
        out, cut = out[:chars], True
    if cut:
        out += f"\n… ({len(rows)} lines, {len(text)} characters in all; the rest is not shown)"
    return out


def _binary(ctype: str, data: bytes) -> bool:
    if any(t in ctype for t in ("json", "text", "xml", "javascript", "x-www-form-urlencoded")):
        return False
    if any(t in ctype for t in ("zip", "octet-stream", "gzip", "multipart", "image/", "tar")):
        return True
    try:
        data[:512].decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def body_excerpt(data: bytes, ctype: str, *, lines: int, chars: int) -> str:
    """What a body shows: pretty JSON with secret-named keys masked, text, or only its size; always scrubbed of secret values and email addresses."""
    if not data:
        return ""
    if _binary(ctype, data):
        return f"<binary, {len(data)} bytes>"
    text = data.decode("utf-8", errors="replace")
    if ("json" in ctype or text.lstrip()[:1] in ("{", "[")) and len(data) <= HTTP_PARSE:
        try:
            text = json.dumps(mask(json.loads(text)), indent=2, ensure_ascii=False)
        except ValueError:
            pass
    return scrub(_clip(scrub(text), lines, chars))  # before the clip too, so a cut never leaves half a secret


def _target(url: Any) -> str:
    """Path and query, the host left off, secret-named query values masked."""
    path = unquote(url.path)
    query = url.query.decode() if isinstance(url.query, bytes) else str(url.query)
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        path += "?" + "&".join(f"{k}={'***' if _SECRET_NAME.search(k) else v}" for k, v in pairs)
    return scrub(path)


def http_line(h: dict[str, Any]) -> str:
    """One request as the terminal shows it: ``POST /api/networks/1/paths?… → 200 · 412 ms · 3.1 KB``."""
    if h.get("retry"):
        return f"↻ retry, attempt {h['attempt']} in {h['delay']:.1f} s ({h['reason']})"
    target = h["path"] if len(h["path"]) <= 96 else h["path"][:95] + "…"
    if h.get("status") is None:
        return f"{h['method']} {target} → {h.get('note') or 'no response'}"
    return f"{h['method']} {target} → {h['status']} · {h['ms']:.0f} ms · {_size(h['size'])}"


def _out(call: dict[str, Any] | None, h: dict[str, Any]) -> None:
    global _hinted
    if call is None or not call["print"]:
        return
    if not any(k is h for k in call["http"]):
        return  # past the per-call cap (a long wait polls every few seconds): counted on the feed, not printed line by line
    mode = http_mode()
    if mode == "marks":
        _print(MARK + json.dumps(_public(h), ensure_ascii=False, separators=(",", ":")), clean=True)  # every field is already masked; masking the JSON again could break it
    elif mode == "lines":
        _print("      " + http_line(h))
        if not _hinted:
            _hinted = True
            _print("      (bodies are not printed here: run the step from the lab page and open the call in its Output)")


def _keep(call: dict[str, Any] | None, h: dict[str, Any]) -> None:
    if call is None:
        return
    if len(call["http"]) < HTTP_PER_CALL:
        call["http"].append(h)
    else:
        call["more"] += 1


def _finish(call: dict[str, Any] | None, note: str) -> None:
    """A request that will get no response the hooks see: say why, and show it."""
    if call is None or call["pending"] is None:
        return
    h, call["pending"] = call["pending"], None
    h["note"] = note
    _out(call, h)


class HttpHooks(Hooks):
    """The SDK's own observation hooks (``ForwardClient(hooks=...)``): every request a traced SDK call makes, attached to that call.

    Read-only: no header but Content-Type is kept (never Authorization), and a body the SDK has not read (a streamed upload or
    download) is never read here, only its size. Every hook fails open; the SDK also ignores a hook that raises."""

    @staticmethod
    def _current() -> dict[str, Any] | None:
        stack = getattr(_local, "calls", None)
        return stack[-1] if stack else None

    def on_request(self, operation: Any, request: Any) -> None:
        try:
            call = self._current()
            if call is None:
                return  # a request outside a traced call (none today): nothing to attach it to
            _finish(call, "no response")
            ctype = request.headers.get("content-type", "")
            try:
                body = body_excerpt(request.content, ctype, lines=10**6, chars=HTTP_BODY)
            except Exception:  # noqa: BLE001 - a streamed upload (a snapshot zip, multipart): its size only, never read here
                n = request.headers.get("content-length")
                body = f"<binary upload, {n} bytes>" if n else "<streamed upload>"
            h = {"method": request.method, "path": _target(request.url), "op": getattr(operation, "operation_id", None),
                 "req_type": ctype.split(";")[0], "req_body": body, "status": None}
            call["pending"] = h
            _keep(call, h)
        except Exception:  # noqa: BLE001, S110 - display only
            pass

    def on_response(self, operation: Any, response: Any, elapsed: float) -> None:
        try:
            call = self._current()
            if call is None or call["pending"] is None:
                return
            h, call["pending"] = call["pending"], None
            ctype = response.headers.get("content-type", "")
            h.update(status=response.status_code, ms=round(elapsed * 1000, 1), type=ctype.split(";")[0])
            try:
                data = response.content  # read already (the SDK's send() is not streamed); an unread stream raises, never reads
            except Exception:  # noqa: BLE001
                n = response.headers.get("content-length", "")
                h.update(size=int(n) if n.isdigit() else 0, body="<streamed: read by the SDK, not by the trace>")
            else:
                h.update(size=len(data), body=body_excerpt(data, ctype, lines=HTTP_LINES, chars=HTTP_BYTES))
            _out(call, h)
        except Exception:  # noqa: BLE001, S110 - display only
            pass

    def on_retry(self, operation: Any, attempt: int, delay: float, reason: str) -> None:
        try:
            call = self._current()
            if call is None:
                return
            _finish(call, f"no response ({scrub(str(reason))})")
            h = {"retry": True, "attempt": attempt + 2, "delay": round(float(delay), 2), "reason": scrub(str(reason))}
            _keep(call, h)
            _out(call, h)
        except Exception:  # noqa: BLE001, S110 - display only
            pass


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
