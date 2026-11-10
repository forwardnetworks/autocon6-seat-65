"""``workshop path``: trace one flow through Forward's model, hop by hop, and say why each device did what it did.

Forward's path search (``path_search.get_paths`` with ``include_network_functions``) returns, for each hop, the device, its
ingress and egress interfaces, what it did (routed, ACL permit, ACL deny…) and which ACL decided it. Path search does not say
which route each router used, so that comes from the same snapshot's routing tables (an NQE query, longest prefix match
here). Together that is the answer to "why does this packet go there, or stop there?" for any snapshot: your baseline, the
predicted network, or the network after a deploy. Display only: nothing here decides a gate.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any

from workshop import data, term

OUTCOME_MEANS = {
    "DELIVERED": "the packet reaches the destination",
    "BLACKHOLE": "a device had no route for it and dropped it silently",
    "DROPPED": "a device dropped it on purpose (a null route, or an explicit drop)",
    "INADMISSIBLE": "the first device would not accept it",
    "UNREACHABLE": "it never found a way towards the destination",
    "LOOP": "it goes round in a loop",
    "DELIVERED_TO_INCORRECT_LOCATION": "it leaves the network, but not where the destination is",
}


def _v(x: Any) -> str:
    return "" if x is None else str(getattr(x, "value", x))


@dataclass
class Hop:
    device: str
    ingress: str
    egress: str
    behaviors: list[str]
    acls: list[str]
    route: str = ""


@dataclass
class Trace:
    snapshot_id: str
    label: str
    src: str
    dst: str
    port: int
    outcome: str
    security: str
    hops: list[Hop]
    return_hops: list[str] = field(default_factory=list)
    return_outcome: str = ""
    paths_found: int = 0
    dst_location: str = ""
    call: str = ""


def _route_for(rows: list[dict[str, Any]], device: str, dst: str) -> str:
    """The route ``device`` uses for ``dst``: longest prefix match over the snapshot's routing table."""
    ip = ipaddress.ip_address(dst)
    best = None
    for r in rows:
        if r.get("device") != device:
            continue
        try:
            net = ipaddress.ip_network(str(r.get("prefix")), strict=False)
        except ValueError:
            continue
        if ip in net and (best is None or net.prefixlen > best[0].prefixlen):
            best = (net, r)
    if best is None:
        return f"no route to {dst}"
    net, r = best
    via = r.get("via") or r.get("interface") or ""
    proto = str(r.get("protocol", "")).lower().replace("direct_connected", "connected").replace("_", " ")
    return f"{net} {proto}" + (f" via {via}" if via else "")


def trace(client, snapshot_id: str, *, src: str, dst: str, port: int, label: str = "", proto: int = 6) -> Trace:
    call = (f'path_search.get_paths(src_ip="{src}", dst_ip="{dst}", ip_proto={proto}, dst_port="{port}", '
            f'intent="PREFER_DELIVERED", include_network_functions=True, snapshot_id="{snapshot_id}")')
    resp = client.path_search.get_paths(src_ip=src, dst_ip=dst, ip_proto=proto, dst_port=str(port), intent="PREFER_DELIVERED",
                                        include_network_functions=True, max_results=3, max_return_path_results=1, snapshot_id=snapshot_id)
    routes = [dict(r) for r in client.nqe.query(data._ROUTES, snapshot_id=snapshot_id)]
    paths = (resp.info.paths if resp.info else None) or []
    first = paths[0] if paths else None
    hops = []
    for h in (first.hops or []) if first else []:
        nf = h.network_functions
        acls = [f"{a.name} {({"PERMIT": "permits", "DENY": "denies"}).get(_v(a.action), _v(a.action).lower())} ({'in' if _v(a.context) == 'INPUT' else 'out'})" for a in ((nf.acl if nf else None) or [])]
        hop = Hop(h.device_name or h.display_name or "?", h.ingress_interface or "", h.egress_interface or "",
                  [_v(b) for b in (h.behaviors or [])], acls)
        if hop.device.startswith("r"):  # the routers: say which route they used (hosts have no routing table in the model)
            hop.route = _route_for(routes, hop.device, dst)
        hops.append(hop)
    ret = (resp.return_path_info.paths if resp.return_path_info else None) or []
    back = ret[0] if ret else None
    return Trace(snapshot_id, label, src, dst, port, _v(first.forwarding_outcome) if first else "NO PATH", _v(first.security_outcome) if first else "",
                 hops, [h.device_name or "?" for h in ((back.hops or []) if back else [])], _v(back.forwarding_outcome) if back else "",
                 len(paths), _v(resp.dst_ip_location_type), call)


def _does(h: Hop) -> str:
    words = {"L3": "routed", "L2": "switched", "ACL_PERMIT": "ACL permit", "ACL_DENY": "ACL DENY", "NAT": "NAT", "PBR": "policy route"}
    return ", ".join(words.get(b, b) for b in h.behaviors) or "–"


def render(t: Trace) -> list[str]:
    out = term.heading(f"Path {t.src} → {t.dst}  tcp/{t.port}   on {t.label or 'snapshot'} {t.snapshot_id}")
    out.append(f"    → Forward SDK  {t.call}")
    means = OUTCOME_MEANS.get(t.outcome, "")
    if t.security == "DENIED" and t.outcome == "DELIVERED":
        means = "routing would deliver it, but an ACL denies it: Forward still traces the whole route so you can see where it was stopped"
    elif t.security == "DENIED":
        means = f"stopped twice over: an ACL denies it, and {means or 'it does not get through'} anyway"
    out += ["", "  " + term.paint(f"{t.outcome}" + (f" · {t.security}" if t.security else "")) + (f"   {means}" if means else ""),
            f"  Forward found {t.paths_found} path(s); the destination is located as {t.dst_location.lower().replace('_', ' ') or 'unknown'}.", ""]
    rows = []
    for i, h in enumerate(t.hops, 1):
        route = [h.route if h.route.startswith("no route") else f"route {h.route}"] if h.route else []
        why = "; ".join([*h.acls, *route]) or "–"
        rows.append([str(i), h.device, f"{h.ingress or '·'} → {h.egress or '·'}", _does(h), why])
    out += term.table(["#", "device", "in → out", "what it did", "why: the ACL and route that decided it"], rows, max_width=64)
    if t.return_hops:
        out.append(f"  Return path: {' → '.join(t.return_hops)}   " + term.paint(t.return_outcome))
    out += [""] + term.how_to_read(
        "each row is one device the packet crosses, in order. 'in → out' are its interfaces. 'what it did' is Forward's verdict "
        "for that device: routed it, or an ACL permitted or denied it. 'why' is the evidence: the ACL that matched, and the route the "
        "router used for the destination (longest prefix match in this snapshot's routing table). The last row is where it ends.")
    return out


def compare(before: Trace, after: Trace) -> list[str]:
    """What changed between two traces of the same flow, in words."""
    out = term.heading(f"What changed between {before.label} {before.snapshot_id} and {after.label} {after.snapshot_id}")
    if before.outcome != after.outcome:
        out.append("  " + term.paint(f"Outcome: {before.outcome} → {after.outcome}") + f"   ({OUTCOME_MEANS.get(after.outcome, '')})")
    else:
        out.append("  " + term.paint(f"Outcome unchanged: {after.outcome}"))
    b, a = [h.device for h in before.hops], [h.device for h in after.hops]
    out.append(f"  Path before: {' → '.join(b) or '–'}")
    out.append(f"  Path after:  {' → '.join(a) or '–'}")
    routes_b = {h.device: h.route for h in before.hops if h.route}
    rows = [[h.device, routes_b.get(h.device, "(not on the path before)"), h.route] for h in after.hops if h.route and routes_b.get(h.device) != h.route]
    if rows:
        out += ["", "  Where the routing differs on the path:"] + term.table(["device", f"route on {before.label}", f"route on {after.label}"], rows, max_width=48)
    elif before.outcome == after.outcome and b == a:
        out.append("  Same devices, same routes: this change does not touch this flow.")
    return out
