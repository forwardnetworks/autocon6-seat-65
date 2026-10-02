"""Settings for one attendee lab, from the environment (a Codespace injects them as secrets) or a
local ``.env``. Forward credentials are read by forward-sdk itself (``FORWARD_URL``,
``FORWARD_USERNAME``, ``FORWARD_PASSWORD``, ``FORWARD_VERIFY_TLS``); nothing here prints them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class SettingsError(RuntimeError):
    """A required setting is missing."""


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    lab_id: str
    network_id: str
    state_dir: Path
    collector_home: Path
    device_username: str
    device_password: str
    enable_password: str

    def state(self, name: str) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return self.state_dir / name


def repo_root(start: Path | None = None) -> Path:
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "requirements" / "candidate.yml").is_file() and (candidate / "lab").is_dir():
            return candidate
    raise SettingsError("run workshop from inside the workshop repository")


def load(start: Path | None = None) -> Settings:
    root = repo_root(start)
    load_dotenv(root / ".env", override=False)
    lab_id = os.environ.get("WORKSHOP_LAB_ID", "").strip()
    network_id = os.environ.get("FORWARD_NETWORK_ID", "").strip()
    missing = [name for name, value in (("WORKSHOP_LAB_ID", lab_id), ("FORWARD_NETWORK_ID", network_id)) if not value]
    for name in ("FORWARD_URL", "FORWARD_USERNAME", "FORWARD_PASSWORD"):
        if not os.environ.get(name, "").strip():
            missing.append(name)
    if missing:
        raise SettingsError("missing settings: " + ", ".join(missing) + " (Codespace secrets, or .env for local development)")
    state = Path(os.path.expanduser(os.environ.get("WORKSHOP_STATE_DIR", "~/.local/state/autocon6"))) / lab_id
    collector = Path(os.path.expanduser(os.environ.get("FWD_HEADLESS_HOME", "~/.cache/autocon6/collector")))
    return Settings(
        repo_root=root,
        lab_id=lab_id,
        network_id=network_id,
        state_dir=state,
        collector_home=collector,
        device_username=os.environ.get("LAB_DEVICE_USERNAME", "admin"),
        device_password=os.environ.get("LAB_DEVICE_PASSWORD", "admin"),
        enable_password=os.environ.get("LAB_ENABLE_PASSWORD", "admin"),
    )
