"""A fingerprint of the lab's router configurations, to prove "the network Predict saw is the
network we are about to change".

It is computed from the collector's snapshot (``<device>,configuration.txt`` in the ZIP), from
Forward's copy of a snapshot, and from the live routers over Netmiko; all three normalize to the
same bytes. Normalizing drops comment and blank lines and trailing spaces, and redacts secret values
the way the collector does (it stores ``secret sha512 <redacted>``).
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

ROUTERS = ("r1", "r2", "r3", "r4")
_SECRET = re.compile(r"\b(secret|password|key)((?:\s+(?:sha512|md5|plaintext|\d))?)\s+\S+")


def normalize(config: str) -> list[str]:
    out = []
    for line in config.splitlines():
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("!"):
            continue
        out.append(_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)} <redacted>", line))
    return out


def of(configs: Mapping[str, str], devices: Iterable[str] = ROUTERS) -> str:
    """SHA-256 over the normalized configs of ``devices``; a missing device is an error."""
    digest = hashlib.sha256()
    for device in sorted(devices):
        if device not in configs:
            raise KeyError(f"no configuration for {device}")
        digest.update(f"== {device}\n".encode())
        digest.update(("\n".join(normalize(configs[device])) + "\n").encode())
    return digest.hexdigest()


def configs_from_zip(path: str | Path, devices: Iterable[str] = ROUTERS) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        out = {}
        for device in devices:
            member = f"{device},configuration.txt"
            if member not in names:
                raise KeyError(f"{path} has no {member}")
            out[device] = archive.read(member).decode("utf-8", "replace")
    return out
