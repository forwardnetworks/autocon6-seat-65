"""Behavioral requirements: the questions a change is judged by.

A requirement set is a small YAML file (``requirements/candidate.yml``, ``requirements/baseline.yml``).
Loading validates every field, so a typo fails loudly instead of silently checking nothing, and the
set's SHA-256 is recorded in the evidence so a later edit cannot pass off an old result.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

Kind = Literal["flow", "route", "path", "nqe"]
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_ID = re.compile(r"^[A-Z][A-Z0-9-]{1,40}$")
_PROTOCOLS = {"tcp": 6, "udp": 17}


class RequirementError(ValueError):
    """A requirement file is malformed."""


@dataclass(frozen=True)
class Requirement:
    id: str
    kind: Kind
    title: str
    why: str = ""
    # flow / path
    src: str = ""
    dst: str = ""
    proto: str = "tcp"
    port: int = 0
    expect: str = ""  # flow: permit | deny
    deny_at: str = ""  # flow deny: device whose ACL must drop it
    path: tuple[str, ...] = ()
    # route
    device: str = ""
    prefix: str = ""
    next_hop: str = ""
    # nqe
    query_file: str = ""
    expect_rows: int = -1

    @property
    def ip_proto(self) -> int:
        return _PROTOCOLS[self.proto]

    @property
    def flow_label(self) -> str:
        return f"{self.proto.upper()} {self.src} -> {self.dst}:{self.port}"


@dataclass(frozen=True)
class RequirementSet:
    name: str
    path: Path
    sha256: str
    requirements: tuple[Requirement, ...] = field(default_factory=tuple)

    def by_id(self, requirement_id: str) -> Requirement:
        for req in self.requirements:
            if req.id == requirement_id:
                return req
        raise KeyError(requirement_id)


def load(path: str | Path, *, root: str | Path | None = None) -> RequirementSet:
    """Load and validate a requirement set. ``root`` resolves ``query_file`` (default: repo root)."""
    file = Path(path)
    raw = file.read_bytes()
    doc = yaml.safe_load(raw) or {}
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        raise RequirementError(f"{file}: expected 'schema: 1'")
    name = doc.get("set")
    if name not in ("baseline", "candidate"):
        raise RequirementError(f"{file}: 'set' must be baseline or candidate")
    defaults = doc.get("defaults") or {}
    items = doc.get("requirements")
    if not isinstance(items, list) or not items:
        raise RequirementError(f"{file}: no requirements (an empty set can never pass)")
    base = Path(root) if root is not None else file.resolve().parent.parent
    seen: set[str] = set()
    reqs = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise RequirementError(f"{file}: requirement {index} is not a mapping")
        req = _parse({**defaults, **item}, file, base)
        if req.id in seen:
            raise RequirementError(f"{file}: duplicate requirement id {req.id}")
        seen.add(req.id)
        reqs.append(req)
    return RequirementSet(name=name, path=file, sha256=hashlib.sha256(raw).hexdigest(), requirements=tuple(reqs))


_ALLOWED = {
    "flow": {"id", "kind", "title", "why", "src", "dst", "proto", "port", "expect", "deny_at"},
    "path": {"id", "kind", "title", "why", "src", "dst", "proto", "port", "path"},
    "route": {"id", "kind", "title", "why", "device", "prefix", "next_hop", "src", "dst", "proto"},
    "nqe": {"id", "kind", "title", "why", "query_file", "expect_rows", "src", "dst", "proto"},
}


def _parse(item: dict, file: Path, base: Path) -> Requirement:
    rid = str(item.get("id", ""))
    where = f"{file}: {rid or '<no id>'}"
    if not _ID.match(rid):
        raise RequirementError(f"{where}: id must look like APP-EXISTING")
    kind = item.get("kind")
    if kind not in _ALLOWED:
        raise RequirementError(f"{where}: unknown kind {kind!r}")
    unknown = set(item) - _ALLOWED[kind]
    if unknown:
        raise RequirementError(f"{where}: unknown field(s) {sorted(unknown)}")
    title = str(item.get("title") or "").strip()
    if not title:
        raise RequirementError(f"{where}: a title is required (it is what the attendee reads)")
    common = {"id": rid, "kind": kind, "title": title, "why": str(item.get("why") or "").strip()}

    if kind in ("flow", "path"):
        src, dst = _ip(item.get("src"), where, "src"), _ip(item.get("dst"), where, "dst")
        proto = str(item.get("proto", "tcp")).lower()
        if proto not in _PROTOCOLS:
            raise RequirementError(f"{where}: proto must be tcp or udp")
        port = item.get("port")
        if not isinstance(port, int) or not 0 < port < 65536:
            raise RequirementError(f"{where}: port must be 1-65535")
        common |= {"src": src, "dst": dst, "proto": proto, "port": port}
        if kind == "flow":
            expect = item.get("expect")
            if expect not in ("permit", "deny"):
                raise RequirementError(f"{where}: expect must be permit or deny")
            deny_at = str(item.get("deny_at") or "")
            if expect == "deny" and not _NAME.match(deny_at):
                raise RequirementError(f"{where}: a deny requirement names the device that must drop it (deny_at)")
            return Requirement(**common, expect=expect, deny_at=deny_at)
        hops = item.get("path")
        if not isinstance(hops, list) or len(hops) < 2 or not all(isinstance(h, str) and _NAME.match(h) for h in hops):
            raise RequirementError(f"{where}: path must list at least two device names")
        return Requirement(**common, path=tuple(hops))

    if kind == "route":
        device = str(item.get("device") or "")
        if not _NAME.match(device):
            raise RequirementError(f"{where}: device must be a device name")
        try:
            prefix = str(ipaddress.ip_network(str(item.get("prefix")), strict=True))
        except ValueError as exc:
            raise RequirementError(f"{where}: prefix: {exc}") from exc
        return Requirement(**common, device=device, prefix=prefix, next_hop=_ip(item.get("next_hop"), where, "next_hop"))

    query_file = str(item.get("query_file") or "")
    if not query_file or not (base / query_file).is_file():
        raise RequirementError(f"{where}: query_file {query_file!r} not found under {base}")
    rows = item.get("expect_rows")
    if not isinstance(rows, int) or rows < 0:
        raise RequirementError(f"{where}: expect_rows must be a count")
    return Requirement(**common, query_file=query_file, expect_rows=rows)


def _ip(value: object, where: str, name: str) -> str:
    try:
        return str(ipaddress.ip_address(str(value)))
    except ValueError as exc:
        raise RequirementError(f"{where}: {name}: {exc}") from exc
