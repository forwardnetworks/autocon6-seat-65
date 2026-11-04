# 5 · Deploy, collect, compare (12:30–12:50)

*About 20 minutes. Goal: the exact predicted change on the routers, and proof that reality matched the prediction.*

**Framework map.** The **Executor** (NAPALM) writes the exact bytes Predict judged. Then the **Collector** and **Observability** run again on the real network, and the result is compared with the prediction. See [the framework map](framework.md).

**Why this matters.** Approval is only worth something if what gets deployed is what was approved. Deploy checks that the pull request is merged, that the candidate and the requirements haven't changed, that your baseline is current and that the routers haven't drifted. Then verify closes the loop: it compares what was predicted with what is really there.

## Core

### Deploy what was predicted (8 min)

Make sure you are on `main` (bottom-left; deleting the local branch in the last exercise already switched you), then pull the merge: click the circular-arrows icon next to the branch name in the status bar and press **OK**. In the terminal:
```
workshop deploy --pr <number> --confirm <first 7 characters of the candidate sha256>
```
The exact command, with your PR number and `--confirm` value filled in, is at the top of the green **forward/predict**
check's summary: copy it from there.
If the confirmation doesn't match, deploy refuses and prints the value it expects. Before writing anything, it checks that:

- the PR is merged and its check passed
- the candidate and requirements are unchanged since the prediction
- your baseline is still the latest in Forward
- the routers haven't drifted from it

It then sends r4 exactly the lines Predict evaluated, over NAPALM, and reads them back. NAPALM stages them in a configuration session, so the router itself tells you the diff (`-` lines removed, `+` added) before the commit makes it live. A line the router refuses stops the whole session, so a change never half applies.

### Verify (10 min)

```
workshop verify
```
Verify does four things:
1. probes the real flows (8080 open, 2222 blocked)
2. collects the network again with the headless collector
3. reruns every test on the new snapshot
4. compares each result with its prediction

**MATCH** closes the loop: Baseline → Change → Predict → Approve → Deploy → Verify.

### What you just proved

The model said which tests would pass, and the real network, collected again, agreed. That is the evidence that lets you trust the next prediction. `MATCH` isn't only a pass, it's a measurement of how good the prediction was.

**At work:** this is the part you can keep even if you change every tool: write down what you expect, deploy, and compare.

**What really changed.** After `workshop verify`, run `workshop data diff --against post`: the routing changes between the baseline and the network as it is now, from Forward's model of the collected result. Compare it with the prediction's `workshop data diff`. For the fixed change, both say the same thing: no route changed, because the two stale advertisements were never in anyone's routing table.

**Behind?** Deploy is three steps: switch to `main` and sync, run the command from the green check's summary, then `workshop verify`.

## Finished early?

- **Make it drift.** Change r4 by hand (`docker exec -it clab-autocon6-r4 Cli`, then `enable`, `configure`,
  `router bgp 65004`, `address-family ipv4`, `network 10.20.50.0/24`), then run `workshop verify` again. What does it
  say, and which test catches the hand edit? A deploy started now would refuse too: the routers no longer match the
  baseline that Predict used. Put things back with `workshop restore && workshop baseline`.
- `workshop ask --prompt post-still-stale`: ask Forward AI about the network after the change.
