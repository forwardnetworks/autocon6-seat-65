# 3 · A change that looks safe (11:15–12:00)

*About 45 minutes. Goal: watch the pipeline stop a regression that is perfectly valid configuration, then find
exactly why.*

**Framework map.** The change is **Intent**; **Observability** is what catches it. The check going red is the Observability to Orchestrator hand-off: the pipeline refuses to continue on anything but PASS. See [the framework map](framework.md).

**Why this matters.** This is the failure the whole workshop is about: a change that is valid, three lines long and looks right in review, and it cuts off the service. The line you change is on r4. The damage lands on r2 and r3 next to it, and on r1 two hops away, where the client's traffic enters. A syntax check passes it, and a reviewer reading r4's diff has no reason to stop it. Here nothing reaches a router, so the only cost is a red check.

## Core

### Finish the job the tempting way (10 min)

In r4's configuration, all three `network` statements look alike. Retire **all** of them:

1. **Start from `main`:** click the branch name at the bottom-left → pick `main`. Then click it again → **Create new
   branch…** → `retire-all`.
2. **Edit** `candidate/r4-bgp.eos` so it ends with:
   ```
   router bgp 65004
      address-family ipv4
         no network 10.20.20.0/24
         no network 10.20.30.0/24
         no network 10.20.40.0/24
   ```
3. **Source Control:** message `Retire r4's network statements`, then **Commit** and **Publish Branch**.
4. **GitHub Pull Requests:** **Create Pull Request** from `retire-all` into `main`.

<details><summary>Or in the terminal</summary>

```
git switch main && git switch -c retire-all
# edit candidate/r4-bgp.eos
git commit -am "Retire r4's network statements" && git push -u origin HEAD && gh pr create --fill
```
</details>
It's valid EOS and a three-line diff. A syntax check or a linter would pass it.

### Watch it fail, before deploy (5 min)

The **forward/predict** check goes red. Your routers weren't touched:
```
workshop probe      # 8080 is still open on the real network
```

### See the data behind the red check (5 min)

A red check says a requirement failed. Forward can also show you **what the change did to the routing tables**, as data, without anyone opening Forward:

- On the **Your lab** page, the **Predict** step has a **What Forward says** panel: a sentence, then a table of the routes that would change.
- Or in the terminal (use the PR's prediction, or run `workshop predict` first):
  ```
  workshop data diff
  ```
  You should see something like "3 routes removed on r1, r2, r3 for 10.20.20.0/24". Look at the **before** column: those are the routes r1 uses to reach the service today. The change is on r4, and the damage is on three routers.
- Narrow it: `workshop data diff --device r1`, or `workshop data routes --prefix 10.20.20.0/24` for the baseline's view. Add `--json` for exactly what Forward returned.

### Follow the packet (5 min)

After a local `workshop predict` of this change, trace the application on both networks and compare:
```
workshop path --compare predicted
```
The baseline table shows r1 → r2 → r4 with the route each router used. The predicted one stops early, and **What changed**
names the router whose route disappeared and what it uses instead. Then try `workshop path --snapshot predicted --port 2222`:
is the management port still blocked when the service is gone?

### Investigate (20 min)

Work out what changed and why, using as many of these as you like:

- **The check summary:** which tests fail, and what Forward observed on the predicted network. Compare it with your
  `retire-one` PR: what's different?
- **The Your lab tab:** run `workshop predict` locally first. The tab then shows the service path in red, r1 with no
  route to `10.20.20.0/24`, and which advertisements the change retires.
- **The forward/advice check:** Forward AI's Reviewer summarizes what the change alters and what it would affect.
- **`workshop explain`**, after a local `workshop predict`: adds the Troubleshooter's answer.

**Find the line responsible.** Why does removing a `network` statement on r4 take a route away from r2, r3 and r1 (two hops away)? Check the routing changes Forward lists.
Which of the three statements carried the service, and how could you have known before writing the change?

### Discuss (10 min)

Forward AI helped, but did it get everything right? Asked with no context, a chat may blame the router where traffic
drops rather than the change that caused it:
```
workshop ask --prompt predicted-why-broken
workshop ask --prompt predicted-r1-routes
```
The requirements, evaluated on the predicted network, are the judge. An AI answer is advice.

### What you just proved

A real class of mistake was caught in a pull request, before deploy, by a test someone wrote down, and you can point at the line that caused it.

**At work:** think of the last change you approved because the diff looked fine. A check like this asks the question the diff can't.

**Behind?** Do steps 1 to 3 (make the change, open the PR, see it go red) and read the check's summary. Skip the AI discussion.

## Finished early?

- **Predict them all.** `workshop matrix` predicts four ways of retiring r4's statements (about two minutes) and
  prints a grid of which tests each one passes. *Before you run it,* write down which rows you expect to pass
  everything. Then compare. Add `--all` for all seven combinations. Only one row is fully green: why is "retire
  everything" not it, even though it clears every stale advertisement?
- Another "looks safe" change: retire only `10.20.20.0/24`, the one statement that *isn't* stale. Guess which tests
  fail, then check with `workshop predict`.
- `cat evidence/evidence.json` for the failed prediction: find the predicted snapshot id and the change set.
- **Why one query isn't enough.** Run the hygiene query yourself on the *predicted* network of this bad change:
  ```
  P=$(jq -r .predicted_snapshot_id evidence/evidence.json)
  python -c "
  import os; from forward_sdk import ForwardClient
  c = ForwardClient.from_env(network_id=os.environ['FORWARD_NETWORK_ID'])
  print(list(c.nqe.query(open('nqe/stale_bgp_advertisements.nqe').read(), snapshot_id='$P')))"
  ```
  Zero rows: no stale advertisements left, so `STALE-CLEARED` passes. If that query were your only check, this change
  would have been approved and the service would be down. The behavioral tests (`APP-EXISTING`, `PATH-SERVICE`) are what
  caught it.
