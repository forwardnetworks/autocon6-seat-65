"""Every configuration change to a lab router goes through here, over Netmiko.

``apply`` enters enable mode, sends the lines, rejects the whole change if EOS answered any line
with ``%`` (a refused command), and saves. Readback commands are used to confirm what landed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from netmiko import ConnectHandler

from workshop.config import Settings


class DeployError(RuntimeError):
    """The router refused part of a change, or could not be reached."""


@dataclass(frozen=True)
class Applied:
    device: str
    lines: tuple[str, ...]
    output: str


def connect(settings: Settings, host: str):
    return ConnectHandler(
        device_type="arista_eos",
        host=host,
        username=settings.device_username,
        password=settings.device_password,
        secret=settings.enable_password,
        fast_cli=False,
        timeout=30,
    )


def apply(settings: Settings, device: str, host: str, lines: Sequence[str]) -> Applied:
    if not lines:
        raise DeployError(f"{device}: nothing to apply")
    conn = connect(settings, host)
    try:
        conn.enable()
        output = conn.send_config_set(list(lines), exit_config_mode=True, cmd_verify=False)
        refused = [line.strip() for line in output.splitlines() if line.strip().startswith("%")]
        if refused:
            raise DeployError(f"{device} refused the change: {'; '.join(refused)}")
        conn.save_config()
    finally:
        conn.disconnect()
    return Applied(device, tuple(lines), output)


def show(settings: Settings, host: str, command: str) -> str:
    conn = connect(settings, host)
    try:
        conn.enable()
        return conn.send_command(command, read_timeout=60)
    finally:
        conn.disconnect()


def running_config(settings: Settings, host: str) -> str:
    return show(settings, host, "show running-config")
