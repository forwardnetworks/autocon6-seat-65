"""The Forward headless collector: fetch it, point it at the lab, run it.

The collector is not pinned: ``ensure`` downloads the collector the Forward server serves (as the seat
user, through forward-sdk). It must come from the server's release line (same ``major.minor``, for example
26.9): the same release passes quietly, a later patch on the server than the collector passes with a warning
(Forward publishes the collector package a little after the server), and any other line is refused. Each
server release is unpacked once under ``FWD_HEADLESS_HOME/<release>``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from workshop import fingerprint, lab
from workshop.config import Settings
from workshop.forward import Forward

HEAP = "-Xmx1g"  # the collector otherwise sizes its heap from host RAM; a Codespace has 16 GB


class CollectError(RuntimeError):
    """Collection did not produce a complete snapshot."""


_RELEASE = re.compile(r"(\d+)\.(\d+)\.(\d+)-(\d+)")


def release_of(file_name: str) -> tuple[int, int, int, int] | None:
    """The release a collector package name carries (``fwd-unix-26.9.0-18.tar.gz`` -> 26, 9, 0, 18)."""
    match = _RELEASE.search(file_name)
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def compatible(server_release: str, file_name: str) -> str:
    """How a served collector relates to the server: ``exact``, ``lagging`` (same line, older) or ``refuse``."""
    served, server = release_of(file_name), release_of(server_release)
    if served is None or server is None:
        return "exact" if server_release in file_name else "refuse"
    if served == server:
        return "exact"
    if served[:2] == server[:2] and served <= server:
        return "lagging"
    return "refuse"


def ensure(settings: Settings, fwd: Forward, log) -> Path:
    release = fwd.release()
    home = settings.collector_home / release
    binary = home / "fwd" / "bin" / "fwd-headless"
    if binary.is_file():
        return binary
    log(f"downloading the Forward {release} headless collector ...")
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=home) as tmp:
        package = fwd.download_collector(Path(tmp))
        fit = compatible(release, package.file_name)
        if fit == "refuse":
            raise CollectError(f"server runs {release} but served {package.file_name!r}; refusing a collector from another release line")
        if fit == "lagging":
            log(f"  note: the server runs {release} and serves {package.file_name}; same release line, so it is used")
        with tarfile.open(package.path) as archive:
            archive.extractall(home, filter="data")
    if not binary.is_file():
        raise CollectError(f"{package.file_name} did not contain fwd/bin/fwd-headless")
    (home / "package.json").write_text(json.dumps({"file": package.file_name, "sha256": package.sha256, "size": package.size}) + "\n")
    log(f"  collector {package.file_name} ready (sha256 {package.sha256[:12]})")
    return binary


def render_data_sources(settings: Settings) -> Path:
    """The collector's device list, from netlab's inventory. Holds lab passwords: 0600, state dir."""
    ips = lab.mgmt_ips(settings)
    doc = {
        "networkName": f"autocon6-{settings.lab_id}",
        "devices": [
            {"name": d, "type": "arista_eos_ssh", "host": ips[d], "cliCredentialId": "L-1", "cliCredential2Id": "PM-1"}
            for d in fingerprint.ROUTERS
        ],
        "credentials": [
            {"id": "L-1", "type": "LOGIN", "username": settings.device_username, "password": settings.device_password},
            {"id": "PM-1", "type": "PRIVILEGED_MODE", "password": settings.enable_password},
        ],
    }
    path = settings.state("data_sources.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(doc, handle, indent=2)
    os.chmod(path, 0o600)
    return path


def run(settings: Settings, binary: Path, base_name: str, log) -> Path:
    sources = render_data_sources(settings)
    out_dir = settings.state("snapshots")
    out_dir.mkdir(exist_ok=True)
    started = time.time()
    env = {**os.environ, "INSTALL4J_ADD_VM_PARAMS": HEAP}
    log_path = settings.state(f"collect-{base_name}.log")
    with log_path.open("w") as log_file:
        result = subprocess.run(
            [str(binary), "createsnapshot", "--data-sources", str(sources), "--snapshot-generated-dir", str(out_dir),
             "--snapshot-base-name", base_name, "--threads", "4"],
            cwd=settings.state_dir, env=env, stdout=log_file, stderr=subprocess.STDOUT, timeout=900, check=False,
        )
    zips = sorted((p for p in out_dir.glob(f"{base_name}_*.zip") if p.stat().st_mtime >= started - 1), key=lambda p: p.stat().st_mtime)
    if result.returncode != 0 or not zips:
        raise CollectError(f"the collector exited {result.returncode} without a snapshot; see {log_path}")
    try:
        fingerprint.configs_from_zip(zips[-1])
    except KeyError as exc:
        raise CollectError(f"incomplete collection: {exc}; see {log_path}") from exc
    log(f"  collected r1-r4 in {time.time() - started:.0f}s -> {zips[-1].name}")
    return zips[-1]
