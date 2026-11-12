# 3 · A change that looks safe (11:15–12:00)

*About 45 minutes. Goal: watch the pipeline stop a regression that is perfectly valid configuration, then find
exactly why.*

**Framework map.** The change is **Intent**; **Observability** is what catches it. The check going red is the Observability to Orchestrator hand-off: the pipeline refuses to continue on anything but PASS. See [the framework map](framework.md).

**Why this matters.** This is the failure the whole workshop is about: a change that is valid, three lines long and looks right in review, and it cuts off the service. The line you change is on r4. The damage lands on r2 and r3 next to it, and on r1 two hops away, where the client's traffic enters. A syntax check passes it, and a reviewer reading r4's diff has no reason to stop it. Here nothing reaches a router, so the only cost is a red check.

## Core

### Finish the job the tempting way (10 min)

In r4's configuration, all three `network` statements look alike. Retire **all** of them:

1. **Start from `main`.** Your new branch must start from `main`, not from `retire-one`. Click the branch name at the
   bottom-left and pick `main` first. Then click it again → **Create new branch…** → `retire-all`.
   Using the lab page instead? Switch to `main` first all the same. **Open pull request** makes a fresh branch only when
   you're on `main`; on any other branch it adds your commit to that branch (and its PR).
2. **Edit** `candidate/r4-bgp.eos`. Under `address-family ipv4`, add three lines:
   ```
         no network 10.20.20.0/24
         no network 10.20.30.0/24
         no network 10.20.40.0/24
   ```
   Save. The end of the file now reads:
   ```
   router bgp 65004
      address-family ipv4
         no network 10.20.20.0/24
         no network 10.20.30.0/24
         no network 10.20.40.0/24
   ```
   (The same lines are in `solutions/bad-candidate.eos`.)
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

The **forward/predict** check goes red. This time it's not "not done yet": `APP-EXISTING`, `ROUTE-SERVICE` and
`PATH-SERVICE` fail. The line you changed is on r4, but the damage lands on r1, where the client's traffic enters.

Your routers weren't touched. `workshop probe` sends real packets on the real network, not Forward's model:
```
workshop probe      # 8080 is still open
```

### See the data behind the red check (5 min)

A red check says a requirement failed. Forward can also show you **what the change did to the routing tables**, as data, without anyone opening Forward:

- On the **Your lab** page, the **Predict** step has a **What Forward says** panel: a sentence, then a table of the routes that would change.
- Or in the terminal. First run the three predict steps on this branch, so your Codespace has this change's
  prediction (the PR check's prediction lives on GitHub):
  ```
  workshop predict changeset
  workshop predict run
  workshop predict check
  workshop data diff
  ```
  You should see something like "3 routes removed on r1, r2, r3 for 10.20.20.0/24". Look at the **before** column: those are the routes r1 uses to reach the service today. The change is on r4, and the damage is on three routers.
- Narrow it: `workshop data diff --device r1`, or `workshop data routes --prefix 10.20.20.0/24` for the baseline's view. Add `--json` for exactly what Forward returned.

### Follow the packet (5 min)

After the local predict steps for this change, trace the application on both networks and compare:
```
workshop path --compare predicted
```
The baseline table shows r1 → r2 → r4 with the route each router used. The predicted one stops early, and **What changed**
names the router whose route disappeared and what it uses instead. Then try `workshop path --snapshot predicted --port 2222`:
is the management port still blocked when the service is gone?

### Investigate (15 min)

Work out what changed and why, in this order:

1. **The check summary** (PR → **Details** next to forward/predict → **Summary**): which tests fail, and what Forward
   observed on the predicted network. If you have a `retire-one` PR, compare: what's different?
2. **`workshop data diff`** (you ran it above): which routes disappear, and on which routers.
3. **The question.** Why does removing a `network` statement on r4 take a route away from r2, r3 and r1 (two hops
   away)? Which of the three statements carried the service, and how could you have known before writing the change?

<details><summary>Answer</summary>

`network 10.20.20.0/24` carried the service. It's the one statement with a connected route behind it (the subnet on
r4's service-facing interface), so it's the only one r4 actually advertised. Retire it and r4 stops telling r2 and r3
about the service; they stop telling r1; r1 has no route left. You could have known from exercise 1: the NQE query
listed only `10.20.30.0/24` and `10.20.40.0/24` as stale, and `workshop data routes --prefix 10.20.20.0/24` showed r4
with it connected.
</details>

The **Your lab** tab shows the same thing once the local predict steps have run: the service path in red, r1 with no
route to `10.20.20.0/24`, and which advertisements the change retires.

### Optional: what does Forward AI say? (instructor demo)

The **forward/advice** check on your PR is Forward AI's Reviewer: what the change alters and what it would affect.
`workshop explain` adds the Troubleshooter's answer. Did it get everything right? Asked with no context, a chat may
blame the router where traffic drops rather than the change that caused it:
```
workshop ask --prompt predicted-why-broken
workshop ask --prompt predicted-r1-routes
```
The requirements, evaluated on the predicted network, are the judge. An AI answer is advice.

### What you just proved

A real class of mistake was caught in a pull request, before deploy, by a test someone wrote down, and you can point at the line that caused it.

**At work:** think of the last change you approved because the diff looked fine. A check like this asks the question the diff can't.

**Behind?** Make the change, open the PR, see it go red, and read the check's summary and the answer above. Skip Forward AI.

## Finished early?

- **How the requirements decide**, exactly (and how a check can pass for the wrong reason): [How it works, §c](how-it-works.md).
- **Predict them all.** `workshop matrix` predicts four ways of retiring r4's statements (about two minutes) and
  prints a grid of which tests each one passes. *Before you run it,* write down which rows you expect to pass
  everything. Then compare. Add `--all` for all seven combinations. Only one row is fully green: why is "retire
  everything" not it, even though it clears every stale advertisement?
- Another "looks safe" change: retire only `10.20.20.0/24`, the one statement that *isn't* stale. Guess which tests
  fail, then check with the predict steps.
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
