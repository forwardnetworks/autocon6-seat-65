# 2 · Your first change through the pipeline (9:50–10:45)

*About 55 minutes. Goal: see how a change travels from Git to Forward Predict and back to the pull request, and be
able to explain every line of the result.*

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
   **Commit**. (If it asks to stage changes, answer **Yes**.) Then **Publish Branch**.
4. **Pull request:** in **GitHub Pull Requests**, press **Create Pull Request** (or the notification that offers it),
   check that it goes from `retire-one` into `main`, and press **Create**.

<details><summary>Or in the terminal</summary>

```
git switch -c retire-one
git commit -am "Retire 10.20.30.0/24 on r4"
git push -u origin HEAD
gh pr create --fill
```
</details>

### Follow it through the pipeline (25 min)

The **forward/predict** check starts on your PR. While it runs, read how it works:

1. `.github/workflows/forward-predict.yml`: the CI job. It checks out `main`, takes **only** `candidate/r4-bgp.eos`
   from your PR (a PR that touches anything else fails), and runs `workshop predict`. Code and requirements come from
   `main`, so a PR can't weaken its own test.
2. `src/workshop/predict.py`: the gate, step by step:
   - the candidate must be in scope
   - the baseline must be this lab's, and unchanged
   - the baseline tests must pass
   - then predict, and judge the **predicted** network
3. `src/workshop/forward.py`: Forward Predict, called programmatically through the Forward Python SDK:
   - `change_sets.create`
   - `set_commands` for r4
   - `predict_and_wait`
   - then path searches and an NQE query against the predicted snapshot
4. `src/workshop/evidence.py`: the evidence manifest. It records what was predicted, against which baseline, with which
   result, and it's kept as the check's artifact. Deploy will insist on it later.

### Read the result (10 min)

Open the PR (in the **GitHub Pull Requests** panel, or its link) and the **forward/predict** check's details. Every behavioral test should **PASS**, and `STALE-CLEARED` should **FAIL**,
because one stale advertisement is still there. A FAIL here means "not done yet", not "broken", and the gate doesn't
care which: only PASS gets through. Leave this PR open; you'll come back to it.

The same judgement runs locally, too:
```
workshop predict
cat evidence/evidence.json
```

### Discuss (10 min)

Why do the code and requirements come from `main` and not from the PR? What would a PR author gain if they didn't?

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
  Then `workshop predict --requirements requirements/mine.yml`. Your test runs against the predicted network like the
  others. (The PR check keeps using `main`'s requirements, which is the point of discussion above.)
- `workshop explain` on this prediction: Forward AI's plain-language summary of what the change alters and affects.
