"""``workshop restore``: put r4's advertisements back to the baseline (instructor recovery)."""

from __future__ import annotations

import re

from workshop import candidate, lab, netmiko_ops
from workshop.config import Settings

_NETWORK = re.compile(r"^\s*network (\S+)\s*$", re.MULTILINE)


def _advertised(readback: str) -> list[str]:
    return _NETWORK.findall(readback)


def run(settings: Settings, log) -> None:
    path = settings.repo_root / "lab" / "restore" / "r4-bgp.eos"
    lines = candidate.normalize(path.read_text())
    host = lab.mgmt_ips(settings)[candidate.DEVICE]
    want = [line.split()[-1] for line in lines if line.startswith("network ")]

    # A hand edit can leave extra advertisements behind: retire anything the baseline does not advertise.
    current = _advertised(netmiko_ops.show(settings, host, "show running-config section router bgp"))
    extras = [prefix for prefix in current if prefix not in want]
    header = [line for line in lines if not line.startswith("network ")]
    plan = tuple(header + [f"no network {prefix}" for prefix in extras] + [f"network {p}" for p in want])
    netmiko_ops.apply(settings, candidate.DEVICE, host, plan)

    readback = _advertised(netmiko_ops.show(settings, host, "show running-config section router bgp"))
    missing = [prefix for prefix in want if prefix not in readback]
    if missing:
        raise RuntimeError(f"restore did not take: {missing} missing on r4")
    left = [prefix for prefix in readback if prefix not in want]
    if left:
        raise RuntimeError(f"restore did not take: {left} still advertised on r4")
    if extras:
        log(f"  removed hand-added advertisements: {', '.join(extras)}")
    log("  r4 advertisements restored; run `workshop baseline` before predicting again")
