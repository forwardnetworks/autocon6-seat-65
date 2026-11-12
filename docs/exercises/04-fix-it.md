# 4 · Fix it, and let it through the gate (12:00–12:30)

*About 30 minutes. Goal: a corrected change whose prediction passes every test, and an approval.*

**Framework map.** You change the **Intent**, **Observability** re-judges it, and your merge is the human approval the **Orchestrator** waits for, bound to the candidate's hash. See [the framework map](framework.md).

**Why this matters.** Catching the problem is half of it. The other half is that the fix goes through the same gate. Nobody gets a shortcut around it: not you, not a deadline, not an AI draft.

## Core

### Correct the change (10 min)

On your `retire-all` branch (check the bottom-left), correct `candidate/r4-bgp.eos` so it retires only the stale
advertisements: delete the `no network 10.20.20.0/24` line and keep these two under `address-family ipv4`:
```
      no network 10.20.30.0/24
      no network 10.20.40.0/24
```
Save. (The answer is in `solutions/fixed-candidate.eos`.) Then, in **Source Control**, commit with the message
`Keep the live service advertisement` and press **Sync Changes** to push. VS Code asks you to confirm it: press **OK**.

<details><summary>Or in the terminal</summary>

```
git commit -am "Keep the live service advertisement" && git push
```
</details>

The **forward/predict** check re-runs by itself. This is a regression test on every commit.

### Prove it (10 min)

When it's green, open the check summary again (PR → **Details** next to forward/predict → **Summary**). All five rows
in its table should be **PASS**:

- The required reachability still works (APP-EXISTING)
- The prohibited port is still blocked (MGMT-DENY)
- r1 still has the service route through r2 (ROUTE-SERVICE)
- Traffic takes the same path (PATH-SERVICE)
- No stale advertisements remain (STALE-CLEARED)

### Approve (10 min)

In this repository, merging is the approval. There's no separate approve button here; in real life you'd also require
a reviewer. The repository only allows the merge once **forward/predict** has passed.

1. Wait for **forward/predict** to go green. GitHub can take a minute or two to start it: press **Refresh** at the top
   of the pull request page now and then. Merge as soon as forward/predict is green. **forward/advice** is advisory
   (Forward AI's review), can take a few minutes, and isn't required.
2. In the **GitHub Pull Requests** panel, open the PR and press **Merge Pull Request**. (The same button is on the
   PR's page on github.com.)
3. It opens a form with the commit message. Scroll down and press **Squash and Merge**. Squash means your branch's
   commits become one commit on `main`.
4. VS Code then asks what to delete: choose **Delete Local Branch** (not *Suspend Codespace*, which stops your lab).
   That also switches you back to `main`.
5. Optional: if you have a `retire-one` PR from exercise 2, try merging it to see the refusal, then close it.

### What you just proved

The only way to merge is a green check, so approval is a person looking at evidence and not a hunch. That holds no matter who wrote the change or when.

**Behind?** No red PR of your own? Make the fix directly: retire only the two stale advertisements, open the PR, and merge it when it is green.

## Finished early?

- Run your own `HTTPS-DENY` test (exercise 2) against the fixed change:
  the predict steps with `--requirements requirements/mine.yml` (on `changeset` and `check`).
- `workshop agent`: on a scratch branch, put the broken change back in the candidate and let the advisers iterate to
  a passing change. It stops before approval, so compare its answer with yours.
