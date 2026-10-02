# 5 · Deploy, collect, compare (12:30–12:50)

*About 20 minutes. Goal: the exact predicted change on the routers, and proof that reality matched the prediction.*

## Core

### Deploy what was predicted (8 min)

Switch to `main` (bottom-left) and **Sync Changes** in Source Control to pull the merge, then in the terminal:
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

It then sends r4 exactly the lines Predict evaluated, over Netmiko, and reads them back.

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

**Behind?** Deploy is three steps: switch to `main` and sync, run the command from the green check's summary, then `workshop verify`.

## Finished early?

- **Make it drift.** Change r4 by hand (`docker exec -it clab-autocon6-r4 Cli`, then `enable`, `configure`,
  `router bgp 65004`, `address-family ipv4`, `network 10.20.50.0/24`), then run `workshop verify` again. What does it
  say, and which test catches the hand edit? A deploy started now would refuse too: the routers no longer match the
  baseline that Predict used. Put things back with `workshop restore && workshop baseline`.
- `workshop ask --prompt post-still-stale`: ask Forward AI about the network after the change.
