"""``workshop restore``: put r4's advertisements back to the baseline (instructor recovery)."""

from __future__ import annotations

from workshop import candidate, lab, netmiko_ops
from workshop.config import Settings


def run(settings: Settings, log) -> None:
    path = settings.repo_root / "lab" / "restore" / "r4-bgp.eos"
    lines = candidate.normalize(path.read_text())
    host = lab.mgmt_ips(settings)[candidate.DEVICE]
    netmiko_ops.apply(settings, candidate.DEVICE, host, lines)
    readback = netmiko_ops.show(settings, host, "show running-config section router bgp")
    missing = [line.split()[-1] for line in lines if line.startswith("network ") and line.split()[-1] not in readback]
    if missing:
        raise RuntimeError(f"restore did not take: {missing} missing on r4")
    log("  r4 advertisements restored; run `workshop baseline` before predicting again")
