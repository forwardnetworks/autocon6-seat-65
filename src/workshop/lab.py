"""The attendee's lab: four cEOS routers and two Linux endpoints, launched by netlab.

netlab defines and launches the topology (``netlab up --no-config``); it never configures the
routers -- the baseline is applied through NAPALM. Endpoints are reached with ``docker exec``.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from workshop import executor, fingerprint
from workshop.config import Settings

CLIENT, SERVICE = "clab-autocon6-client", "clab-autocon6-service"
SERVICE_IP = "10.20.20.20"
SERVICE_PORTS = (8080, 8443, 2222)


class LabError(RuntimeError):
    """The lab is not up or not healthy."""


@dataclass(frozen=True)
class Probe:
    port: int
    reachable: bool
    listener_up: bool

    @property
    def trustworthy(self) -> bool:
        """A refused connection only means 'blocked' if the service is actually listening."""
        return self.reachable or self.listener_up


def lab_dir(settings: Settings) -> Path:
    return settings.repo_root / "lab"


def mgmt_ips(settings: Settings) -> dict[str, str]:
    """Router management addresses from netlab's generated inventory."""
    hosts = lab_dir(settings) / "hosts.yml"
    if not hosts.is_file():
        raise LabError("lab/hosts.yml is missing: start the lab with `workshop up`")
    inventory = yaml.safe_load(hosts.read_text()) or {}
    out = {}
    for group in inventory.values():
        for name, host in ((group or {}).get("hosts") or {}).items():
            if name in fingerprint.ROUTERS and (host or {}).get("ansible_host"):
                out[name] = host["ansible_host"]
    missing = set(fingerprint.ROUTERS) - set(out)
    if missing:
        raise LabError(f"lab/hosts.yml has no management address for {sorted(missing)}")
    return out


def containers() -> dict[str, str]:
    """Container name -> docker state for this lab."""
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", "label=containerlab=autocon6", "--format", "{{json .}}"],
        capture_output=True, text=True, check=True,
    )
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return {row["Names"]: row["State"] for row in rows}


def up(settings: Settings, log) -> None:
    """Start the lab if needed and apply the baseline. Safe to re-run."""
    if not shutil.which("netlab"):
        raise LabError("netlab is not installed in this environment")
    states = containers()
    lab = lab_dir(settings)
    if not states:
        log("starting the lab (netlab up --no-config) ...")
        _run(["netlab", "up", "--no-config", "-p", "clab"], cwd=lab, log=log)
    elif any(state != "running" for state in states.values()):
        # Containers that were stopped lose their data-plane links; only a redeploy restores them.
        log("the lab was stopped; redeploying it (containerlab deploy --reconfigure) ...")
        _run(["sudo", "-E", "containerlab", "deploy", "--reconfigure", "-t", "clab.yml"], cwd=lab, log=log)
    else:
        log("lab containers are running")
    wait_ssh(settings, log)
    apply_baseline(settings, log)
    wait_bgp(settings, log)


def apply_baseline(settings: Settings, log) -> None:
    ips = mgmt_ips(settings)
    for device in fingerprint.ROUTERS:
        lines = baseline_lines(settings, device)
        executor.apply(settings, device, ips[device], lines)
        log(f"  {device}: baseline applied ({len(lines)} lines)")


def baseline_lines(settings: Settings, device: str) -> list[str]:
    path = lab_dir(settings) / "baseline" / f"{device}.eos"
    return [line.rstrip() for line in path.read_text().splitlines() if line.strip() and not line.strip().startswith("!")]


def wait_ssh(settings: Settings, log, timeout: float = 600) -> None:
    ips, deadline = mgmt_ips(settings), time.monotonic() + timeout
    pending, last_error = set(ips), {}
    while pending:
        for device in sorted(pending):
            try:
                executor.show(settings, ips[device], "show hostname")  # eAPI: what the Executor uses
                socket.create_connection((ips[device], 22), timeout=3).close()  # SSH: what the collector uses
            except Exception as exc:  # noqa: BLE001 - still booting; retried until the deadline
                last_error[device] = f"{type(exc).__name__}: {str(exc)[:120]}"
            else:
                pending.discard(device)
        if pending:
            if time.monotonic() > deadline:
                detail = "; ".join(f"{d}: {last_error.get(d, '?')}" for d in sorted(pending))
                raise LabError(f"routers not reachable over eAPI and SSH after {timeout:.0f}s -- {detail}")
            time.sleep(5)
    log("  all four routers answer eAPI and SSH")


def wait_bgp(settings: Settings, log, timeout: float = 300) -> None:
    ips, deadline = mgmt_ips(settings), time.monotonic() + timeout
    while True:
        down = {}
        for device in fingerprint.ROUTERS:
            try:
                summary = executor.show_json(settings, ips[device], "show ip bgp summary")
            except executor.DeployError as exc:  # BGP is not running yet on a router that is still booting
                down[device] = [str(exc)[:100]]
                continue
            peers = (summary.get("vrfs", {}).get("default", {}) or {}).get("peers", {}) or {}
            not_up = [peer for peer, info in peers.items() if info.get("peerState") != "Established"]
            if len(peers) < 2 or not_up:
                down[device] = not_up or ["fewer than 2 peers"]
        if not down:
            log("  BGP established on all four routers")
            return
        if time.monotonic() > deadline:
            raise LabError(f"BGP not established after {timeout:.0f}s: {down}")
        time.sleep(5)


def live_configs(settings: Settings) -> dict[str, str]:
    ips = mgmt_ips(settings)
    return {device: executor.running_config(settings, ips[device]) for device in fingerprint.ROUTERS}


def probe(port: int) -> Probe:
    reach = subprocess.run(["docker", "exec", CLIENT, "nc", "-z", "-w", "3", SERVICE_IP, str(port)], capture_output=True, check=False)
    listen = subprocess.run(["docker", "exec", SERVICE, "nc", "-z", "-w", "1", "127.0.0.1", str(port)], capture_output=True, check=False)
    return Probe(port, reach.returncode == 0, listen.returncode == 0)


def _run(cmd: list[str], cwd: Path, log=None) -> None:
    """Run a lab command, showing its output as it happens (netlab and containerlab take minutes, and people should see them work)."""
    lines: list[str] = []
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
    for raw in proc.stdout:  # type: ignore[union-attr]
        line = raw.rstrip("\n")
        lines.append(line)
        if log is not None and line.strip():
            log("  | " + line)
    if proc.wait() != 0:
        raise LabError(f"{' '.join(cmd)} failed:\n" + "\n".join(lines[-15:]))
