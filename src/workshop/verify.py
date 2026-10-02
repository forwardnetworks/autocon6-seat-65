"""``workshop verify``: did the network do what Forward predicted?

After a deploy it probes the real flows, collects the network again, and judges the new snapshot
against the candidate requirements. The result is MATCH only if every requirement has the same
verdict it had in the prediction and the live probes agree. A collection failure is reported as
"applied, verification incomplete" -- never as a pass.
"""

from __future__ import annotations

import json
from pathlib import Path

from workshop import baseline, lab, requirements
from workshop.config import Settings
from workshop.evaluate import evaluate
from workshop.evidence import Manifest
from workshop.forward import Forward


def run(settings: Settings, evidence: Path, log) -> dict:
    m = Manifest.read(evidence)
    reqs = requirements.load(settings.repo_root / "requirements" / "candidate.yml", root=settings.repo_root)
    lab.wait_bgp(settings, log)
    log("probing the real flows from the client ...")
    probes = []
    for req in reqs.requirements:
        if req.kind != "flow":
            continue
        p = lab.probe(req.port)
        want = req.expect == "permit"
        ok = p.reachable == want and p.trustworthy
        probes.append({"id": req.id, "port": req.port, "expected": "open" if want else "blocked",
                       "observed": "open" if p.reachable else ("blocked" if p.listener_up else "service down"), "ok": ok})
        log(f"  TCP {req.port}: {probes[-1]['observed']} (expected {probes[-1]['expected']})")
    try:
        snapshot_id, fp, _ = baseline.run(settings, log, kind="post", extra=f"after {m.candidate_sha256[:12]}")
    except Exception as exc:  # noqa: BLE001 - any collection failure: applied, not verified
        result = {"outcome": "INCOMPLETE", "reason": f"applied, verification incomplete: {exc}", "probes": probes}
        settings.state("verify.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    with Forward(settings) as fwd:
        report = evaluate(reqs, fwd.observer(snapshot_id), snapshot_id, root=settings.repo_root)
    predicted = {r["id"]: r["status"] for r in m.results}
    rows = [{"id": r.id, "title": r.title, "predicted": predicted.get(r.id, "?"), "collected": r.status.value, "observed": r.observed}
            for r in report.results]
    match = all(row["predicted"] == row["collected"] for row in rows) and all(p["ok"] for p in probes)
    result = {"outcome": "MATCH" if match else "MISMATCH", "post_snapshot_id": snapshot_id, "fingerprint": fp, "rows": rows, "probes": probes}
    settings.state("verify.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
