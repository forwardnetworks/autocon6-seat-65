"""Every call from the workshop into Forward, through forward-sdk.

A baseline snapshot is tagged in its note as ``autocon6:baseline lab=<lab> fp=<sha256>``: the lab it
came from and the fingerprint of the router configs collected into it. The PR check (which has no
lab access) re-derives that fingerprint from Forward's own copy of the configs, so it knows exactly
which network state it is predicting against.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from forward_sdk import ForwardClient, answer_of
from forward_sdk.errors import (
    ForwardConflictError,
    ForwardError,
    ForwardPermissionError,
    ForwardRateLimitError,
    ForwardTimeoutError,
)

from workshop import __version__, fingerprint
from workshop.candidate import Candidate
from workshop.config import Settings
from workshop.evaluate import ObserverTimeout
from workshop.requirements import Requirement

_TAG = re.compile(r"autocon6:(?P<kind>baseline|post) lab=(?P<lab>\S+) fp=(?P<fp>[0-9a-f]{64})")


@dataclass(frozen=True)
class SnapshotTag:
    kind: str  # baseline | post
    lab: str
    fp: str

    def note(self, extra: str = "") -> str:
        return f"autocon6:{self.kind} lab={self.lab} fp={self.fp}" + (f" {extra}" if extra else "")

    @classmethod
    def parse(cls, note: str | None) -> SnapshotTag | None:
        match = _TAG.search(note or "")
        return cls(match["kind"], match["lab"], match["fp"]) if match else None


class AiUnavailable(RuntimeError):
    """Forward AI could not answer (not enabled, busy, timed out, or an error). Advisory only: callers
    report it and carry on -- AI never decides a gate."""


@dataclass(frozen=True)
class AiAnswer:
    chat_id: str
    summary: str
    insights: tuple[str, ...]
    tools: tuple[str, ...]
    out_of_scope: bool


@dataclass(frozen=True)
class Prediction:
    change_set_id: str
    snapshot_id: str
    parent_snapshot_id: str
    reused: bool


UNAVAILABLE_WAIT = 90.0  # seconds a just-processed snapshot may take to become queryable


def while_unavailable(call, *, sleep=time.sleep, now=time.monotonic, limit: float = UNAVAILABLE_WAIT):
    """Run ``call``; if Forward says the snapshot cannot be used yet, wait and try again.

    Forward can report a snapshot PROCESSED a moment before path searches and queries accept it: they answer HTTP 409
    SNAPSHOT_UNAVAILABLE "(currently PROCESSED)". That is a race, not a verdict, so it is retried for up to ``limit``
    seconds. Any other error, and the same error after the limit, is raised as it was.
    """
    deadline = now() + limit
    delay = 2.0
    while True:
        try:
            return call()
        except ForwardConflictError as exc:
            if "SNAPSHOT_UNAVAILABLE" not in str(exc) or now() + delay > deadline:
                raise
            sleep(delay)
            delay = min(delay * 1.5, 10.0)


class SnapshotObserver:
    """Answers the evaluator's questions from one snapshot."""

    def __init__(self, client: ForwardClient, snapshot_id: str) -> None:
        self._c, self.snapshot_id = client, snapshot_id

    def paths(self, req: Requirement, intent: str) -> dict[str, Any]:
        try:
            response = while_unavailable(lambda: self._c.path_search.get_paths(
                src_ip=req.src, dst_ip=req.dst, ip_proto=req.ip_proto, dst_port=str(req.port),
                intent=intent, snapshot_id=self.snapshot_id, max_results=5, max_return_path_results=1,
            ))
        except ForwardTimeoutError as exc:
            raise ObserverTimeout(str(exc)) from exc
        return response.model_dump(by_alias=True, mode="json")

    def nqe(self, query: str) -> list[dict[str, Any]]:
        try:
            return [dict(row) for row in while_unavailable(lambda: self._c.nqe.query(query, snapshot_id=self.snapshot_id))]
        except ForwardTimeoutError as exc:
            raise ObserverTimeout(str(exc)) from exc


class Forward:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.c = ForwardClient.from_env(network_id=settings.network_id, user_agent=f"autocon6-workshop/{__version__}")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.c.close()

    def release(self) -> str:
        return str(self.c.version().get("release", ""))

    def network_ids(self) -> list[str]:
        return [str(n.id) for n in self.c.networks.list()]

    def latest_baseline(self):
        """The newest processed snapshot that is not a prediction (a collection, import or reprocess)."""
        return self.c.snapshots.latest_processed(include_predicted=False)

    def snapshot(self, snapshot_id: str):
        for snap in self.c.snapshots.list(include_archived=True):
            if str(snap.id) == str(snapshot_id):
                return snap
        return None

    def upload(self, zip_path: Path, tag: SnapshotTag, extra: str = ""):
        snap = self.c.snapshots.upload(zip_path, note=tag.note(extra), process=True, wait=False)
        return self.c.snapshots.wait_until_processed(snap.id, poll_interval=3, timeout=900)

    def device_names(self, snapshot_id: str) -> set[str]:
        return {d.name for d in self.c.devices.list(snapshot_id=snapshot_id)}

    def configs(self, snapshot_id: str, devices=fingerprint.ROUTERS) -> dict[str, str]:
        return {d: self.c.devices.file(d, "configuration.txt", snapshot_id=snapshot_id) for d in devices}

    def fingerprint(self, snapshot_id: str) -> str:
        return fingerprint.of(self.configs(snapshot_id))

    def observer(self, snapshot_id: str) -> SnapshotObserver:
        return SnapshotObserver(self.c, snapshot_id)

    def predict(self, candidate: Candidate, baseline_id: str, name: str, description: str) -> Prediction:
        """Predict ``candidate`` against ``baseline_id`` in change set ``name``.

        Re-running with the same name reuses a finished prediction of the same baseline instead of
        submitting a duplicate job (a CI retry must not queue a second Predict run).
        """
        for info in self.c.change_sets.list():
            if info.name != name:
                continue
            handle = self.c.change_sets.handle(str(info.id))
            for snap in handle.predicted_snapshots():
                if str(snap.state) == "PROCESSED" and str(snap.parent_snapshot_id) == str(baseline_id):
                    return Prediction(str(info.id), str(snap.id), str(snap.parent_snapshot_id), True)
            handle.delete()  # a half-finished attempt; start clean
        handle = self.c.change_sets.create(name, snapshot_id=baseline_id, description=description, tags=["autocon6"])
        handle.set_commands(candidate.device, candidate.text)
        handle.commit(f"{name} {candidate.sha256[:12]}")
        snap = handle.predict_and_wait(f"{name} {candidate.sha256[:12]}", timeout=600, poll_interval=3)
        return Prediction(handle.id, str(snap.id), str(snap.parent_snapshot_id), False)

    def download_collector(self, destination: Path):
        return self.c.client_software.download(destination)

    # --- Forward AI (advisory; see workshop.ai) -------------------------------------------------

    def ask_ai(self, prompt: str, snapshot_id: str, *, timeout: float = 300, busy_timeout: float = 300) -> AiAnswer:
        """One Forward AI chat question grounded in ``snapshot_id``, in a new chat. Forward answers one
        question per user at a time; ``busy_timeout`` waits for this user's other question to finish."""
        with _ai_errors("Forward AI chat"):
            conv = self.c.ai.start(prompt, network_id=self.settings.network_id, snapshot_id=snapshot_id, busy_timeout=busy_timeout)
            conv.wait(timeout=timeout)
            messages = conv.messages()
        return _answer(str(conv.id), messages[-1] if messages else None)

    def ai_follow_up(self, chat_id: str, prompt: str, *, timeout: float = 300, busy_timeout: float = 300) -> AiAnswer:
        """A follow-up question in an existing chat (same snapshot, with the conversation so far)."""
        with _ai_errors("Forward AI chat"):
            conv = self.c.ai.get(chat_id)
            message = conv.ask_and_wait(prompt, timeout=timeout, busy_timeout=busy_timeout)
        return _answer(chat_id, message)

    def ai_transcript(self, chat_id: str) -> str:
        with _ai_errors("Forward AI transcript"):
            return self.c.ai.get(chat_id).transcript()

    def author_change_set(self, baseline_id: str, name: str) -> str:
        """A change set on ``baseline_id`` for the CLI assist to draft against (it never predicts)."""
        for info in self.c.change_sets.list():
            if info.name == name:
                if str(info.snapshot_id) == str(baseline_id):
                    return str(info.id)
                self.c.change_sets.handle(str(info.id)).delete()
        return str(self.c.change_sets.create(name, snapshot_id=baseline_id, description="Forward AI drafting space", tags=["autocon6"]).id)

    def cli_assist(self, change_set_id: str, device: str, prompt: str) -> str:
        with _ai_errors("Forward AI config assist"):
            response = self.c.predict_assist.assist_change_set_commands(
                change_set_id=change_set_id, device_name=device, network_id=self.settings.network_id, body={"prompt": prompt})
        return response.commands or ""

    def change_overview(self, change_set_id: str) -> dict[str, Any]:
        with _ai_errors("Forward AI change overview"):
            r = self.c.predict_assist.assist_change_set_overview(change_set_id=change_set_id, network_id=self.settings.network_id)
        return {"name": r.name or "", "description": r.description or "", "tags": list(r.tags or [])}

    def diff_summaries(self, before_id: str, after_id: str) -> dict[str, str]:
        """Forward AI's summaries of the config change and of its impact between two snapshots."""
        with _ai_errors("Forward AI diff summary"):
            config = self.c.predict_assist.assist_config_diff_summary(before_snapshot_id=before_id, after_snapshot_id=after_id)
            impact = self.c.predict_assist.assist_impact_diff_summary(before_snapshot_id=before_id, after_snapshot_id=after_id)
        return {"config": config.summary or "", "impact": impact.summary or ""}


def _answer(chat_id: str, message) -> AiAnswer:
    answer = answer_of(message) if message else None
    if answer is None:
        raise AiUnavailable("Forward AI returned no answer")
    return AiAnswer(
        chat_id=chat_id, summary=answer.summary or "", insights=tuple(answer.key_insights or ()),
        tools=tuple(str(tc.type) for tc in (message.tool_calls or ())), out_of_scope=bool(answer.out_of_scope),
    )


@contextmanager
def _ai_errors(what: str) -> Iterator[None]:
    """Turn SDK errors from an AI call into AiUnavailable with a sentence an attendee can act on."""
    try:
        yield
    except ForwardPermissionError as exc:
        raise AiUnavailable(f"{what} is not enabled for this seat: {str(exc)[:200]}") from exc
    except ForwardRateLimitError as exc:
        raise AiUnavailable(f"{what} is still answering your other question; try again when it finishes") from exc
    except ForwardTimeoutError as exc:
        raise AiUnavailable(f"{what} did not answer in time") from exc
    except ForwardError as exc:
        raise AiUnavailable(f"{what} failed ({type(exc).__name__}): {str(exc)[:200]}") from exc
