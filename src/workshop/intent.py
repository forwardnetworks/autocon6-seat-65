"""The critical flows and the NQE finding, as Forward's own intent checks.

The workshop's requirements (``requirements/candidate.yml``) are judged by the workshop's evaluate.py. Here the same
requirements are also registered in Forward as **intent checks** on the lab's network, so Forward judges them itself on
every snapshot: the baseline, a prediction, and the collection after a deploy. That is what Predict's "Define intent"
step and its check results are, and why the change records can show Forward's own verdicts next to the workshop's.

One source: the checks are built from the requirement file. Each kind maps to what Forward has:

- ``flow``, expect permit: an **Existential** path check (traffic from src to dst on the port must get through);
- ``flow``, expect deny: an **Isolation** path check (it must not);
- ``path``: an **Existential** path check that must pass through the middle hops (``chain``);
- ``nqe`` with ``expect_rows: 0``: an **NQE** check on the query published to the org's NQE library at
  ``/autocon6/<file stem>`` (an NQE check's violations are its rows, so it passes at zero rows);
- ``route``: no Forward check (it would need its own published query); the workshop's own judge covers it.

Persistent: a check added on one snapshot is evaluated on every later one. Idempotent: an existing check of the same name
(or, for NQE, the same query) is left alone. Display only: a failure here never changes a verdict or an exit code.
"""

from __future__ import annotations

from typing import Any

PREFIX = "autocon6 "  # every path check's name starts with it, so the records can find ours among Forward's predefined checks
LIBRARY = "/autocon6/"  # where the workshop's NQE queries live in the org's library


def _status(value: Any) -> str:
    """Forward's check status as plain text (the SDK gives an enum)."""
    return str(getattr(value, "value", value) or "")


def _dump(x: Any) -> Any:
    return x.model_dump(by_alias=True) if hasattr(x, "model_dump") else x


def _flow(kind: str, src: str, dst: str, port: int, via: list[str] | None = None) -> dict[str, Any]:
    filters: dict[str, Any] = {
        "from": {"location": {"type": "SubnetLocationFilter", "value": src}},
        "to": {"location": {"type": "SubnetLocationFilter", "value": dst},
               "headers": [{"type": "PacketFilter", "values": {"ip_proto": ["6"], "tp_dst": [str(port)]}}]},
    }
    if via:
        filters["chain"] = [{"location": {"type": "DeviceFilter", "value": d}, "transitType": "through"} for d in via]
    return {"checkType": kind, "filters": filters}


def planned(requirements) -> list[dict[str, Any]]:
    """The Forward checks for these requirements: {"id", "kind", "body" | "query_path", "what"}."""
    out = []
    for r in requirements.requirements:
        d = dict(vars(r)) if hasattr(r, "__dict__") else dict(r)
        rid, kind = d.get("id"), d.get("kind")
        src, dst, port = d.get("src"), d.get("dst"), d.get("port")
        if kind == "flow" and src and dst and port:
            check = "Existential" if d.get("expect") == "permit" else "Isolation"
            out.append({"id": rid, "kind": check, "what": f"TCP {src} -> {dst}:{port} {'gets through' if check == 'Existential' else 'is blocked'}",
                        "body": {"name": PREFIX + rid, "note": f"{d.get('title', '')} (requirements: {rid})",
                                 "definition": _flow(check, src, dst, int(port)), "tags": ["autocon6"]}})
        elif kind == "path" and src and dst and port and len(d.get("path") or []) > 2:
            via = list(d["path"])[1:-1]
            out.append({"id": rid, "kind": "Existential", "what": f"TCP {src} -> {dst}:{port} through {', '.join(via)}",
                        "body": {"name": PREFIX + rid, "note": f"{d.get('title', '')} (requirements: {rid})",
                                 "definition": _flow("Existential", src, dst, int(port), via), "tags": ["autocon6"]}})
        elif kind == "nqe" and int(d.get("expect_rows", -1)) == 0 and d.get("query_file"):
            stem = str(d["query_file"]).rsplit("/", 1)[-1].removesuffix(".nqe")
            out.append({"id": rid, "kind": "NQE", "what": f"{LIBRARY}{stem} returns no rows", "query_path": LIBRARY + stem})
    return out


def ensure(client, snapshot_id: str, requirements, log=None) -> list[dict[str, Any]]:
    """Add each planned check that Forward doesn't have yet (persistent), and return them all with Forward's status on
    ``snapshot_id``. Raises on a Forward error; callers treat it as display only."""
    from workshop import show

    existing = [_dump(c) for c in client.checks.get_checks(snapshot_id=snapshot_id)]
    by_name = {c.get("name"): c for c in existing}
    by_query = {((c.get("definition") or {}).get("queryId")): c for c in existing if (c.get("definition") or {}).get("queryId")}
    out = []
    for p in planned(requirements):
        if p["kind"] == "NQE":
            q = client.nqe.repo.find(p["query_path"])
            have = by_query.get(q.query_id)
            body = {"definition": {"checkType": "NQE", "queryId": q.query_id}}  # Forward names an NQE check after its query
        else:
            have = by_name.get(p["body"]["name"])
            body = p["body"]
        if have is None:
            show.sdk(log, f'checks.add_check(snapshot_id="{snapshot_id}", persistent=True)   {p["kind"]}: {p["what"]}')
            have = _dump(client.checks.add_check(snapshot_id=snapshot_id, body=body, persistent=True))
        out.append({"id": p["id"], "kind": p["kind"], "what": p["what"], "check_id": str(have.get("id")),
                    "name": have.get("name"), "status": _status(have.get("status")), "violations": have.get("numViolations")})
    return out


def our_queries(client) -> set[str]:
    """The query ids of the workshop's NQE queries in the org library (everything under LIBRARY)."""
    try:
        return {str(q.query_id) for path, q in client.nqe.repo.index().items() if str(path).startswith(LIBRARY)}
    except Exception:  # noqa: BLE001 - no library access: only the named path checks are recognised
        return set()


def results(client, snapshot_id: str) -> dict[str, dict[str, Any]]:
    """Forward's status for the workshop's intent checks on one snapshot, by check id: the path checks by their name,
    the NQE checks by their query (Forward allows no name or tag on an NQE check)."""
    queries = our_queries(client)
    out = {}
    for c in client.checks.get_checks(snapshot_id=snapshot_id):
        d = _dump(c)
        definition = d.get("definition") or {}
        ours = str(d.get("name") or "").startswith(PREFIX) or str(definition.get("queryId") or "") in queries
        if ours:
            out[str(d.get("id"))] = {"name": d.get("name"), "type": definition.get("checkType"), "status": _status(d.get("status")),
                                     "violations": d.get("numViolations")}
    return out


MARK = {"PASS": "✅ PASS", "FAIL": "❌ FAIL"}


def section(client, before: str, after: str, *, before_label: str, after_label: str, title: str) -> list[str]:
    """Forward's own verdicts on the workshop's intent checks, on two snapshots, as markdown lines. [] if there are none."""
    b, a = results(client, before), results(client, after)
    ids = list(b)
    if not ids:
        return []
    changed = [i for i in ids if i in a and b[i]["status"] != a[i]["status"]]
    out = [f"## {title}", "",
           ("The critical flows and the NQE finding, registered in Forward as **intent checks** (path checks and an NQE "
           "check, from `requirements/candidate.yml`) and judged by Forward itself on each snapshot."), "",
           (f"**{len(changed)} of {len(ids)} change status.**" if changed else f"None of the {len(ids)} changes status."), "",
           f"| Intent check | Type | {before_label} | {after_label} |", "|---|---|---|---|"]
    for i in ids:
        x, y = b[i], a.get(i, {})
        after_cell = MARK.get(y.get("status", ""), y.get("status")) if y else "not evaluated on this snapshot"
        out.append(f"| {x['name']} | {x['type']} | {MARK.get(x['status'], x['status'])} | {after_cell} |")
    out += ["", ("<sub>📍 In Forward: Verify › intent checks (path and NQE), and Predict › checks · SDK: <code>checks.add_check · "
            "checks.get_checks</code></sub>"), ""]
    return out


def register(settings, snapshot_id: str, log, *, quiet: bool = False) -> None:
    """Register the intent checks on the network (from requirements/candidate.yml) and print Forward's verdict on
    ``snapshot_id``: after every baseline, step by step, all at once, or from a reset. Display only. ``quiet``: register
    (and place the routers in their Forward locations) without printing; lab 1 is facts only, and intent checks are
    introduced in lab 2 (`workshop intent`)."""
    if quiet:  # nothing printed: not the hand-written lines, nor the SDK trace lines (the page's feed still records the calls)
        from workshop import trace

        previous = trace._sink
        trace.set_sink(None)
        try:
            return register(settings, snapshot_id, lambda _line: None)
        finally:
            trace.set_sink(previous)
    from workshop import requirements
    from workshop.forward import Forward

    root = settings.repo_root
    reqs = requirements.load(root / "requirements" / "candidate.yml", root=root)
    findings = set()  # NQE queries the baseline requirements expect rows from: on a baseline, their intent check fails by design
    try:
        base = requirements.load(root / "requirements" / "baseline.yml", root=root)
        findings = {str(vars(r).get("query_file")) for r in base.requirements if vars(r).get("kind") == "nqe" and int(vars(r).get("expect_rows", 0)) > 0}
    except Exception:  # noqa: BLE001, S110 - only used for the "expected" label
        pass
    files = {vars(r)["id"]: str(vars(r).get("query_file")) for r in reqs.requirements}
    with Forward(settings, quick=True) as fwd:
        try:  # sites for Forward's connectivity diff (see workshop/locations.py)
            from workshop import locations

            locations.ensure(fwd.c, settings, log)
        except Exception as exc:  # noqa: BLE001 - display only
            log(f"  (Forward locations not set: {type(exc).__name__}: {str(exc)[:120]})")
        log("")
        log("Your critical flows and the NQE finding, as Forward's own intent checks (they judge every snapshot from now on):")
        for r in ensure(fwd.c, str(snapshot_id), reqs, log=log):
            expected = r["status"] == "FAIL" and r["kind"] == "NQE" and files.get(r["id"]) in findings
            label = "FAIL (expected: the finding the change will clear)" if expected else (r["status"] or "?")
            log(f"  {label:5} {r['id']:<14} {r['kind']:<12} {r['what']}")
