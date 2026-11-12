# 1 · Meet Forward, and write down what must remain true (9:20–10:05)

*About 45 minutes. Goal: get Forward a model of your network, find your way around it, then turn the network's good behavior into tests.*

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

### The behavioral tests (5 min)

Those behaviors are written as tests. You'll run them against Forward's model at the end of this exercise. Read them: each one has an id, a kind, what it
expects, and why it matters. The **kind** says what sort of question Forward is asked: `flow` (can this traffic get
through, on this port?), `route` (does this router have this route, via this next hop?), `path` (which routers does the
traffic cross?) or `nqe` (a query over the model; here, "are there stale advertisements?").
```
cat requirements/baseline.yml
cat requirements/candidate.yml
cat nqe/stale_bgp_advertisements.nqe
```
Which test would catch the service becoming unreachable? Which would catch 2222 becoming reachable? Which one checks
that the change request is actually done?

<details><summary>Answers</summary>

- The service becoming unreachable: `APP-EXISTING` (and `ROUTE-SERVICE` and `PATH-SERVICE` would go with it).
- 2222 becoming reachable: `MGMT-DENY`.
- The change request done: `STALE-CLEARED`, in `requirements/candidate.yml`. It runs the same query as `STALE-KNOWN`
  and expects zero rows. On today's network it fails, which is why it's only in the candidate tests.
</details>

### Take the baseline, one step at a time (10 min)

Four commands. Each does one thing, shows you the real call it makes, and leaves evidence the next one picks up.

**1. Collect.** Forward's headless collector (a program in this Codespace, the same one a customer runs on a jump host)
logs in to r1-r4 over SSH, runs `show` commands, and writes one snapshot ZIP:
```
workshop baseline collect
```
It also takes a **fingerprint**: one hash of all four routers' configs. It takes it twice, live over NAPALM before it
collects and again from the configs inside the ZIP, so we can prove nothing changed between collecting and judging. A
router that changed mid-collection is caught. The evidence: the ZIP's path and size, and the fingerprint.

**2. Upload** the ZIP to your Forward network with the Python SDK (`snapshots.upload`). The note it carries,
`autocon6:baseline lab=… fp=…`, is how every later step knows which snapshot is your baseline and what it holds:
```
workshop baseline upload
```

**3. Wait** while Forward turns the ZIP into a model: the routes, ACLs and forwarding paths of the four routers it collected, as Forward models them.
```
workshop baseline wait
```
It shows Forward's own progress (its queue, then each processing stage) and a line every 20 seconds or so. A minute is normal.

Want to run the collector and the upload yourself? `workshop baseline prepare`, then the commands it prints (the
collector, then `python examples/upload_by_hand.py`), then `workshop baseline judge`.

### Meet Forward (15 min)

**Do two of these; the rest are extras if you have time.** `workshop path` and the NQE query are the two to pick if
you're unsure.

Before you test anything, look around. Forward now holds a model of your network, and most of you haven't used one
before. Everything here is a question to Forward through the Python SDK; each command prints the call it makes.

**What did Forward find?**
```
workshop data inventory
```
Four routers and their OS. The client and service are small Linux containers the collector doesn't log in to: Forward
knows them as the subnets on r1's and r4's client- and service-facing interfaces.

**What does each router know?**
```
workshop data routes --prefix 10.20.20.0/24     # the service's prefix, on every router
workshop data routes --device r1                 # everything r1 knows
```
Each row is a route as Forward modelled it: the prefix, the protocol, the next hop and interface. r4 has it connected;
r2 and r3 learned it from r4 over BGP; r1 learned it over BGP too and uses r2 as the next hop (local preference 200
makes the r2 path the primary one).

**Where does a packet actually go?**
```
workshop path                # client → service, TCP 8080
workshop path --port 2222    # the management port
```
That's Forward's path search (`path_search.get_paths`): every hop, what each router did with the packet, and the ACL and
the route behind each decision. For 2222 Forward gives two answers: routing *would* deliver it, and r1's `CLIENT-IN` ACL
*denies* it. It traces the full route anyway, so you can see where it would have gone.

**Ask it anything: NQE.** NQE is Forward's query language over the model: every device, interface, route and line of
config is data you can query.
```
cat nqe/stale_bgp_advertisements.nqe
workshop nqe nqe/stale_bgp_advertisements.nqe
```
That query looks at r4's BGP `network` statements and r4's routes, and lists the statements with no route behind them.
Two come back: `10.20.30.0/24` and `10.20.40.0/24`. They advertise nothing, they look exactly like the live
`10.20.20.0/24` line, and nobody would spot them by reading configs. Retiring them is today's change request.
The third statement, `10.20.20.0/24`, is live because a connected route is behind it: it's the subnet on r4's
service-facing interface.

Write your own. One that lists every interface and whether it is up:
```
workshop nqe 'foreach d in network.devices foreach i in d.interfaces select {device: d.name, interface: i.name, admin: i.adminStatus, oper: i.operStatus}'
```
Change it: only `oper` not up, only r1, add `i.description`. It answers in a second, for four routers or four thousand.

**The one idea to take away:** none of this touched a router. It is all Forward's model, built from the ZIP you
collected. That model is what Predict will change, in exercise 2, to show you a network that doesn't exist yet.

### Check it: the baseline requirements (5 min)

```
workshop baseline check
```
Before any requirement it checks three things and prints each: the snapshot's note says *baseline* and *your lab*;
Forward's copy of the configs hashes to the note's fingerprint (the ZIP is what the note claims); and the live routers
still hash to it (nothing changed since you collected). Then, for each requirement, it asks Forward a question (the same
path searches and NQE you just ran) and `src/workshop/evaluate.py` compares the answer with what the YAML says it must be.
Forward answers questions; `evaluate.py` judges.

Every line should say **PASS**.

Read the `baseline: PASS` line near the end and the **What this means** under it first. Then work up: the table is the
summary, and the blocks below it are the proof. Each block is one requirement: its **name and meaning**, **must be**
(what had to be true), and **Forward saw** (what the model shows, with the path where there is one).

If any line says **FAIL**, see [If something goes wrong](stuck.md). The most likely cause: `workshop up` hadn't
finished when you collected.

### What you just proved

You know the network's good behavior in a form a machine can check, and Forward holds a model of it. Every change from here is judged against that. `STALE-KNOWN` shows one more thing: today's change request was found by a query, not by reading configs.

**At work:** pick a service you care about and write down three things that must stay true after any change: what must be reachable, what must stay blocked, and the path it should take.

**Behind?** The one must-do is `workshop baseline check` with every line **PASS** (after collect, upload, wait). Do two of the Meet Forward commands (`workshop path` and the NQE query) and skip the rest.

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
- <details><summary>Open the box: the snapshot (optional, Python and shell)</summary>

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
  </details>
