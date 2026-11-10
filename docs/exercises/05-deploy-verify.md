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

It then sends r4 exactly the lines Predict evaluated, over NAPALM, and reads them back. NAPALM stages them in an EOS
configuration session, asks the router for the session's diff (`-` lines removed, `+` added), and commits the session.
A line the router refuses aborts the whole session, so a change never half applies. Deploy prints the router's diff
after the commit. Nobody approves it there: the approval was the merged pull request, bound to these exact bytes.

**Open the box first (3 min, before you deploy).** Do by hand what NAPALM is about to do, and throw it away:
```
docker exec -it clab-autocon6-r4 Cli
enable
configure session preview
router bgp 65004
address-family ipv4
no network 10.20.30.0/24
show session-config diffs
abort
```
`show session-config diffs` is the router's own diff of a change that isn't live yet; `abort` discards it. NAPALM's
`compare_config` is that command, and `commit_config` is `commit` instead of `abort`.

Also try the guard: `workshop deploy --pr <number> --confirm 0000000` refuses and prints the value it expects.

### Verify (10 min)

```
workshop verify
```
Verify does four things:
1. probes the real flows (8080 open, 2222 blocked)
2. collects the network again with the headless collector
3. reruns every test on the new snapshot
4. compares each result with its prediction

The output is laid out so you can read it as evidence, top to bottom: the probes first, then every requirement as a block
(what it must be, what Forward saw on the real network, and "predicted PASS, collected PASS: as predicted"), then **what
really changed in the routing tables** between the baseline and now, then **What this means**. A `DIFFERENT from the
prediction` line is the thing to look for.

**MATCH** closes the loop: Baseline → Change → Predict → Approve → Deploy → Verify.

**Open the box: the records.** Deploy and verify each leave a file:
```
S=$WORKSHOP_STATE_DIR/$WORKSHOP_LAB_ID
jq '{lines, device_diff, readback_problems}' $S/deploy.json      # what was sent, and the router's own diff
jq '.rows[] | {id, predicted, collected}' $S/verify.json          # each requirement: predicted against collected
docker exec clab-autocon6-client nc -z -w 3 10.20.20.20 8080; echo "exit $?"   # the probe verify ran: 0 means open
docker exec clab-autocon6-client nc -z -w 3 10.20.20.20 2222; echo "exit $?"   # non-zero: blocked
```

### What you just proved

The model said which tests would pass, and the real network, collected again, agreed. That is the evidence that lets you trust the next prediction. Be precise about what `MATCH` compares: each requirement's verdict (PASS or FAIL) predicted against collected, plus the live probes. It does not compare every route; `workshop data diff --against post` below is how you look at those.

**At work:** this is the part you can keep even if you change every tool: write down what you expect, deploy, and compare.

**What really changed.** After `workshop verify`, run `workshop data diff --against post`: the routing changes between the baseline and the network as it is now, from Forward's model of the collected result. Compare it with the prediction's `workshop data diff`. For the fixed change, both say the same thing: no route changed, because the two stale advertisements were never in anyone's routing table.

**Behind?** Deploy is three steps: switch to `main` and sync, run the command from the green check's summary, then `workshop verify`.

## Finished early?

- **Make it drift.** Change r4 by hand (`docker exec -it clab-autocon6-r4 Cli`, then `enable`, `configure`,
  `router bgp 65004`, `address-family ipv4`, `network 10.20.50.0/24`), then run `workshop verify` again. What does it
  say, and which test catches the hand edit? Note what *didn't* catch it: the PR check never touches your routers, so it
  only sees what was collected. Verify and deploy are the steps that read the live network. Put things back with
  `workshop restore && workshop baseline` (restore is the one change path with no prediction: it is the break-glass step,
  and that's why a new baseline must follow).
- `workshop ask --prompt post-still-stale`: ask Forward AI about the network after the change.
