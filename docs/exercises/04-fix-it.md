# 4 · Fix it, and let it through the gate (12:00–12:30)

*About 30 minutes. Goal: a corrected change whose prediction passes every test, and an approval.*

**Framework map.** You change the **Intent**, **Observability** re-judges it, and your merge is the human approval the **Orchestrator** waits for, bound to the candidate's hash. See [the framework map](framework.md).

**Why this matters.** Catching the problem is half of it. The other half is that the fix goes through the same gate. Nobody gets a shortcut around it: not you, not a deadline, not an AI draft.

## Core

### Correct the change (10 min)

On your `retire-all` branch (check the bottom-left; if you opened exercise 3's PR from the Your lab page, it's the `change-…` branch the page made), correct `candidate/r4-bgp.eos` so it retires only the stale
advertisements: delete the `no network 10.20.20.0/24` line and keep these two under `address-family ipv4`:
```
      no network 10.20.30.0/24
      no network 10.20.40.0/24
```
Save. (Stuck on which line? `@wsa4` gives hints, and the answer if you ask for it outright.) Then, in **Source Control**, commit with the message
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

**You are the approver.** In this repository, merging is the approval: read the green check's summary (what Predict
judged, the change request at the top of the Change analysis), then merge. There's no separate approve button here; in
real life you'd also require a second reviewer. The repository only allows the merge once **forward/predict** has passed.

1. Wait for **forward/predict** to go green. GitHub can take a minute or two to start it: press **Refresh** at the top
   of the pull request page now and then. Merge as soon as forward/predict is green. **forward/advice** is advisory
   (Forward AI's review), can take a few minutes, and isn't required.
2. In the **GitHub Pull Requests** panel, open the PR and scroll to the bottom of its overview: **Merge Pull Request**
   *using method* **Squash and Merge** (already chosen). Press **Merge Pull Request**. (On github.com it's the PR page's
   **Squash and merge** button.)
3. It opens a form with the commit message. Scroll down and press **Squash and Merge**. Squash means your branch's
   commits become one commit on `main`.
4. VS Code then asks what to delete: **Delete local branch** is already ticked and *Suspend Codespace* isn't (leave it
   that way: it would stop your lab). Press **OK**. That also switches you back to `main`. Then pull the merge: the circular-arrows icon next to the branch name in the
   status bar (or `git pull`).
5. Optional: if you still have a `retire-one` PR from exercise 2, look at it: GitHub won't merge it, because its
   forward/predict check is red (it may also say the branch has conflicts now; that's the merge you just made). Close it.

*On github.com instead:* the PR's **Conversation** tab, the green **Squash and merge** button at the bottom, then
**Confirm squash and merge**. Then in your Codespace: `git switch main` and `git pull`.
*Hold My Beer (terminal):* `gh pr merge <N> --squash`, then `git switch main && git pull`. (`--squash` matters: this
repository refuses plain merge commits.)

### What you just proved

The only way to merge is a green check, so approval is a person looking at evidence and not a hunch. That holds no matter who wrote the change or when.

**Behind?** No red PR of your own? From `main`, make the fix on a new branch: tick only the two stale prefixes on the Your lab page's Change stage and press **Open pull request** (it makes the branch for you), or `git switch -c fix-it`, edit, commit, push, `gh pr create --fill`. Merge it when it is green.

## Finished early?

- Run your own `HTTPS-DENY` test (exercise 2) against the fixed change:
  the predict steps with `--requirements requirements/mine.yml` (on `changeset` and `check`).
- `workshop agent`: on a scratch branch, put the broken change back in the candidate and let the advisers iterate to
  a passing change. It stops before approval, so compare its answer with yours.

## Go deeper

Forward AI's [limits and scope](https://docs.fwd.app/latest/application/forward-ai/limits-and-scope/) are why the requirements decide and AI only advises. To see the gate refuse on purpose and then back out cleanly: [SQ12](side-quests.md#sq12--deploy-it-anyway-then-back-it-out). More: [Go deeper](go-deeper.md).
