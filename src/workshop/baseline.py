"""``workshop baseline``: collect the lab, prove it, and give Forward a tagged baseline.

The live configs (over NAPALM) are fingerprinted before collection and must match what the
collector captured -- otherwise the network changed while it was being collected. The snapshot is
uploaded with its note tagged ``autocon6:baseline lab=<lab> fp=<fingerprint>``, then judged against
the baseline requirements.
"""

from __future__ import annotations

import json
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from forward_sdk import ForwardTimeoutError

from workshop import collect, executor, fingerprint, lab, requirements, show
from workshop.config import Settings
from workshop.evaluate import Report, evaluate
from workshop.forward import PENDING, Forward, SnapshotTag, state_of


class BaselineError(RuntimeError):
    pass


def forget_previous_loop(settings: Settings) -> None:
    """A fresh baseline starts a new loop: the last deploy and verify results no longer describe the lab."""
    for name in ("deploy.json", "verify.json", "steps/collect-post.json", "steps/upload-post.json", "steps/probes.json"):
        settings.state(name).unlink(missing_ok=True)


COLLECT_TRIES = (0, 15, 30)  # seconds to wait before each attempt


def collect_clean(settings: Settings, binary, kind: str, log, *, sleep=time.sleep):
    """A ZIP that is complete (all four routers) and is the network as it is (it hashes like the live configs).

    Routers still booting, an SSH login that times out, or a config changing mid-collection all make one attempt come out
    wrong, and all of them pass on their own. So: up to three attempts, a little apart, each saying why it is trying again."""
    for i, pause in enumerate(COLLECT_TRIES):
        if pause:
            log(f"  trying the collection again in {pause} s (attempt {i + 1} of {len(COLLECT_TRIES)})")
            sleep(pause)
        try:
            log("reading the routers' running configs ...")
            show.napalm(log, "r1-4", "cli('show running-config')   hashed into a fingerprint of the network as it is")
            live_fp = fingerprint.of(lab.live_configs(settings))
            log("running the headless collector ...")
            zip_path = collect.run(settings, binary, kind, log)
            zip_fp = fingerprint.of(fingerprint.configs_from_zip(zip_path))
            if zip_fp == live_fp:
                return zip_path, zip_fp
            problem = "the router configs changed while they were being collected"
        except (collect.CollectError, executor.DeployError, OSError, KeyError, zipfile.BadZipFile) as exc:  # KeyError: a router missing from the ZIP
            problem = str(exc)
        log(f"  the collection did not come out clean: {problem}")
    raise BaselineError(f"three collections did not come out clean (last: {problem}). Is the lab up? `workshop up`, then try again")


def upload(settings: Settings, fwd: Forward, zip_path: Path, zip_fp: str, kind: str, extra: str, log) -> str:
    """Upload the ZIP with its note (or find the identical upload Forward is still processing). Returns the snapshot id."""
    tag = SnapshotTag(kind, settings.lab_id, zip_fp)
    extra = extra or f"collected {datetime.now(UTC).isoformat(timespec='seconds')}"
    waiting = fwd.tagged(kind, settings.lab_id, fp=zip_fp, minutes=30)
    if waiting is not None and state_of(waiting) in PENDING:
        # an earlier run uploaded this very network and Forward is still on it: wait for that one, don't queue a second
        log(f"Forward is still processing snapshot {waiting.id}, an earlier upload of exactly this network: waiting for it, not uploading again")
        return str(waiting.id)
    log("uploading to Forward ...")
    show.sdk(log, f'snapshots.upload("{zip_path.name}", note="{tag.note(extra)}", process=True)')
    snap_id = fwd.submit(zip_path, tag, extra, log=log)
    log(f"  uploaded: snapshot {snap_id}")
    return snap_id


def wait(settings: Settings, fwd: Forward, snap_id: str, kind: str, log):
    """Wait while Forward turns the ZIP into a model; check all four routers are in it."""
    show.sdk(log, f'snapshots.wait_until_processed("{snap_id}")   Forward turns the ZIP into a model of every route, ACL and path')
    try:
        snap = fwd.wait(snap_id, log=log)
    except ForwardTimeoutError as exc:
        again = "`workshop baseline wait` (or `judge`)" if kind == "baseline" else "`workshop verify wait`"
        raise BaselineError(f"snapshot {snap_id} is uploaded and Forward is still processing it (its queue is busy). Don't collect "
                            f"again: that only adds to the queue. In a minute or two, run {again}: it waits for this upload rather than sending another.") from exc
    devices = fwd.device_names(str(snap.id))
    if not set(fingerprint.ROUTERS) <= devices:
        raise BaselineError(f"snapshot {snap.id} is missing {sorted(set(fingerprint.ROUTERS) - devices)}")
    log(f"  snapshot {snap.id} processed ({len(devices)} devices)")
    return snap


def evaluate_baseline(settings: Settings, fwd: Forward, snap_id: str, fp: str, log) -> Report:
    reqs = requirements.load(settings.repo_root / "requirements" / "baseline.yml", root=settings.repo_root)
    show.note(log, f"judging {len(reqs.requirements)} requirements: each is a path search or an NQE query to Forward, compared with the YAML by evaluate.py")
    report = evaluate(reqs, fwd.observer(snap_id), snap_id, root=settings.repo_root)
    settings.state("baseline.json").write_text(json.dumps({"snapshot": snap_id, "fingerprint": fp, "report": report.to_json()}, indent=2) + "\n")
    forget_previous_loop(settings)
    return report


def run(settings: Settings, log, *, kind: str = "baseline", extra: str = "") -> tuple[str, str, Report | None]:
    """Collect, upload, wait and (for a baseline) evaluate, in a row: the steps `workshop baseline collect|upload|wait|check`
    run one at a time. Returns (snapshot id, fingerprint, report)."""
    with Forward(settings) as fwd:
        binary = collect.ensure(settings, fwd, log)
        zip_path, zip_fp = collect_clean(settings, binary, kind, log)
        snap_id = upload(settings, fwd, zip_path, zip_fp, kind, extra, log)
        snap = wait(settings, fwd, snap_id, kind, log)
        report = evaluate_baseline(settings, fwd, str(snap.id), zip_fp, log) if kind == "baseline" else None
    return str(snap.id), zip_fp, report


# ---- by hand: you run the collector and upload the snapshot yourself; workshop prepares, then checks and judges ----

def prepare(settings: Settings, log) -> dict[str, str]:
    """Everything you need to take the baseline yourself: the collector, its device file, the live fingerprint and the note
    your snapshot must carry. Written to ``by-hand.sh`` in the lab's folder, so ``source`` gives you the variables."""
    with Forward(settings) as fwd:
        binary = collect.ensure(settings, fwd, log)
    sources = collect.render_data_sources(settings)
    show.napalm(log, "r1-4", "cli('show running-config')   hashed into the fingerprint your snapshot's note will carry")
    live_fp = fingerprint.of(lab.live_configs(settings))
    tag = SnapshotTag("baseline", settings.lab_id, live_fp)
    out_dir = settings.state("snapshots")
    out_dir.mkdir(exist_ok=True)
    env = {"S": str(settings.state_dir), "COLLECTOR": str(binary), "SOURCES": str(sources), "SNAPDIR": str(out_dir), "NOTE": tag.note()}
    script = settings.state("by-hand.sh")
    script.write_text("".join(f"export {k}='{v}'\n" for k, v in env.items()))
    return env


def judge(settings: Settings, log, snapshot_id: str = "") -> tuple[str, str, Report]:
    """Check the newest baseline of this lab in Forward (the one you just uploaded; waited for if Forward is still processing
    it) was taken of the network as it is now, then judge it.

    Three checks before any requirement: the note says baseline and this lab; Forward's copy of the configs hashes to the
    note's fingerprint (the ZIP really is what the note claims); and the live routers still hash to it (nothing changed since)."""
    with Forward(settings) as fwd:
        if snapshot_id:
            show.sdk(log, f'snapshots.list()   snapshot {snapshot_id}, the one you uploaded')
            snap = fwd.snapshot(snapshot_id)
        else:
            show.sdk(log, 'snapshots.list(exclude_triggers=("PREDICT",))   your uploads, newest first: the newest note saying baseline and this lab')
            snap = fwd.tagged("baseline", settings.lab_id, minutes=24 * 60) or fwd.latest_baseline()
        if snap is None:
            raise BaselineError("Forward has no snapshot for this network yet: upload yours first")
        if state_of(snap) not in PENDING | {"PROCESSED"}:
            raise BaselineError(f"Forward could not process snapshot {snap.id} ({state_of(snap)}): collect and upload it again")
        if state_of(snap) in PENDING:
            log(f"  snapshot {snap.id} is still processing in Forward: waiting for it")
            show.sdk(log, f'snapshots.wait_until_processed("{snap.id}")')
            try:
                snap = fwd.wait(str(snap.id), log=log)
            except ForwardTimeoutError as exc:
                raise BaselineError(f"Forward is still processing snapshot {snap.id}: don't upload again; run `workshop baseline judge` in a minute or two") from exc
        tag = SnapshotTag.parse(snap.note)
        log(f"  snapshot {snap.id}, note: {snap.note!r}")
        if tag is None or tag.kind != "baseline" or tag.lab != settings.lab_id:
            raise BaselineError(f"snapshot {snap.id}'s note is not 'autocon6:baseline lab={settings.lab_id} fp=…': upload it with the NOTE from `workshop baseline prepare`")
        show.sdk(log, 'devices.file("rN", "configuration.txt") for r1-r4   Forward\'s copy of each config, hashed')
        if fwd.fingerprint(str(snap.id)) != tag.fp:
            raise BaselineError(f"the configs in snapshot {snap.id} do not hash to the note's fingerprint: that ZIP is not the network the note describes")
        show.napalm(log, "r1-4", "cli('show running-config')   the live network, hashed the same way")
        if fingerprint.of(lab.live_configs(settings)) != tag.fp:
            raise BaselineError("the routers changed after that snapshot was taken: run `workshop baseline prepare` and collect again")
        log("  ✓ a baseline of this lab  ✓ Forward's copy of the four configs matches the note  ✓ the live routers match it too")
        report = evaluate_baseline(settings, fwd, str(snap.id), tag.fp, log)
    return str(snap.id), tag.fp, report
