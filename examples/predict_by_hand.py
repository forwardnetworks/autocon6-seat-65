"""Forward Predict, by hand: the same SDK calls the forward/predict check makes, one at a time, printed as they happen.

    python examples/predict_by_hand.py                 # predicts candidate/r4-bgp.eos against your baseline
    python examples/predict_by_hand.py --port 2222     # and traces another flow on the predicted network

This is for learning, not a gate: the pull request's forward/predict check is still what decides. Read it top to bottom;
every Forward call is one line. Compare what it prints with `workshop predict` and with the check on your pull request.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from forward_sdk import ForwardClient

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8080)
args = ap.parse_args()

# 0. The candidate, normalized the way the workshop does: no comments, no blank lines, no indentation. These are the exact
#    lines Forward will model (and the lines deploy would send to r4).
text = Path("candidate/r4-bgp.eos").read_text()
lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("!")]
print("candidate lines:", lines)

# 1. Connect with your seat's token (from the Codespace's environment).
c = ForwardClient.from_env(network_id=os.environ["FORWARD_NETWORK_ID"])

# 2. Your baseline: the newest snapshot that is not a prediction. Its note says which lab and which fingerprint it is.
base = c.snapshots.latest_processed(include_predicted=False)
print(f"baseline: snapshot {base.id}  note: {base.note!r}")
if not re.match(r"autocon6:baseline ", base.note or ""):
    raise SystemExit("the newest snapshot is not a baseline: run `workshop baseline` first")

# 3. A change set on top of that baseline, with the candidate's lines for r4.
cs = c.change_sets.create("by-hand", snapshot_id=str(base.id), description="predict_by_hand.py")
print(f"change set {cs.id} created on snapshot {base.id}")
cs.set_commands("r4", "\n".join(lines) + "\n")
print("set_commands(r4) done")

# 4. Commit it, then ask Forward to build the network that would result. That network is a new snapshot.
cs.commit("by hand")
started = cs.predict("by hand")
print(f"predict started: the predicted snapshot will be {started.id}")
pred = c.snapshots.wait_until_processed(str(started.id), poll_interval=3, timeout=600,
                                        on_poll=lambda s, secs: print(f"  ... {getattr(s.state, 'value', s.state)} ({secs:.0f}s)"))
print(f"predicted snapshot {pred.id} is ready (parent {pred.parent_snapshot_id})")

# 5. Ask the predicted network a question: does the client still reach the service on this port, and how?
for label, snap in (("baseline", base.id), ("predicted", pred.id)):
    r = c.path_search.get_paths(src_ip="10.10.10.10", dst_ip="10.20.20.20", ip_proto=6, dst_port=str(args.port),
                                intent="PREFER_DELIVERED", include_network_functions=True, max_results=1, snapshot_id=str(snap))
    p = (r.info.paths or [None])[0] if r.info else None
    hops = " -> ".join(h.device_name or "?" for h in (p.hops or [])) if p else "(no path)"
    outcome = f"{getattr(p.forwarding_outcome, 'value', p.forwarding_outcome)}/{getattr(p.security_outcome, 'value', p.security_outcome)}" if p else "NO PATH"
    print(f"{label:>9}: tcp/{args.port}  {outcome:<22} {hops}")

# 6. Clean up: the change set was only for this experiment.
cs.delete()
print("change set deleted. The predicted snapshot stays in Forward: `workshop path --snapshot", pred.id, "` traces it hop by hop.")
