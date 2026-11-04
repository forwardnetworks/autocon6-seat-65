"""The Executor: every configuration change to a lab router goes through here, over NAPALM.

NAPALM's EOS driver speaks eAPI (HTTPS) and applies a change as a configuration *session*:

1. ``load_merge_candidate`` stages the exact lines in a session. Nothing is live yet.
2. ``compare_config`` asks the router for the diff the change would make. This is the device's own
   answer, and it is what ``workshop deploy`` prints before it commits.
3. ``commit_config`` makes the session live, saves it, and keeps a ``rollback-0`` copy of the old
   startup config. A line the router refuses stops the whole session, so a change never half applies.

This is the one module that imports a device library. A different Executor (Netmiko, scrapli, Nornir)
means rewriting these five functions: ``connect``, ``apply``, ``show``, ``show_json`` and ``running_config``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from napalm import get_network_driver
from napalm.base.exceptions import ConnectionException, MergeConfigException, NapalmException
from pyeapi.eapilib import EapiError

from workshop.config import Settings


class DeployError(RuntimeError):
    """The router refused part of a change, or could not be reached."""


@dataclass(frozen=True)
class Applied:
    device: str
    lines: tuple[str, ...]
    output: str  # the diff the router computed for the session, before it was committed


def connect(settings: Settings, host: str):
    driver = get_network_driver("eos")(
        host, settings.device_username, settings.device_password, timeout=30,
        # eAPI over HTTPS. cEOS presents a self-signed certificate on the lab's private management network, and
        # pyeapi does not verify it by default.
        optional_args={"transport": "https", "port": 443, "enable_password": settings.enable_password},
    )
    try:
        driver.open()
    except (ConnectionException, NapalmException, EapiError, OSError) as exc:
        raise DeployError(f"cannot reach {host} over eAPI: {exc}") from exc
    return driver


def _clear_stale_sessions(conn) -> None:
    """Abort configuration sessions an earlier, killed run left pending: NAPALM refuses to start while one exists."""
    try:
        sessions = conn.device.run_commands(["show configuration sessions"], encoding="json")[0].get("sessions", {})
        for name, info in sessions.items():
            if name.startswith("napalm_") and info.get("state") == "pending":
                conn.device.run_commands([f"configure session {name} abort"], encoding="text")
    except (NapalmException, EapiError):
        pass  # best effort: the load below reports a real problem


def apply(settings: Settings, device: str, host: str, lines: Sequence[str]) -> Applied:
    if not lines:
        raise DeployError(f"{device}: nothing to apply")
    conn = connect(settings, host)
    try:
        try:
            _clear_stale_sessions(conn)
            conn.load_merge_candidate(config="\n".join(lines) + "\n")
            diff = conn.compare_config()
            if diff.strip():
                conn.commit_config()
            else:
                conn.discard_config()  # already as asked: nothing to commit
        except (MergeConfigException, NapalmException, EapiError) as exc:
            try:
                conn.discard_config()
            except (NapalmException, EapiError):
                pass
            raise DeployError(f"{device} refused the change or could not finish it: {exc}") from exc
    finally:
        try:
            conn.close()
        except (NapalmException, EapiError):
            pass  # never let closing hide the real error
    return Applied(device, tuple(lines), diff)


def show(settings: Settings, host: str, command: str) -> str:
    conn = connect(settings, host)
    try:
        return conn.cli([command])[command]
    except (NapalmException, EapiError) as exc:
        raise DeployError(f"{host}: {command!r} failed: {exc}") from exc
    finally:
        conn.close()


def show_json(settings: Settings, host: str, command: str) -> dict:
    """A show command as structured data, straight from eAPI (no ``| json`` pipe)."""
    conn = connect(settings, host)
    try:
        return conn.device.run_commands([command], encoding="json")[0]
    except (NapalmException, EapiError) as exc:  # e.g. "BGP inactive" while a router is still booting
        raise DeployError(f"{host}: {command!r} failed: {exc}") from exc
    finally:
        conn.close()


def running_config(settings: Settings, host: str) -> str:
    return show(settings, host, "show running-config")
