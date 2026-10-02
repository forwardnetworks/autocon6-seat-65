"""``workshop ui``: this seat's loop, drawn, in a browser tab inside the Codespace.

A small local web page (stdlib HTTP server on localhost; Codespaces forwards the port and opens it as a
preview). It shows where the lab is in the loop -- lab, baseline, change, prediction, pull request,
deploy, verify -- the four-router diamond with the service path, the prediction's requirement table, the
Forward AI advisers' output, and the sample prompts with an "ask" button.

It is read-mostly on purpose. The only action is asking Forward AI a question (advice). There is no
deploy button: deploying stays a typed ``workshop deploy ... --confirm`` in the terminal.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

from workshop import ai, candidate, progress
from workshop.config import Settings
from workshop.evidence import Manifest

ASSETS = resources.files("workshop") / "ui_assets"
MAX_QUESTION = 1000


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


class _Cached:
    """A value refreshed at most every ``ttl`` seconds (docker and gh calls are slow)."""

    def __init__(self, fn, ttl: float) -> None:
        self.fn, self.ttl, self.at, self.value, self.lock = fn, ttl, 0.0, None, threading.Lock()

    def get(self):
        with self.lock:
            if time.monotonic() - self.at > self.ttl:
                try:
                    self.value = self.fn()
                except Exception as exc:  # noqa: BLE001 - shown on the page, never fatal
                    self.value = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
                self.at = time.monotonic()
            return self.value


def _containers() -> dict[str, Any]:
    from workshop import lab

    states = lab.containers()
    return {"running": sum(1 for s in states.values() if s == "running"), "total": len(states), "states": states}


def _pull_requests(root: Path) -> list[dict[str, Any]]:
    out = subprocess.run(
        ["gh", "pr", "list", "--state", "all", "--limit", "5", "--json", "number,title,state,headRefOid,statusCheckRollup,url"],
        cwd=root, capture_output=True, text=True, timeout=20, check=False,
    )
    if out.returncode != 0:
        return []
    prs = []
    for pr in json.loads(out.stdout or "[]"):
        checks = {c.get("name"): (c.get("conclusion") or c.get("status") or "").upper() for c in pr.get("statusCheckRollup") or []}
        prs.append({"number": pr["number"], "title": pr["title"], "state": pr["state"], "url": pr["url"],
                    "predict": checks.get("forward/predict", ""), "advice": checks.get("forward/advice", "")})
    return prs


class Chats:
    """Forward AI conversations for the page, one question at a time (Forward answers one per user).

    Each thread is one Forward chat grounded in one snapshot; follow-ups go to the same chat, so Forward
    keeps the conversation. Threads are kept in the state dir, so a reload (or a restart) keeps them.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings, self.lock = settings, threading.Lock()
        self.path = settings.state("ai/chats.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.threads: list[dict[str, Any]] = (_read_json(self.path) or {}).get("threads", [])
        for thread in self.threads:  # a question cut off by a restart is not coming back
            for turn in thread["turns"]:
                if turn["status"] == "running":
                    turn["status"], turn["error"] = "error", "interrupted"

    def busy(self) -> bool:
        return any(turn["status"] == "running" for t in self.threads for turn in t["turns"])

    def ask(self, question: str, *, snapshot_id: str = "", label: str = "", thread_id: str = "") -> dict[str, Any]:
        with self.lock:
            if self.busy():
                raise RuntimeError("Forward AI is still answering your last question")
            if thread_id:
                thread = next((t for t in self.threads if t["id"] == thread_id), None)
                if thread is None:
                    raise LookupError("no such conversation")
                if not thread.get("chat_id"):
                    raise LookupError("that conversation never started; ask again as a new one")
            else:
                thread = {"id": uuid.uuid4().hex[:8], "chat_id": "", "snapshot": snapshot_id, "label": label,
                          "title": question[:80], "turns": []}
                self.threads.insert(0, thread)
            turn = {"question": question, "status": "running", "started": time.time()}
            thread["turns"].append(turn)
            self._save()
        threading.Thread(target=self._run, args=(thread, turn), daemon=True).start()
        return thread

    def _run(self, thread: dict[str, Any], turn: dict[str, Any]) -> None:
        from workshop.forward import Forward

        try:
            with Forward(self.settings) as fwd:
                if thread["chat_id"]:
                    answer = fwd.ai_follow_up(thread["chat_id"], turn["question"])
                else:
                    answer = fwd.ask_ai(turn["question"], thread["snapshot"])
                    thread["chat_id"] = answer.chat_id
            turn.update(status="done", answer=asdict(answer))
        except ai.AiUnavailable as exc:
            turn.update(status="error", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - reported on the page
            turn.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:200]}")
        turn["secs"] = round(time.time() - turn["started"])
        with self.lock:
            self._save()

    def _save(self) -> None:
        self.path.write_text(json.dumps({"threads": self.threads[:20]}, indent=1) + "\n")


class Board:
    """Builds the page's state from the same files the CLI writes."""

    def __init__(self, settings: Settings, evidence: Path) -> None:
        self.settings, self.evidence = settings, evidence
        self.containers = _Cached(_containers, 10)
        self.prs = _Cached(lambda: _pull_requests(settings.repo_root), 30)
        self.chats = Chats(settings)

    def snapshot_for(self, which: str) -> str:
        if which == "baseline":
            data = _read_json(self.settings.state_dir / "baseline.json")
            if data:
                return str(data["snapshot"])
        elif which == "predicted" and self.evidence.is_file():
            m = Manifest.read(self.evidence)
            if m.predicted_snapshot_id:
                return m.predicted_snapshot_id
        elif which == "post":
            data = _read_json(self.settings.state_dir / "verify.json")
            if data and data.get("post_snapshot_id"):
                return str(data["post_snapshot_id"])
        raise LookupError(f"there is no {which} snapshot yet")

    def state(self) -> dict[str, Any]:
        s = self.settings
        base = _read_json(s.state_dir / "baseline.json")
        deployed = _read_json(s.state_dir / "deploy.json")
        verified = _read_json(s.state_dir / "verify.json")
        m = Manifest.read(self.evidence) if self.evidence.is_file() else None
        advice = _read_json(self.evidence.parent / "ai.json")
        cand = self._candidate()
        lab = self.containers.get()
        prs = self.prs.get() or []
        return {
            "lab_id": s.lab_id, "network_id": s.network_id,
            "stages": _stages(lab, base, cand, m, prs, deployed, verified),
            "lab": lab, "baseline": base, "candidate": cand,
            "prediction": asdict(m) if m else None, "prediction_current": bool(m and cand.get("sha256") == m.candidate_sha256),
            "advice": advice, "prs": prs, "deploy": deployed, "verify": verified,
            "progress": progress.card(s, prs=prs, threads=self.chats.threads, evidence=self.evidence),
            "prompts": [asdict(p) for p in ai.sample_prompts(s.repo_root).values()],
            "threads": self.chats.threads[:12], "busy": self.chats.busy(), "intent": _intent(s.repo_root),
        }

    def _candidate(self) -> dict[str, Any]:
        path = self.settings.repo_root / "candidate" / "r4-bgp.eos"
        try:
            c = candidate.load(path)
            return {"ok": True, "changes": list(c.changes), "sha256": c.sha256, "problems": []}
        except candidate.CandidateError as exc:
            return {"ok": False, "changes": [], "sha256": "", "problems": [str(exc).split(": ", 1)[-1]]}
        except OSError:
            return {"ok": False, "changes": [], "sha256": "", "problems": ["candidate/r4-bgp.eos is missing"]}


def _intent(root: Path) -> str:
    try:
        return ai.intent(root)
    except (OSError, ValueError):
        return ""


def _stages(lab, base, cand, m, prs, deployed, verified) -> list[dict[str, str]]:
    """One entry per loop step: state is done | active | failed | todo, with a one-line detail."""

    def stage(key, label, state, detail, hint=""):
        return {"key": key, "label": label, "state": state, "detail": detail, "hint": hint}

    out = []
    running = lab.get("running", 0) if isinstance(lab, dict) else 0
    out.append(stage("lab", "Lab", "done" if running == 6 else ("active" if running else "todo"),
                     f"{running}/6 containers running" if running else "not started", "workshop up"))
    if base:
        ok = (base.get("report") or {}).get("status") == "PASS"
        out.append(stage("baseline", "Baseline", "done" if ok else "failed", f"snapshot {base['snapshot']}", "workshop baseline"))
    else:
        out.append(stage("baseline", "Baseline", "todo", "no baseline yet", "workshop baseline"))
    if cand.get("ok"):
        out.append(stage("change", "Change", "done", f"{len(cand['changes'])} line(s) in candidate/r4-bgp.eos", "edit, commit, open a PR"))
    else:
        out.append(stage("change", "Change", "active", (cand.get("problems") or ["edit the candidate"])[0], "edit candidate/r4-bgp.eos"))
    if m and cand.get("sha256") != m.candidate_sha256:
        out.append(stage("predict", "Predict", "todo", f"last prediction ({m.status}) was of a different candidate", "open a PR, or workshop predict"))
    elif m:
        state = {"PASS": "done", "FAIL": "failed"}.get(m.status, "failed")
        out.append(stage("predict", "Predict", state, f"{m.status}: {m.reason[:90]}", "workshop predict, or the PR check"))
    else:
        out.append(stage("predict", "Predict", "todo", "not predicted yet", "open a PR, or workshop predict"))
    pr = prs[0] if prs else None
    if pr and pr["state"] == "MERGED":
        out.append(stage("approve", "Approve", "done", f"PR #{pr['number']} merged", ""))
    elif pr and pr["state"] == "OPEN":
        check = pr["predict"] or "PENDING"
        out.append(stage("approve", "Approve", "active" if check != "FAILURE" else "failed", f"PR #{pr['number']} open, forward/predict {check.lower()}", "merge only a green check"))
    else:
        out.append(stage("approve", "Approve", "todo", "no pull request yet", "open a pull request"))
    out.append(stage("deploy", "Deploy", "done" if deployed else "todo",
                     f"applied {len(deployed.get('lines', [])) - 2} change(s)" if deployed else "not deployed", "workshop deploy --pr N --confirm <sha7>"))
    if verified:
        outcome = verified.get("outcome", "")
        out.append(stage("verify", "Verify", "done" if outcome == "MATCH" else "failed", outcome, "workshop verify"))
    else:
        out.append(stage("verify", "Verify", "todo", "not verified", "workshop verify"))
    # A step only counts once every step before it is done: a deploy or verify left over from an earlier
    # loop is history, not progress.
    for i, s in enumerate(out):
        if s["state"] == "done" and any(prev["state"] != "done" for prev in out[:i]):
            s["state"], s["detail"] = "todo", f"earlier loop: {s['detail']}"
    return out


def handler_for(board: Board) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "workshop-ui"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet
            return

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: Any) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, (ASSETS / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/logo.svg":
                self._send(HTTPStatus.OK, (ASSETS / "fn-logo.svg").read_bytes(), "image/svg+xml")
            elif self.path == "/api/state":
                self._json(HTTPStatus.OK, board.state())
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            # A custom header cannot be sent cross-site without a CORS preflight, which this server never
            # approves -- so only this page can start a question.
            if self.path != "/api/ask" or self.headers.get("X-Workshop-UI") != "1":
                self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
                return
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 8192)) or b"{}")
                prompts = ai.sample_prompts(board.settings.repo_root)
                sample = prompts.get(str(body.get("prompt", "")))
                question = (sample.prompt if sample else str(body.get("question", ""))).strip()[:MAX_QUESTION]
                if not question:
                    raise ValueError("ask a question")
                if body.get("thread"):
                    thread = board.chats.ask(question, thread_id=str(body["thread"]))
                else:
                    which = sample.snapshot if sample else str(body.get("snapshot", "baseline"))
                    thread = board.chats.ask(question, snapshot_id=board.snapshot_for(which),
                                             label=f"{sample.id} · {which}" if sample else which)
            except (ValueError, LookupError, RuntimeError) as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
                return
            self._json(HTTPStatus.ACCEPTED, {"thread": thread["id"]})

    return Handler


def serve(settings: Settings, evidence: Path, port: int, log) -> None:
    board = Board(settings, evidence)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler_for(board))
    log(f"workshop ui on http://localhost:{port} -- in a Codespace, open it from the PORTS tab (Ctrl+C stops it)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
