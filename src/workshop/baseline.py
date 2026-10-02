"""``workshop baseline``: collect the lab, prove it, and give Forward a tagged baseline.

The live configs (over Netmiko) are fingerprinted before collection and must match what the
collector captured -- otherwise the network changed while it was being collected. The snapshot is
uploaded with its note tagged ``autocon6:baseline lab=<lab> fp=<fingerprint>``, then judged against
the baseline requirements.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from workshop import collect, fingerprint, lab, requirements
from workshop.config import Settings
from workshop.evaluate import Report, evaluate
from workshop.forward import Forward, SnapshotTag


class BaselineError(RuntimeError):
    pass


def run(settings: Settings, log, *, kind: str = "baseline", extra: str = "") -> tuple[str, str, Report | None]:
    """Collect, upload and (for a baseline) evaluate. Returns (snapshot id, fingerprint, report)."""
    with Forward(settings) as fwd:
        binary = collect.ensure(settings, fwd, log)
        log("reading the routers' running configs ...")
        live_fp = fingerprint.of(lab.live_configs(settings))
        log("running the headless collector ...")
        zip_path = collect.run(settings, binary, kind, log)
        zip_fp = fingerprint.of(fingerprint.configs_from_zip(zip_path))
        if zip_fp != live_fp:
            raise BaselineError("the router configs changed while they were being collected; run it again")
        log("uploading to Forward and waiting for the model ...")
        snap = fwd.upload(zip_path, SnapshotTag(kind, settings.lab_id, zip_fp), f"collected {datetime.now(UTC).isoformat(timespec='seconds')}")
        devices = fwd.device_names(str(snap.id))
        if not set(fingerprint.ROUTERS) <= devices:
            raise BaselineError(f"snapshot {snap.id} is missing {sorted(set(fingerprint.ROUTERS) - devices)}")
        log(f"  snapshot {snap.id} processed ({len(devices)} devices)")
        report = None
        if kind == "baseline":
            reqs = requirements.load(settings.repo_root / "requirements" / "baseline.yml", root=settings.repo_root)
            report = evaluate(reqs, fwd.observer(str(snap.id)), str(snap.id), root=settings.repo_root)
            settings.state("baseline.json").write_text(json.dumps({"snapshot": str(snap.id), "fingerprint": zip_fp, "report": report.to_json()}, indent=2) + "\n")
    return str(snap.id), zip_fp, report
