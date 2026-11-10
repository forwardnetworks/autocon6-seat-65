"""Upload your snapshot to Forward by hand: the two SDK calls behind `workshop baseline`'s upload.

    workshop baseline prepare && source $WORKSHOP_STATE_DIR/$WORKSHOP_LAB_ID/by-hand.sh
    (cd $S && INSTALL4J_ADD_VM_PARAMS=-Xmx1g $COLLECTOR createsnapshot --data-sources "$SOURCES" --snapshot-generated-dir "$SNAPDIR" --snapshot-base-name baseline --threads 4 > $S/collect-by-hand.log 2>&1)
    python examples/upload_by_hand.py
    workshop baseline judge
"""

import glob
import os

from forward_sdk import ForwardClient

c = ForwardClient.from_env(network_id=os.environ["FORWARD_NETWORK_ID"])
zip_path = max(glob.glob(os.environ["SNAPDIR"] + "/baseline_*.zip"))  # the newest ZIP the collector wrote

# 1. Upload. process=True: Forward starts building its model straight away. The note is how later steps find your baseline.
snap = c.snapshots.upload(zip_path, note=os.environ["NOTE"], process=True, wait=False)
print("uploaded", os.path.basename(zip_path), "as snapshot", snap.id)

# 2. Wait while Forward turns the ZIP into a model. If this runs out of time, Forward is still working: don't upload again.
done = c.snapshots.wait_until_processed(snap.id, poll_interval=3, timeout=900,
                                       on_poll=lambda s, secs: print(f"  {getattr(s.state, 'value', s.state)} ({secs:.0f}s)"))
print("processed:", done.id, "· next: workshop baseline judge")
