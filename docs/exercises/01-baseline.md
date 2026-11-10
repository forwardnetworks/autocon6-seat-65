# 1 · What must remain true (9:20–9:50)

*About 30 minutes. Goal: know the network's good behavior before you change anything, and have Forward model it.*

**Framework map.** The **Collector** (Forward's headless collector) reads your routers and **Observability** (Forward's model) checks them against the requirements. The requirements are your **Intent**: what must stay true. See [the framework map](framework.md).

**Why this matters.** A prediction only means something against a known "before". The baseline is a snapshot of the network at a moment you can name, plus tests that say what has to stay true. If nobody writes down what must stay true, no tool can tell you a change broke it. Pipelines usually go straight to the change; this step is what makes a prediction mean something.

## Core

### The topology (5 min)

The client `10.10.10.10` sits behind **r1** (AS 65001), and the service `10.20.20.20` behind **r4** (AS 65004).
**r2** and **r3** connect them over eBGP. Local preference makes r1 → r2 → r4 the primary path. r4 advertises the
service subnet `10.20.20.0/24` to its neighbors with a BGP `network` statement.

The **Your lab** tab draws the same diamond. r4's configuration, as it is in Git:
```
cat lab/baseline/r4.eos
```

### Required and prohibited reachability (5 min)

```
workshop probe
```
From the client, **8080 is open** (the application), and **8443 and 2222 are blocked** by r1's `CLIENT-IN` ACL.

### The behavioral tests (10 min)

Those behaviors are written as tests. Read them: each one has an id, a kind (flow, route, path, nqe), what it
expects, and why it matters.
```
cat requirements/baseline.yml
cat requirements/candidate.yml
cat nqe/stale_bgp_advertisements.nqe
```
Which test would catch the service becoming unreachable? Which would catch 2222 becoming reachable? Which one checks
that the change request is actually done?

### Take the baseline by hand (15 min)

You'll do the three steps yourself: collect, upload, judge. (`workshop baseline` does all three in one go, if you're behind.)

**Prepare.** The collector normally came with your Codespace (if not, this downloads it); it writes the collector's device file,
fingerprints the live routers, and prints the note your snapshot must carry:
```
workshop baseline prepare
source $WORKSHOP_STATE_DIR/$WORKSHOP_LAB_ID/by-hand.sh
```

**1. Collect** with Forward's headless collector, the same program a customer runs on a jump host:
```
(cd $S && INSTALL4J_ADD_VM_PARAMS=-Xmx1g $COLLECTOR createsnapshot --data-sources "$SOURCES" --snapshot-generated-dir "$SNAPDIR" --snapshot-base-name baseline --threads 4 > $S/collect-by-hand.log 2>&1)
grep -E "finished with|Collected devices|Saved snapshot zip|ERROR" $S/collect-by-hand.log
ls -la $SNAPDIR
```
The collector logs every detail (about 1,600 lines: Java, SSH, each command): the full log is in `$S/collect-by-hand.log`. The
`grep` picks out the story: each router's `Pipeline execution finished with SUCCESS`, `Collected devices: [r1, r2, r3, r4]`,
and where it saved the ZIP. Open the log and search for `show` to see the commands it ran.

**2. Upload** the ZIP to your Forward network with the Python SDK, and wait while Forward builds its model. Open
`examples/upload_by_hand.py` first: it is two SDK calls, `snapshots.upload` (with your note) and `wait_until_processed`.
```
python examples/upload_by_hand.py
```
If the wait runs out, Forward is still working on it: don't upload again, just go to step 3 in a minute.

**3. Judge** it:
```
workshop baseline judge
```
Before any requirement it checks three things, and prints each: the snapshot's note says *baseline* and *your lab*;
Forward's copy of the configs hashes to the note's fingerprint (the ZIP is what the note claims); and the live routers still
hash to it (nothing changed since you collected). Then it judges the requirements.
Three different things happen, in order, and the output shows each one:

1. **Collect.** Forward's headless collector (a program in this Codespace, not on Forward's servers) logs in to the four
   routers over SSH, runs `show` commands and writes everything into one snapshot ZIP. `workshop` also fingerprints the
   running configs over NAPALM and refuses the snapshot if they differ from what the collector read.
2. **Model.** The ZIP is uploaded to your Forward network with the Python SDK (`snapshots.upload`, then
   `wait_until_processed`). Forward turns it into a model: every route, ACL and forwarding decision. The upload carries a
   note like `autocon6:baseline lab=… fp=…`; later steps read it to know which snapshot is your baseline.
3. **Judge.** Forward doesn't know your requirements. `workshop` asks Forward questions about the model (a path search for
   each flow, an NQE query for the rest) and `src/workshop/evaluate.py` compares each answer with what the YAML says it must be.

Every line should say **PASS**.

**Drill down into one path.** A requirement says PASS; this shows you why, hop by hop, with the ACL and the route each
router used:
```
workshop path               # the application, TCP 8080
workshop path --port 2222   # the blocked port: Forward says DELIVERED but DENIED. Read the r1 row to see why
```
For TCP 2222 Forward reports two separate answers: routing would deliver the packet, and security denies it at r1. Forward
traces the whole route anyway, so you can see where it would have gone. That's why `MGMT-DENY` checks for a deny *at r1*,
not just "no path".

And run the hygiene query yourself, as a table: `workshop nqe nqe/stale_bgp_advertisements.nqe`.

Read the baseline output top to bottom. It starts with what Forward modelled (how many devices, running what). Then each requirement
is a block: its **name and meaning**, **must be** (what had to be true), and **Forward saw** (what Forward's model shows,
with the path traffic takes where there is one). It ends with **What this means** and the next step.

### Open the box: the snapshot (5 min, best done while the upload waits)

```
S=$WORKSHOP_STATE_DIR/$WORKSHOP_LAB_ID      # your lab's working folder
jq '.devices' $S/data_sources.json          # what the collector was told to read (the passwords are in another part of this file)
ls -la $S/snapshots/                         # one ZIP per collection
Z=$(ls -t $S/snapshots/baseline_*.zip | head -1)
unzip -l $Z | head -20                       # one entry per router and command output, named "rN,<what>"
unzip -p $Z 'r4,configuration.txt' | sed -n '/router bgp/,/^!/p'   # r4's BGP, as Forward received it
tail -5 $S/collect-baseline.log 2>/dev/null || grep -E "finished with|Collected devices" $S/collect-by-hand.log
```

That ZIP is the whole input to Forward's model. Nothing about your network reaches Forward any other way.

Now ask Forward one question yourself, with the same SDK and the same query the workshop uses:
```
python - <<'PY'
import os
from forward_sdk import ForwardClient
c = ForwardClient.from_env(network_id=os.environ["FORWARD_NETWORK_ID"])
s = c.snapshots.latest_processed(include_predicted=False)
print(s.id, s.note)
for row in c.nqe.query(open("nqe/stale_bgp_advertisements.nqe").read(), snapshot_id=str(s.id)):
    print(dict(row))
PY
```
That is the query behind `STALE-KNOWN`. Its rows are Forward's answer; `evaluate.py` is what turned them into PASS.

`STALE-KNOWN` is the one to read. It lists r4 `network` statements whose prefixes r4 has no route for. These are
stale advertisements, and retiring them is today's change request.

### What you just proved

You know the network's good behavior in a form a machine can check, and Forward holds a model of it. Every change from here is judged against that. `STALE-KNOWN` shows one more thing: today's change request was found by a query, not by reading configs.

**At work:** pick a service you care about and write down three things that must stay true after any change: what must be reachable, what must stay blocked, and the path it should take.

**Behind?** The one must-do is `workshop baseline` with every line **PASS**. Skip the router tour and the AI prompts.

## Finished early?

- Look at the real routers. Type `enable` first, then try `show ip bgp summary`, `show ip route 10.20.20.0/24`,
  `show ip access-lists CLIENT-IN`:
  ```
  docker exec -it clab-autocon6-r1 Cli
  ```
  Then do the same on `clab-autocon6-r4` with `show running-config section router bgp`.
- Ask Forward AI about the snapshot (a minute or two each, one at a time):
  ```
  workshop ask --prompt baseline-stale
  workshop ask --prompt baseline-service-path
  workshop ask --prompt baseline-mgmt-deny
  ```
  Compare each answer with what you saw on the routers.
