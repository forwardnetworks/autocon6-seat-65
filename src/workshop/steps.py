"""One step at a time: each ``workshop <stage> <step>`` command does one thing, says what it did, and leaves its result in the
state folder (``steps/*.json``) for the next step. A step whose input is missing says which step to run first.

    workshop baseline collect   the headless collector -> a snapshot ZIP, and the network's fingerprint
    workshop baseline upload    that ZIP -> a Forward snapshot, with a note saying what it is
    workshop baseline wait      Forward turns it into a model
    workshop baseline check     three integrity checks, then the baseline requirements
    workshop verify collect | upload | wait   the same three, for the network after the change
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from workshop import baseline, collect, fingerprint
from workshop.config import Settings
from workshop.forward import Forward


class StepError(RuntimeError):
    pass


STAGE = {"baseline": "baseline", "post": "verify"}


def save(settings: Settings, name: str, data: dict[str, Any]) -> Path:
    settings.state("steps").mkdir(exist_ok=True)
    path = settings.state(f"steps/{name}.json")
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def load(settings: Settings, name: str, first: str) -> dict[str, Any]:
    path = settings.state(f"steps/{name}.json")
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise StepError(f"nothing to work on yet: run `{first}` first") from exc


def collect_step(settings: Settings, kind: str, log) -> dict[str, Any]:
    with Forward(settings) as fwd:
        binary = collect.ensure(settings, fwd, log)
    zip_path, fp = baseline.collect_clean(settings, binary, kind, log)
    with zipfile.ZipFile(zip_path) as z:
        entries = z.namelist()
    data = {"kind": kind, "zip": str(zip_path), "fingerprint": fp, "bytes": zip_path.stat().st_size, "entries": len(entries)}
    save(settings, f"collect-{kind}", data)
    log("")
    log(f"Evidence: {zip_path}")
    log(f"  {data['bytes'] // 1024} KiB, {len(entries)} files: each router's command output (`unzip -l {zip_path} | head`)")
    log(f"  fingerprint {fp[:16]}…: a hash of r1-r4's running configs, taken live and again from the ZIP (they matched)")
    log(f"Next: workshop {STAGE[kind]} upload")
    return data


def upload_step(settings: Settings, kind: str, log, extra: str = "") -> dict[str, Any]:
    got = load(settings, f"collect-{kind}", f"workshop {STAGE[kind]} collect")
    zip_path = Path(got["zip"])
    if not zip_path.is_file():
        raise StepError(f"{zip_path} is gone: run `workshop {STAGE[kind]} collect` again")
    with Forward(settings) as fwd:
        snap_id = baseline.upload(settings, fwd, zip_path, got["fingerprint"], kind, extra, log)
    data = {"kind": kind, "snapshot": snap_id, "fingerprint": got["fingerprint"], "zip": got["zip"]}
    save(settings, f"upload-{kind}", data)
    log("")
    log(f"Evidence: snapshot {snap_id} in your Forward network. Its note says what it is: autocon6:{kind} lab={settings.lab_id} fp={got['fingerprint'][:12]}…")
    log("  Forward has the ZIP and is building its model now (routes, ACLs, every path).")
    log(f"Next: workshop {STAGE[kind]} wait")
    return data


def wait_step(settings: Settings, kind: str, log) -> dict[str, Any]:
    got = load(settings, f"upload-{kind}", f"workshop {STAGE[kind]} upload")
    with Forward(settings) as fwd:
        snap = baseline.wait(settings, fwd, got["snapshot"], kind, log)
        devices = sorted(fwd.device_names(str(snap.id)))
    got["processed"] = True
    save(settings, f"upload-{kind}", got)
    log("")
    log(f"Evidence: snapshot {snap.id} is processed. Forward modelled {len(devices)} devices: {', '.join(devices)}")
    log("  Look at it now if you like: workshop data inventory · workshop data routes --device r1 · workshop path")
    log(f"Next: workshop {'baseline check' if kind == 'baseline' else 'verify compare'}")
    return got


def processed_snapshot(settings: Settings, kind: str) -> dict[str, Any]:
    got = load(settings, f"upload-{kind}", f"workshop {STAGE[kind]} upload")
    if not got.get("processed"):
        raise StepError(f"snapshot {got['snapshot']} has not been seen processed yet: run `workshop {STAGE[kind]} wait` first")
    return got


def baseline_snapshot(settings: Settings) -> str:
    """The baseline to look at: the newest of the checked one (baseline.json) and one uploaded and processed but not checked
    yet (after `workshop baseline wait`), so you can explore Forward's model before you judge it. "" if there is none."""
    found = []
    for name in ("baseline.json", "steps/upload-baseline.json"):
        path = settings.state_dir / name
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("snapshot") and (name == "baseline.json" or data.get("processed")):
            found.append((path.stat().st_mtime, str(data["snapshot"])))
    return max(found)[1] if found else ""


def forget(settings: Settings, *names: str) -> None:
    for name in names:
        settings.state(f"steps/{name}.json").unlink(missing_ok=True)


def live_fingerprint(settings: Settings) -> str:
    from workshop import lab

    return fingerprint.of(lab.live_configs(settings))
