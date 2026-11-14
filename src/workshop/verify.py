"""``workshop verify``: did the network do what Forward predicted?

After a deploy it probes the real flows, collects the network again, and judges the new snapshot
against the candidate requirements. The result is MATCH only if every requirement has the same
verdict it had in the prediction and the live probes agree. A collection failure is reported as
"applied, verification incomplete" -- never as a pass.
"""

from __future__ import annotations

import json
from pathlib import Path

from workshop import baseline, lab, requirements, show
from workshop.config import Settings
from workshop.evaluate import evaluate
from workshop.evidence import Manifest
from workshop.forward import Forward


def deployed_evidence(settings: Settings, evidence: Path) -> Manifest:
    """The evidence verify judges against, only if it is the prediction of the change that was deployed (a later prediction can overwrite
    evidence/evidence.json; judging the real network against that would compare it with the wrong forecast)."""
    m = Manifest.read(evidence)
    record = settings.state("deploy.json")
    if record.is_file():
        sent = json.loads(record.read_text()).get("candidate_sha256", "")
        if sent and sent != m.candidate_sha256:
            raise RuntimeError(f"{evidence} is now the prediction of another change ({m.candidate_sha256[:7]}), not the one deployed ({sent[:7]}): "
                               "a newer `workshop predict` replaced it. Predict the deployed change again (same candidate), then verify")
    return m


def probe(settings: Settings, evidence: Path, log) -> list[dict]:
    """``workshop verify probe``: real packets from the real client, for every flow requirement. Saved for ``compare``."""
    reqs = requirements.load(settings.repo_root / "requirements" / "candidate.yml", root=settings.repo_root)
    predicted = {str(r.get("id")): str(r.get("status")) for r in deployed_evidence(settings, evidence).results}
    lab.wait_bgp(settings, log)
    log("probing the real flows from the client ...")
    probes = []
    for req in reqs.requirements:
        if req.kind != "flow":
            continue
        show.docker(log, lab.CLIENT, f"exec_run(['nc', '-z', '-w', '3', '{lab.SERVICE_IP}', '{req.port}'])   real packets from the client")
        p = lab.probe(req.port)
        # The probe checks the prediction, not the requirement: where Predict said a requirement FAILs (a deploy with caveats),
        # the packet is expected to fail the same way. For a PASS prediction that is the requirement itself.
        broken = predicted.get(req.id) == "FAIL"
        want = (req.expect == "permit") != broken
        ok = p.reachable == want and p.trustworthy
        probes.append({"id": req.id, "port": req.port, "expected": "open" if want else "blocked",
                       "observed": "open" if p.reachable else ("blocked" if p.listener_up else "service down"), "ok": ok,
                       "predicted": predicted.get(req.id, "")})
        why = f", as Predict said: {req.id} FAILs" if broken else ""
        log(f"  {'✓' if ok else '✗'} TCP {req.port}: {probes[-1]['observed']} (expected {probes[-1]['expected']}{why})")
    settings.state("steps").mkdir(exist_ok=True)
    settings.state("steps/probes.json").write_text(json.dumps({"evidence": str(evidence), "probes": probes}, indent=2) + "\n")
    return probes


def compare(settings: Settings, evidence: Path, snapshot_id: str, fp: str, probes: list[dict], log) -> dict:
    """``workshop verify compare``: the candidate requirements on the post-change snapshot, verdict by verdict against the
    prediction, and the probes. MATCH only if every one agrees."""
    m = deployed_evidence(settings, evidence)
    reqs = requirements.load(settings.repo_root / "requirements" / "candidate.yml", root=settings.repo_root)
    show.note(log, f"judging {len(reqs.requirements)} requirements on post-change snapshot {snapshot_id}, the same way Predict's were judged")
    with Forward(settings) as fwd:
        report = evaluate(reqs, fwd.observer(snapshot_id), snapshot_id, root=settings.repo_root)
    predicted = {r["id"]: r["status"] for r in m.results}
    rows = [{"id": r.id, "title": r.title, "expected": r.expected, "predicted": predicted.get(r.id, "?"), "collected": r.status.value,
             "observed": r.observed, "evidence": r.evidence} for r in report.results]
    match = all(row["predicted"] == row["collected"] for row in rows) and all(p["ok"] for p in probes)
    result = {"outcome": "MATCH" if match else "MISMATCH", "post_snapshot_id": snapshot_id, "fingerprint": fp, "rows": rows, "probes": probes}
    settings.state("verify.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def run(settings: Settings, evidence: Path, log) -> dict:
    """probe, collect, upload, wait, compare in a row (the lab page's catch-up); the exercises run them one at a time."""
    m = Manifest.read(evidence)
    probes = probe(settings, evidence, log)
    try:
        snapshot_id, fp, _ = baseline.run(settings, log, kind="post", extra=f"after {m.candidate_sha256[:12]}")
    except Exception as exc:  # noqa: BLE001 - any collection failure: applied, not verified
        result = {"outcome": "INCOMPLETE", "reason": f"applied, verification incomplete: {exc}", "probes": probes}
        settings.state("verify.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    return compare(settings, evidence, snapshot_id, fp, probes, log)
