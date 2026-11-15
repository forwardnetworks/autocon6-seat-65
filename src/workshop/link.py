"""Exercise 1's change: shut r2's link to r4, then bring it back.

The point is the Forward way of seeing a change: a snapshot before, the change, a snapshot after, and what Forward says
changed between them. This change is chosen because it is undone exactly (``no shutdown`` leaves the configuration as it
was) and because it shows what a diff alone can't: the service still works (traffic reroutes through r3), but the route
and the path changed, and the PATH-SERVICE intent check ("through r2") fails while APP-EXISTING still passes.

Like every change after bring-up it goes through NAPALM (``executor.apply``: an EOS config session, the router's own diff,
an atomic commit). The advanced track makes the same change by hand on the router (``docker exec -it clab-autocon6-r2
Cli``), outside any tool, and sees that Forward finds it just the same.
"""

from __future__ import annotations

from workshop import executor, lab
from workshop.config import Settings

ROUTER, INTERFACE, PEER = "r2", "Ethernet2", "r4"


def lines(state: str) -> list[str]:
    if state not in ("down", "up"):
        raise ValueError("say down or up")
    return [f"interface {INTERFACE}", "shutdown" if state == "down" else "no shutdown"]


def change(settings: Settings, state: str, log) -> str:
    """Shut (down) or restore (up) r2's link to r4 over NAPALM. Returns the router's own diff ("" if it already was)."""
    host = lab.mgmt_ips(settings)[ROUTER]
    cfg = lines(state)
    applied = executor.apply(settings, ROUTER, host, cfg)
    return applied.output
