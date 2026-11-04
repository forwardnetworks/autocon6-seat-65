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

### Take the baseline (10 min)

```
workshop baseline
```
The headless collector reads the four routers into a snapshot, Forward processes it into a model, and the
baseline tests run against that model. Every line should say **PASS**.

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
