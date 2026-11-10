"""The attendee's lab: four cEOS routers and two Linux endpoints, launched by netlab.

netlab builds the lab and configures it (``netlab up``: its modules plus the custom templates in lab/*.j2, pushed with
Netmiko). After that, every change to a router -- a deploy, a restore, a reset -- goes through NAPALM (executor.py).
Endpoints are reached with ``docker exec``.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from workshop import executor, fingerprint, show
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


def _docker():
    import docker  # the Docker SDK for Python: the same API the docker CLI uses, without parsing its output

    return docker.from_env(timeout=20)


def containers() -> dict[str, str]:
    """Container name -> docker state for this lab."""
    found = _docker().containers.list(all=True, filters={"label": "containerlab=autocon6"})
    return {c.name: c.status for c in found}


def netlab(settings: Settings, log, *args: str) -> None:
    """Run a netlab command in this process through netlab's own Python entry point (``netsim.cli.<command>.run``), the code
    behind ``netlab <command>`` in a terminal. netlab prints its own progress; a failure ends in SystemExit, turned into LabError."""
    import contextlib
    import io
    import sys

    from netsim.cli import down, initial
    from netsim.cli import up as up_

    command = {"up": up_.run, "down": down.run, "initial": initial.run}[args[0]]
    import os

    os.environ.setdefault("CLAB_VERSION_CHECK", "disable")  # containerlab's "a newer version is available" banner: noise in a lab
    show.netlab(log, list(args))
    beat = show.Heartbeat(log, f"netlab {args[0]}: it is quiet while the routers boot", every=30, usual=300, give_up=720)

    class Tee(io.TextIOBase):  # pass netlab's output straight through, and note that something was said
        def __init__(self, out):
            self.out = out

        def write(self, text: str) -> int:
            if text.strip():
                beat.heard()
            return self.out.write(text)

        def flush(self) -> None:
            self.out.flush()

    stop = threading.Event()

    def ticker() -> None:
        while not stop.wait(5):
            beat.beat()

    threading.Thread(target=ticker, daemon=True).start()
    try:
        with contextlib.chdir(lab_dir(settings)), contextlib.redirect_stdout(Tee(sys.stdout)):
            command(list(args[1:]))
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise LabError(f"netlab {' '.join(args)} failed (see its messages above)") from exc
    finally:
        stop.set()


def up(settings: Settings, log) -> None:
    """Start the lab if needed: netlab builds it and configures every router itself. Safe to re-run (a running lab is left as it is)."""
    states = containers()
    if not states:
        log("starting the lab: netlab creates the containers, then configures each router (Netmiko, over SSH) ...")
        netlab(settings, log, "up", "-p", "clab")
    elif any(state != "running" for state in states.values()):
        # Stopped containers lose their data-plane links and their configuration: netlab removes them and builds them again.
        log("the lab was stopped (a Codespace restart does that): netlab removes it and builds it again ...")
        netlab(settings, log, "down", "--cleanup")
        netlab(settings, log, "up", "-p", "clab")
    else:
        log("lab containers are running")
        wait_ssh(settings, log)
        if not configured(settings):
            # a bring-up that stopped after the containers started (netlab up was interrupted, or failed before configuring)
            log("  the routers have no configuration yet: netlab finishes its bring-up (netlab initial) ...")
            netlab(settings, log, "initial")
        else:
            log("  and configured (their configuration is left as it is: `workshop restore` or `workshop reset` puts it back)")
    wait_ssh(settings, log)
    wait_bgp(settings, log)


def configured(settings: Settings) -> bool:
    """Has netlab configured the lab? r1 with BGP neighbors is the tell: a fresh cEOS has no BGP at all."""
    try:
        summary = executor.show_json(settings, mgmt_ips(settings)["r1"], "show ip bgp summary")
    except executor.DeployError:
        return False
    return bool((summary.get("vrfs", {}).get("default", {}) or {}).get("peers"))


def netlab_has_netmiko() -> bool:
    """netlab pushes EOS configuration with Netmiko; without it netlab falls back to Ansible (not installed)."""
    import importlib.util

    return importlib.util.find_spec("netmiko") is not None


def wait_ssh(settings: Settings, log, timeout: float = 600) -> None:
    ips, deadline = mgmt_ips(settings), time.monotonic() + timeout
    pending, last_error = set(ips), {}
    beat = show.Heartbeat(log, "waiting for the routers to boot (cEOS takes 1 to 4 minutes)", usual=240, give_up=540)
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
            up_now = len(ips) - len(pending)
            beat.beat(f"{up_now} of {len(ips)} routers answering ({100 * up_now // len(ips)}%); waiting for {', '.join(sorted(pending))}")
            time.sleep(5)
    log("  all four routers answer eAPI and SSH")


def wait_bgp(settings: Settings, log, timeout: float = 300) -> None:
    ips, deadline = mgmt_ips(settings), time.monotonic() + timeout
    beat = show.Heartbeat(log, "waiting for BGP to come up", usual=120, give_up=240)
    while True:
        down, established, sessions = {}, 0, 0
        for device in fingerprint.ROUTERS:
            try:
                summary = executor.show_json(settings, ips[device], "show ip bgp summary")
            except executor.DeployError as exc:  # BGP is not running yet on a router that is still booting
                down[device] = [str(exc)[:100]]
                continue
            peers = (summary.get("vrfs", {}).get("default", {}) or {}).get("peers", {}) or {}
            not_up = [peer for peer, info in peers.items() if info.get("peerState") != "Established"]
            sessions += max(len(peers), 2)
            established += len(peers) - len(not_up)
            if len(peers) < 2 or not_up:
                down[device] = not_up or ["fewer than 2 peers"]
        if not down:
            log("  BGP established on all four routers")
            return
        if time.monotonic() > deadline:
            raise LabError(f"BGP not established after {timeout:.0f}s: {down}")
        total = max(sessions, 2 * len(fingerprint.ROUTERS))
        beat.beat(f"{established} of {total} BGP sessions Established ({100 * established // total}%); still waiting on {', '.join(sorted(down))}")
        time.sleep(5)


def live_configs(settings: Settings) -> dict[str, str]:
    ips = mgmt_ips(settings)
    return {device: executor.running_config(settings, ips[device]) for device in fingerprint.ROUTERS}


def probe(port: int) -> Probe:
    """Real packets from the real client: ``nc -z`` inside the client container, and inside the service container to tell a
    blocked port from a service that is not listening. Docker SDK ``exec_run``: the API behind ``docker exec``."""
    d = _docker()
    reach = d.containers.get(CLIENT).exec_run(["nc", "-z", "-w", "3", SERVICE_IP, str(port)])
    listen = d.containers.get(SERVICE).exec_run(["nc", "-z", "-w", "1", "127.0.0.1", str(port)])
    return Probe(port, reach.exit_code == 0, listen.exit_code == 0)


