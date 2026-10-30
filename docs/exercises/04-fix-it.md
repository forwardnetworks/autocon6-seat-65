# 4 · Fix it, and let it through the gate (12:00–12:30)

*About 30 minutes. Goal: a corrected change whose prediction passes every test, and an approval.*

## Core

### Correct the change (10 min)

On your `retire-all` branch (check the bottom-left), correct `candidate/r4-bgp.eos` so it retires only the stale
advertisements. Then, in **Source Control**, commit with the message `Keep the live service advertisement` and press
**Sync Changes** to push. VS Code asks you to confirm it: press **OK**.

<details><summary>Or in the terminal</summary>

```
git commit -am "Keep the live service advertisement" && git push
```
</details>

The **forward/predict** check re-runs by itself. This is a regression test on every commit.

### Prove it (10 min)

When it's green, read the summary again. Confirm each of these:

- The required reachability still works (APP-EXISTING)
- The prohibited port is still blocked (MGMT-DENY)
- r1 still has the service route through r2 (ROUTE-SERVICE)
- Traffic takes the same path (PATH-SERVICE)
- No stale advertisements remain (STALE-CLEARED)

### Approve (10 min)

Merge the pull request: in the **GitHub Pull Requests** panel, open it and press **Merge Pull Request** (choose
**Squash and Merge**). The same button is on the PR's page on github.com. The repository only allows the merge once
**forward/predict** has passed. Try merging `retire-one` to see the refusal, then close `retire-one`.
Wait for the checks to finish: GitHub can take a few minutes to start them, so press **Refresh** at the top of the pull request page now and then until it says **2 successful checks**. Pressing **Merge Pull Request** opens a form with the commit message; scroll down and press **Squash and Merge** to finish. VS Code then asks what to delete: choose **Delete Local Branch** (not *Suspend Codespace*, which stops your lab). That also switches you back to `main`.

**Behind?** No red PR of your own? Make the fix directly: retire only the two stale advertisements, open the PR, and merge it when it is green.

## Finished early?

- Run your own `HTTPS-DENY` test (exercise 2) against the fixed change:
  `workshop predict --requirements requirements/mine.yml`.
- `workshop agent`: on a scratch branch, put the broken change back in the candidate and let the advisers iterate to
  a passing change. It stops before approval, so compare its answer with yours.
