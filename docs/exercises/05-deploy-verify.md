# 5 · Deploy, collect, compare (12:30–12:50)

*About 20 minutes. Goal: the exact predicted change on the routers, and proof that reality matched the prediction.*

**Framework map.** The **Executor** (NAPALM) writes the exact bytes Predict judged. Then the **Collector** and **Observability** run again on the real network, and the result is compared with the prediction. See [the framework map](framework.md).

**Why this matters.** Approval is only worth something if what gets deployed is what was approved. Deploy checks that the pull request is merged, that the candidate and the requirements haven't changed, that your baseline is current and that the routers haven't drifted. Then verify closes the loop: it compares what was predicted with what is really there.

## Core

### Deploy what was predicted, one step at a time (10 min)

Make sure you are on `main` (bottom-left; deleting the local branch in the last exercise already switched you), then pull the merge: click the circular-arrows icon next to the branch name in the status bar and press **OK**.

The green **forward/predict** check's summary (PR → **Details** → **Summary**) has all four commands written out with
your values, ready to copy. They look like this:
```
workshop deploy gates --pr <N> --confirm <sha7>
workshop deploy render --pr <N>
workshop deploy apply --pr <N> --confirm <sha7>
workshop deploy readback
```

| Placeholder | What it is | Example | Where it comes from |
|---|---|---|---|
| `<N>` | your merged pull request's number | `4` | the PR's title on GitHub (`#4`) |
| `<sha7>` | the first 7 characters of the candidate's sha256: the hash of the exact bytes Predict judged. Not a git commit | `3f9a1c2` | the check summary, or `workshop predict check` |

`gates` and `apply` need both flags. `render` needs only `--pr`. `readback` takes none: it reads what the last apply sent.
Four steps:

**1. Gates.** Every check, and nothing sent:
```
workshop deploy gates --pr <N> --confirm <sha7>
```
It fetches the check's evidence from GitHub, then confirms, one line each: the evidence is a PASS for this lab; the
candidate and requirements are the ones predicted; the PR is merged with its check green on that commit; your baseline is
still the newest in Forward; and the four routers still match it (nobody changed one by hand). Try `--confirm 0000000`:
it refuses and prints the value it expects.

**2. Render: one change, two applications.** This is the dry run that shows Forward's model and the real router read
your lines the same way, before anything is written.
```
workshop deploy render --pr <N>
```
The same lines, applied two ways, side by side:
- **inside Forward**: the commands the change set holds for r4, and r4's configuration in Forward's model before and after;
- **on the router**: NAPALM stages the lines in an EOS configuration session, asks r4 for its own diff
  (`compare_config`), and throws the session away (`discard_config`). Nothing on r4 changes.

Then it checks the two agree. They should: Forward's model and the router read the same lines the same way. If they
didn't, that's the moment to stop: what Predict judged would not be what the router does.

**3. Apply.**
```
workshop deploy apply --pr <N> --confirm <sha7>
```
The gates once more (they must hold at the moment of the write), then the same session for real: load, compare, and
`commit_config`. A line the router refuses aborts the whole session, so a change never half applies. Nobody approves it
here: the approval was the merged pull request, bound to these exact bytes.

**4. Read it back.**
```
workshop deploy readback
```
`show running-config section router bgp` on r4, checked line by line against what was sent.

**Open the box first (optional, 3 min, before you apply).** Do by hand what NAPALM does, and throw it away:
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
`compare_config` is that command, `discard_config` is `abort`, and `commit_config` is `commit`. Then `exit`.

You must type `abort`. If you leave without it, the `preview` session stays open on r4, and NAPALM won't start its own
session while another one is pending: `render` and `apply` fail with "Session is already in use" (the workshop only
cleans up NAPALM's own leftover sessions, not yours). To clear it:
```
docker exec clab-autocon6-r4 Cli -p 15 -c "configure session preview abort"
```

### Verify, one step at a time (10 min)

```
workshop verify probe       # real packets from the client container: 8080 open, 2222 blocked
workshop verify collect     # the headless collector again, on the network as it is now
workshop verify upload      # into Forward, noted as a post-change snapshot
workshop verify wait        # Forward builds its model of the real, changed network
workshop verify compare     # every requirement on that model, verdict by verdict against the prediction
```
`compare` lays it out as evidence: every requirement as a block (what it must be, what Forward saw on the real
network, and "predicted PASS, collected PASS: as predicted"), then **what really changed in the routing tables** between
the baseline and now, then **What this means**. A `DIFFERENT from the prediction` line is the thing to look for.

The last line should start `verify: MATCH`. That closes the loop: Baseline → Change → Predict → Approve → Deploy → Verify.

Don't expect the network to look different. Retiring stale advertisements removes no live route, so every path and
route is the same as before. That's the good result.

**Open the box: the records (optional).** Deploy and verify each leave a file:
```
S=$WORKSHOP_STATE_DIR/$WORKSHOP_LAB_ID
jq '{lines, device_diff, readback_problems}' $S/deploy.json      # what was sent, and the router's own diff
jq '.rows[] | {id, predicted, collected}' $S/verify.json          # each requirement: predicted against collected
docker exec clab-autocon6-client nc -z -w 3 10.20.20.20 8080; echo "exit $?"   # what verify probe ran (through the Docker SDK): 0 means open
docker exec clab-autocon6-client nc -z -w 3 10.20.20.20 2222; echo "exit $?"   # non-zero: blocked
```

### What you just proved

The model said which tests would pass, and the real network, collected again, agreed. That is the evidence that lets you trust the next prediction. Be precise about what `MATCH` compares: each requirement's verdict (PASS or FAIL) predicted against collected, plus the live probes. It does not compare every route; `workshop data diff --against post` below is how you look at those.

**At work:** this is the part you can keep even if you change every tool: write down what you expect, deploy, and compare.

**What really changed.** After `workshop verify compare`, run `workshop data diff --against post`: the routing changes between the baseline and the network as it is now, from Forward's model of the collected result. Compare it with the prediction's `workshop data diff`. For the fixed change, both say the same thing: no route changed, because the two stale advertisements were never in anyone's routing table.

**Behind?** Switch to `main` and sync, then `workshop deploy gates`, `apply` and `readback` with the values from the green check's summary (skip `render`), then the five verify steps.

## Finished early?

- **What the gates, render and MATCH do and don't protect**, and a time Forward's model was wrong: [How it works](how-it-works.md).
- **Make it drift.** Change r4 by hand (`docker exec -it clab-autocon6-r4 Cli`, then `enable`, `configure`,
  `router bgp 65004`, `address-family ipv4`, `network 10.20.50.0/24`), then run the verify steps again. What does it
  say, and which test catches the hand edit? Note what *didn't* catch it: the PR check never touches your routers, so it
  only sees what was collected. Verify and deploy are the steps that read the live network. Put things back with
  `workshop restore`, then the baseline steps (restore is the one change path with no prediction: it is the break-glass step,
  and that's why a new baseline must follow).
- `workshop ask --prompt post-still-stale`: ask Forward AI about the network after the change.
