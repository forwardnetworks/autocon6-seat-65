"""``workshop ui``: this seat's loop, drawn, in a browser tab inside the Codespace.

A small local web page (stdlib HTTP server on localhost; Codespaces forwards the port and opens it as a
preview). It shows where the lab is in the loop -- lab, baseline, change, prediction, pull request,
deploy, verify -- the four-router diamond with the service path, the prediction's requirement table, the
Forward AI advisers' output, and the sample prompts with an "ask" button.

It runs the loop's steps for people who would rather click than type: each button starts the very same
``workshop`` command a terminal would (one at a time, output streamed to the page), so there is no second
code path. Deploying still needs the person to type the first 7 characters of the candidate hash, exactly
as ``workshop deploy --confirm`` does. It can also write the candidate file from a checklist of the prefixes
on r4, and ask Forward AI a question (advice).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from workshop import ai, candidate, progress
from workshop.config import Settings
from workshop.evidence import Manifest

ASSETS = resources.files("workshop") / "ui_assets"
MAX_QUESTION = 1000
MAX_LINES = 1500
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SHA7 = re.compile(r"^[0-9a-f]{7}$")
# The prefixes r4 advertises; the change builder may only retire these.
R4_PREFIXES = ("10.20.20.0/24", "10.20.30.0/24", "10.20.40.0/24")
# What the page may start. Anything else (down, collector, agent...) stays a typed command.
STEPS = ("doctor", "up", "baseline", "probe", "predict", "explain", "propose", "deploy", "verify", "restore", "status")
MAX_ARTIFACT = 60_000
# What each step uses and produces, shown on the page so people see exactly what they are deploying. Paths are
# repo-relative; "state:" paths live in the seat's state folder; "forward:<which>" is Forward's raw answers.
ARTIFACTS: dict[str, list[tuple[str, str, str, bool]]] = {
    "lab": [("The topology netlab builds: four routers, a client, a service and their links", "lab/topology.yml", "yaml", True),
            ("r4's baseline configuration, as NAPALM applies it", "lab/baseline/r4.eos", "text", False)],
    "baseline": [("What must stay true: the baseline requirements", "requirements/baseline.yml", "yaml", True),
                 ("The NQE query that finds stale advertisements", "nqe/stale_bgp_advertisements.nqe", "text", False),
                 ("What Forward said about the baseline (structured)", "forward:baseline", "json", False)],
    "change": [("Why: the change request", "intent.md", "text", False),
               ("The change you are making (the exact lines Predict judges and the router receives)", "candidate/r4-bgp.eos", "text", True)],
    "predict": [("The change being predicted", "candidate/r4-bgp.eos", "text", True),
                ("The requirements the prediction is judged against", "requirements/candidate.yml", "yaml", False),
                ("The verdict, as evidence.json", "evidence:", "json", False),
                ("What Forward said about the predicted network (structured)", "forward:predicted", "json", False)],
    "approve": [("The pipeline that runs on your pull request", ".github/workflows/forward-predict.yml", "yaml", True)],
    "deploy": [("The change that will be sent to r4", "candidate/r4-bgp.eos", "text", True),
               ("What was deployed, with the router's own diff", "state:deploy.json", "json", True)],
    "verify": [("The result compared with the prediction", "state:verify.json", "json", True),
               ("What Forward said about the real network afterwards (structured)", "forward:post", "json", False)],
}
EXIT_WORDS = {0: "PASS", 1: "FAIL", 2: "ERROR", 3: "INCONCLUSIVE", 4: "STALE"}


ALLOWED_HOSTS = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\]|[a-z0-9-]+\.app\.github\.dev)(:\d+)?$", re.IGNORECASE)


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


class Jobs:
    """Runs one ``workshop`` step at a time and keeps its output for the page to poll."""

    def __init__(self, settings: Settings, evidence: Path) -> None:
        self.settings, self.evidence = settings, evidence
        self.lock = threading.Lock()
        self.current: dict[str, Any] | None = None

    def argv(self, step: str, params: dict[str, Any]) -> list[str]:
        if step not in STEPS:
            raise ValueError(f"'{step}' is not a step this page can start")
        out = [step]
        if step == "predict":
            out += ["--candidate", "candidate/r4-bgp.eos", "--out", str(self.evidence.parent)]
        elif step == "verify" or step == "explain":
            out += ["--evidence", str(self.evidence)]
        elif step == "deploy":
            confirm = str(params.get("confirm", "")).strip().lower()
            if not SHA7.match(confirm):
                raise ValueError("type the first 7 characters of the candidate hash (letters and digits, no spaces) to confirm")
            pr = str(params.get("pr", "")).strip()
            if not pr.isdigit() or not 0 < int(pr) < 100000:
                raise ValueError("enter the number of your merged pull request: deploy only ships a change that was approved there")
            out += ["--pr", pr]
            out += ["--confirm", confirm]
        return out

    def start(self, step: str, params: dict[str, Any]) -> dict[str, Any]:
        argv = self.argv(step, params)
        with self.lock:
            if self.current and not self.current["done"]:
                raise RuntimeError(f"'workshop {self.current['step']}' is still running; wait for it to finish")
            job = {"id": uuid.uuid4().hex[:8], "step": step, "cmd": "workshop " + " ".join(argv), "lines": [],
                   "done": False, "code": None, "word": "", "started": time.time(), "secs": 0, "dropped": 0}
            self.current = job
        threading.Thread(target=self._run, args=(job, argv), daemon=True).start()
        return job

    def _run(self, job: dict[str, Any], argv: list[str]) -> None:
        cmd = [sys.executable, "-c", "import sys; from workshop.cli import main; sys.exit(main(sys.argv[1:]))", *argv]
        try:
            proc = subprocess.Popen(cmd, cwd=self.settings.repo_root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
            for line in proc.stdout:  # type: ignore[union-attr]
                job["lines"].append(ANSI.sub("", line.rstrip("\n")))
                if len(job["lines"]) > MAX_LINES * 2:  # keep the end: the verdict is there
                    drop = len(job["lines"]) - MAX_LINES
                    del job["lines"][:drop]
                    job["dropped"] += drop
            code = proc.wait()
        except OSError as exc:
            job["lines"].append(f"could not start: {exc}")
            code = 2
        job["code"], job["word"] = code, EXIT_WORDS.get(code, "ERROR" if code else "")
        job["secs"] = round(time.time() - job["started"])
        job["done"] = True

    def view(self, job_id: str, since: int) -> dict[str, Any]:
        job = self.current
        if not job or (job_id and job["id"] != job_id):
            return {"error": "no such job"}
        lines = list(job["lines"])  # one copy: offsets are absolute, counting lines dropped from the front
        dropped = job["dropped"]
        start = max(since - dropped, 0)
        out = lines[start:]
        if since < dropped:
            out = [f"... {dropped - since} earlier lines not shown ..."] + out
        return {"id": job["id"], "step": job["step"], "cmd": job["cmd"], "done": job["done"], "code": job["code"],
                "word": job["word"], "secs": job["secs"] or round(time.time() - job["started"]),
                "lines": out, "next": dropped + len(lines)}

    def summary(self) -> dict[str, Any] | None:
        job = self.current
        return {"id": job["id"], "step": job["step"], "done": job["done"], "word": job["word"]} if job else None


class Board:
    """Builds the page's state from the same files the CLI writes."""

    def __init__(self, settings: Settings, evidence: Path) -> None:
        self.settings, self.evidence = settings, evidence
        self.containers = _Cached(_containers, 10)
        self.prs = _Cached(lambda: _pull_requests(settings.repo_root), 30)
        self.chats = Chats(settings)
        self.jobs = Jobs(settings, evidence)

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
            "job": self.jobs.summary(), "r4_prefixes": list(R4_PREFIXES),
        }

    def artifacts(self, step: str) -> list[dict[str, Any]]:
        out = []
        for title, ref, lang, opened in ARTIFACTS.get(step, []):
            path: Path | None
            if ref.startswith("state:"):
                path = self.settings.state(ref[6:])
            elif ref == "evidence:":
                path = self.evidence
            elif ref.startswith("forward:"):
                try:
                    path = self.settings.state(f"forward-{self.snapshot_for(ref[8:])}.json")
                except LookupError:
                    path = None
            else:
                path = self.settings.repo_root / ref
            text = ""
            if path is not None:
                try:
                    text = path.read_text(errors="replace")
                except OSError:
                    text = ""
            if lang == "json" and text:
                try:
                    text = json.dumps(json.loads(text), indent=2)
                except ValueError:
                    pass
            shown = ref if not ref.startswith(("forward:", "state:", "evidence:")) else (path.name if path else "not collected yet")
            out.append({"title": title, "path": shown, "lang": lang, "open": opened, "text": text[:MAX_ARTIFACT],
                        "truncated": len(text) > MAX_ARTIFACT, "missing": not text})
        return out

    def write_candidate(self, retire: list[str]) -> dict[str, Any]:
        """Write candidate/r4-bgp.eos from a checklist: ``no network`` for each chosen r4 prefix."""
        bad = [p for p in retire if p not in R4_PREFIXES]
        if bad:
            raise ValueError(f"r4 does not advertise {', '.join(bad)}")
        path = self.settings.repo_root / "candidate" / "r4-bgp.eos"
        head = [line for line in path.read_text().splitlines() if line.startswith("!")] if path.is_file() else []
        lines = [*head, *candidate.HEADER[:1], f"   {candidate.HEADER[1]}", *[f"      no network {p}" for p in R4_PREFIXES if p in retire]]
        path.write_text("\n".join(lines) + "\n")
        return self._candidate()

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

        def _host_ok(self) -> bool:
            # A page on another site can point its own name at 127.0.0.1 (DNS rebinding) and then reach this server
            # as same-origin; refusing any Host that is not ours stops it.
            if ALLOWED_HOSTS.match(self.headers.get("Host", "")):
                return True
            self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden host"})
            return False

        def do_GET(self) -> None:
            if not self._host_ok():
                return
            if self.path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, (ASSETS / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/logo.svg":
                self._send(HTTPStatus.OK, (ASSETS / "fn-logo.svg").read_bytes(), "image/svg+xml")
            elif self.path == "/api/state":
                self._json(HTTPStatus.OK, board.state())
            elif self.path.startswith("/api/artifacts"):
                q = parse_qs(urlparse(self.path).query)
                self._json(HTTPStatus.OK, board.artifacts((q.get("step") or [""])[0]))
            elif self.path == "/api/guide":
                self._send(HTTPStatus.OK, (ASSETS / "guide.json").read_bytes(), "application/json")
            elif self.path.startswith("/api/job"):
                q = parse_qs(urlparse(self.path).query)
                try:
                    since = max(int((q.get("since") or ["0"])[0]), 0)
                except ValueError:
                    since = 0
                self._json(HTTPStatus.OK, board.jobs.view((q.get("id") or [""])[0], since))
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._host_ok():
                return
            # A custom header cannot be sent cross-site without a CORS preflight, which this server never
            # approves -- so only this page can start a question.
            if self.path not in ("/api/ask", "/api/run", "/api/candidate") or self.headers.get("X-Workshop-UI") != "1":
                self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
                return
            if self.path in ("/api/run", "/api/candidate"):
                try:
                    body = json.loads(self.rfile.read(max(min(int(self.headers.get("Content-Length", 0)), 2048), 0)) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError("expected a JSON object")  # noqa: TRY004 - answered as a 409 like every bad request
                    if self.path == "/api/run":
                        job = board.jobs.start(str(body.get("step", "")), body)
                        self._json(HTTPStatus.ACCEPTED, {"job": job["id"]})
                    else:
                        self._json(HTTPStatus.OK, board.write_candidate([str(x) for x in (body.get("retire") if isinstance(body.get("retire"), list) else [])]))
                except (ValueError, RuntimeError, OSError) as exc:
                    self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
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
