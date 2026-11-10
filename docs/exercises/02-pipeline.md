# 2 · Your first change through the pipeline (10:05–10:45)

*About 40 minutes. Goal: see how a change travels from Git to Forward Predict and back to the pull request, and be
able to explain every line of the result.*

**Framework map.** Your candidate file is **Intent**. Opening the pull request is the event the **Orchestrator** reacts to, and the forward/predict check is **Observability** judging a network that doesn't exist yet. Nothing here touches a router. See [the framework map](framework.md).

**Why this matters.** Most teams already review changes in pull requests. This puts a test inside the review. The code and the requirements come from `main`, not from your pull request, because a test the author can edit isn't a gate.

## Core

### Make a small change (10 min)

Start small: retire **one** stale advertisement.

> **In the editor (recommended).** The branch name sits at the bottom-left of the window (the status bar). The
> **Source Control** panel (the branching icon on the left, or Ctrl+Shift+G) commits and pushes. The **GitHub Pull
> Requests** panel (the GitHub icon on the left) creates pull requests and shows their checks.

1. **New branch:** click the branch name (`main`) at the bottom-left → **Create new branch…** → type `retire-one` →
   Enter.
2. **Edit:** open `candidate/r4-bgp.eos` in the Explorer and make it end with the lines below. Save with Ctrl+S.
   ```
   router bgp 65004
      address-family ipv4
         no network 10.20.30.0/24
   ```
3. **Commit and push:** in **Source Control**, type the message `Retire 10.20.30.0/24 on r4`, then press
   **Commit**. (If it asks to stage changes, answer **Yes**. A long message wraps and moves the Commit button down a line.) Then **Publish Branch**. A notification may also ask whether VS Code should periodically run `git fetch`: either answer is fine.
4. **Pull request:** in **GitHub Pull Requests**, press **Create Pull Request** (or the notification that offers it),
   check that it goes from `retire-one` into `main`, and press **Create**.

> **From the lab page.** On the **Predict** step, **Open pull request** does steps 1 to 4 for you: it commits only
> `candidate/r4-bgp.eos` on a new branch (never `main`), pushes it and opens the pull request. Reviewing and merging
> still happen on GitHub.

<details><summary>Or in the terminal</summary>

```
git switch -c retire-one
git commit -am "Retire 10.20.30.0/24 on r4"
git push -u origin HEAD
gh pr create --fill
```
</details>

### Follow it through the pipeline (15 min)

The **forward/predict** check starts on your PR. While it runs, read how it works:

1. `.github/workflows/forward-predict.yml`: the CI job. It checks out `main`, takes **only** `candidate/r4-bgp.eos`
   from your PR (a PR that changes any other file ends in ERROR), and runs the three predict steps you'll run yourself in a
   minute: `workshop predict changeset`, `run`, `check`, one job step each. Open the check's log on GitHub and you see all three. Code
   and requirements come from `main`, so a PR can't weaken its own test.
2. `src/workshop/predict.py`: the gate, step by step:
   - the candidate must be in scope
   - the baseline must be this lab's, and unchanged
   - the baseline tests must pass
   - then predict, and judge the **predicted** network
3. `src/workshop/forward.py`: Forward Predict, called programmatically through the Forward Python SDK:
   - `change_sets.create` against your baseline snapshot
   - `set_commands("r4", …)` with the exact candidate lines
   - `commit`, then `predict`: Forward builds the network that *would* result, as a new snapshot
   - `wait_until_processed`, then path searches and NQE queries against that predicted snapshot
4. `src/workshop/evaluate.py`: the judge. Forward answers questions; this file decides what the answers mean against
   `requirements/*.yml`. Forward never sees your YAML.
5. `src/workshop/evidence.py`: the evidence manifest. It records what was predicted, against which baseline, with which
   result, and it's kept as the check's artifact. Deploy will insist on it later.

### Read the result (5 min)

Open the PR (in the **GitHub Pull Requests** panel, or its link) and the **forward/predict** check's details. Every behavioral test should **PASS**, and `STALE-CLEARED` should **FAIL**,
because one stale advertisement is still there. A FAIL here means "not done yet", not "broken", and the gate doesn't
care which: only PASS gets through. Leave this PR open; you'll come back to it.

### Predict it yourself, one step at a time (10 min)

The check is three steps in a CI job. Run the same three here, and read what each one leaves behind:
```
workshop predict changeset
```
Checks your baseline (this lab's, unchanged, still passing its requirements), then creates a **change set** in Forward on
that baseline holding r4's lines from `candidate/r4-bgp.eos`. The evidence: the change set's id. Nothing is predicted yet.
```
workshop predict run
```
Forward applies the change set to its model and builds the network that *would* result, as a new **predicted snapshot**.
The evidence: its id, and what the change does to the routing tables. Nothing is judged yet.
```
workshop predict check
```
Every requirement in `requirements/candidate.yml`, asked of the predicted network, judged by `evaluate.py`. This writes
`evidence/evidence.json`, the file deploy will insist on later. Same answer as the PR check: `STALE-CLEARED` FAILs.

Now look at the predicted network the way you looked at the baseline:
```
workshop path --compare predicted       # the application's path, before and after
workshop data diff                      # every route the change adds or removes
```

The raw SDK calls behind those steps are in `examples/predict_by_hand.py`, if you want to read or run them bare.

### Open the box: the evidence (optional, if your check is still running)

The verdict is a file, and everything after this step trusts it. Read it:
```
jq '{status, candidate_sha256, baseline_snapshot_id, change_set_id, predicted_snapshot_id}' evidence/evidence.json
jq '.results[] | {id, status, observed}' evidence/evidence.json
```

**The exact bytes.** The hash is taken over the candidate *after* comments and indentation are removed, because those are
the bytes Forward predicted and NAPALM will send. That's why `sha256sum candidate/r4-bgp.eos` gives a different value:
```
python -c "from workshop import candidate as c; x = c.load('candidate/r4-bgp.eos'); print(x.sha256[:7]); print(x.text)"
```

**What Forward was actually asked.** Every SDK call and Forward's raw answer are saved next to the evidence:
```
P=$(jq -r .predicted_snapshot_id evidence/evidence.json)
jq '.calls[] | {call, request}' evidence/forward-$P.json
```
Each flow requirement is a `path_search.get_paths` from the client to the service on one port. Each route or
hygiene requirement is an `nqe.query`. Find `MGMT-DENY` and look at the path's hops: "blocked" means a hop on r1
says the `CLIENT-IN` ACL denied it, not just "no path".

### Discuss (with your neighbor, if there's time)

Why do the code and requirements come from `main` and not from the PR? What would a PR author gain if they didn't?

### What you just proved

A change went from a branch to a verdict without anyone reading router configs by hand, and nothing was deployed. The verdict was a failing `STALE-CLEARED`, which means "not done yet", not "broken". The gate doesn't care which: only PASS gets through.

**Behind?** This exercise can be skipped: exercise 3 needs only your baseline. Go straight there. You can read the workflow later.

## Finished early?

- **Write your own behavioral test.** r1's ACL blocks HTTPS (TCP 8443) too, but no test says it must stay blocked.
  Copy the requirements and add one:
  ```
  cp requirements/candidate.yml requirements/mine.yml
  ```
  ```yaml
    - id: HTTPS-DENY
      kind: flow
      port: 8443
      expect: deny
      deny_at: r1
      title: HTTPS stays blocked at r1
      why: Only the application port may cross r1.
  ```
  Then the predict steps with your file: `workshop predict changeset --requirements requirements/mine.yml`, then `run` and `check --requirements requirements/mine.yml`. Your test runs against the predicted network like the
  others. (The PR check keeps using `main`'s requirements, which is the point of discussion above.)
- **Try to cheat the gate.** On a scratch branch, change your candidate *and* delete `APP-EXISTING` from
  `requirements/candidate.yml`, then open a PR. The check ends in ERROR before Forward is asked anything. Then look at the
  job itself: `gh run list --workflow forward-predict.yml -L 3`, `gh run view <id> --log | grep -A6 "Take only the candidate"`,
  and the artifact deploy will use later: `gh run download <id> -n forward-predict-evidence -D /tmp/ev && jq .git /tmp/ev/evidence.json`.
  Close the PR when you're done.
- `workshop explain` on this prediction: Forward AI's plain-language summary of what the change alters and affects.
