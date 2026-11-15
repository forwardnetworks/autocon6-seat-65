"""Forward locations for the lab's routers, so Forward's connectivity diff says *which sites* lost or gained reachability.

Without locations every subnet is in Forward's "default" location and the connectivity diff is one default→default count.
With them it reads like a network: "Branches → Service lost 3 subnet pairs". Each router stands for the site behind it
(the hosts' subnets belong to the router they hang off; see lab/topology.yml).

Forward places only devices it knows as the network's collection sources ("Unrecognized devices cannot be moved"), and a
network fed by headless-collector uploads has none. So each router is also registered as a classic device with
``collect: false`` and no credentials: a name Forward can place, never something a Forward collector logs in to.
Idempotent, by name; runs after every baseline (next to the intent checks). Display only: a Forward error never fails a step.
"""

from __future__ import annotations

from workshop import lab

# router -> (location, lat, lng). Coordinates only place the pins on Forward's map.
PLACES = {
    "r1": ("Client edge", 32.2226, -110.9747),
    "r2": ("Branches", 33.4484, -112.0740),
    "r3": ("Data centre", 36.1699, -115.1398),
    "r4": ("Service", 37.3382, -121.8863),
}


def names(client) -> dict[str, str]:
    """Location id -> name on the client's network ({} if there are none)."""
    return {str(loc.id): loc.name for loc in client.network_locations.get_locations() or []}


def ensure(client, settings, log=None) -> dict[str, str]:
    """Locations, classic-device registrations and the atlas, as PLACES says. Returns router -> location name."""
    from workshop import show

    def say(text: str) -> None:
        if log:
            show.sdk(log, text)

    cd = client.classic_devices
    say("networks.ensure_locations(" + ", ".join(p[0] for p in PLACES.values()) + ")   creates or fixes them by name; sends nothing when they match")
    client.networks.ensure_locations({name: {"lat": lat, "lng": lng} for name, lat, lng in PLACES.values()})
    known = {d.name for d in (cd.get_classic_devices().devices or [])}
    missing = [r for r in PLACES if r not in known]
    if missing:  # Forward's atlas only places devices it knows as the network's classic devices
        ips = lab.mgmt_ips(settings)
        say(f"classic_devices.add_classic_devices({', '.join(missing)}, collect=False)   names Forward can place; never collected by Forward")
        cd.add_classic_devices(body=[{"name": r, "host": ips[r], "type": "arista_eos_ssh", "collect": False} for r in missing])
    have = names(client)
    atlas = {k: have.get(str(v), str(v)) for k, v in (client.network_locations.get_atlas() or {}).items()}
    want = {r: p[0] for r, p in PLACES.items()}
    if any(atlas.get(r) != name for r, name in want.items()):
        say("networks.set_device_locations(" + ", ".join(f"{r}: {n}" for r, n in want.items()) + ")")
        client.networks.set_device_locations(want)
    return want
