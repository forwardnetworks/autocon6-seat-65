"""Where a snapshot came from: the headless collector's input, its output ZIP and the Forward snapshot it became.

In the NAF framework Forward is the **Collector** here: its headless collector (``fwd-headless createsnapshot``) reads each
router over SSH, as listed in ``data_sources.json``, and writes a ZIP of every command's output. That ZIP is uploaded and
becomes the snapshot Forward models. A change record that names the ZIP by its SHA-256, the files in it and the snapshot it
became can be checked by anyone later: no hand-waving about what Forward was looking at.

Credentials never appear: ``data_sources.json`` is summarised (devices, their type and address), not copied.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from workshop.config import Settings


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def collection(settings: Settings, kind: str, expected: str = "") -> dict[str, Any]:
    """What the last ``kind`` collection ("baseline" or "post") used and produced, from the step records in the state dir.
    With ``expected`` (the snapshot a record is about): if the records describe another snapshot, nothing about the ZIP
    is claimed, only that it was not recorded here."""
    steps = settings.state_dir / "steps"
    out: dict[str, Any] = {"kind": kind}
    for name in (f"collect-{kind}.json", f"upload-{kind}.json"):
        try:
            out.update(json.loads((steps / name).read_text()))
        except (OSError, ValueError):
            pass
    if expected and str(out.get("snapshot", "")) != str(expected):
        return {"kind": kind, "snapshot": str(expected), "unrecorded": True}
    zip_path = Path(out["zip"]) if out.get("zip") else None
    if zip_path and zip_path.is_file():
        out["sha256"] = _sha256(zip_path)
        out["bytes"] = zip_path.stat().st_size  # from the ZIP itself, never from a record about another one
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
        out["entries"] = len(names)
        per_device: dict[str, list[str]] = {}
        for name in names:  # the collector names each file "<device>,<what>.txt"
            device, _, what = name.partition(",")
            if what:
                per_device.setdefault(device, []).append(what.removesuffix(".txt"))
        out["files"] = {d: sorted(w) for d, w in sorted(per_device.items())}
    else:
        out.pop("bytes", None)
        out.pop("entries", None)
    try:
        ds = json.loads((settings.state_dir / "data_sources.json").read_text())
        out["devices"] = [{"name": d.get("name"), "type": d.get("type"), "host": d.get("host")} for d in ds.get("devices") or []]
    except (OSError, ValueError, AttributeError):
        pass
    return out


def section(settings: Settings, kind: str, title: str, *, snapshot: str = "") -> list[str]:
    """The change record's provenance section for one collection, as markdown lines. ``snapshot``: the snapshot this
    record is about; the section only describes a ZIP the step records tie to that snapshot."""
    c = collection(settings, kind, snapshot)
    if c.get("unrecorded"):
        return [f"## {title}", "", (f"Forward snapshot `{c['snapshot']}`. The collection behind it was not recorded on this "
                                    "machine (taken before this record existed), so no ZIP is named here."), ""]
    if not c.get("zip") and not c.get("snapshot"):
        return []
    out = [f"## {title}", "",
           ("Forward is the **Collector** here (NAF framework): its headless collector read each router over SSH and wrote a ZIP; "
           "that ZIP became the snapshot Forward models."), "",
           "| | |", "|---|---|"]
    col = c.get("collector") or {}
    if col:
        pkg = f"`{col['package']}` (sha256 `{col.get('sha256', '')[:12]}…`), " if col.get("package") else ""
        out.append(f"| Collector | Forward headless collector {pkg}fetched for server release `{col.get('server_release', '?')}` "
                   "(`fwd-headless createsnapshot`, collection mode) |")
    if c.get("devices"):
        out.append("| Input (`data_sources.json`, credentials not shown) | " +
                   ", ".join(f"`{d['name']}` {d['type']} {d['host']}" for d in c["devices"]) + " |")
    if c.get("zip"):
        out.append(f"| Output | `{Path(c['zip']).name}`, {c.get('bytes', '?')} bytes, {c.get('entries', '?')} files |")
    if c.get("sha256"):
        out.append(f"| SHA-256 | `{c['sha256']}` |")
    if c.get("files"):
        counts = ", ".join(f"`{d}` {len(w)}" for d, w in c["files"].items())
        first = next(iter(c["files"].values()))
        out.append(f"| Files per device | {counts}: each router's {', '.join(first)} |")
    if c.get("fingerprint"):
        out.append(f"| Config fingerprint | `{c['fingerprint'][:16]}…` (r1-r4's running configs, live and in the ZIP) |")
    if c.get("snapshot"):
        out.append(f"| Became | Forward snapshot `{c['snapshot']}` (note: `autocon6:{kind} lab={settings.lab_id} fp={str(c.get('fingerprint', ''))[:12]}…`) |")
    out += ["", ("<sub>📍 Collector: `workshop baseline collect` / `verify collect` (fwd-headless) · upload: `snapshots.upload` · "
            "check it yourself: `unzip -l` on the ZIP, `sha256sum` it</sub>"), ""]
    return out
