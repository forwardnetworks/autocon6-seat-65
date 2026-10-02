# 3 · A change that looks safe (11:15–12:00)

*About 45 minutes. Goal: watch the pipeline stop a regression that is perfectly valid configuration, then find
exactly why.*

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

### Investigate (20 min)

Work out what changed and why, using as many of these as you like:

- **The check summary:** which tests fail, and what Forward observed on the predicted network. Compare it with your
  `retire-one` PR: what's different?
- **The Your lab tab:** run `workshop predict` locally first. The tab then shows the service path in red, r1 with no
  route to `10.20.20.0/24`, and which advertisements the change retires.
- **The forward/advice check:** Forward AI's Reviewer summarizes what the change alters and what it would affect.
- **`workshop explain`**, after a local `workshop predict`: adds the Troubleshooter's answer.

**Find the line responsible.** Why does removing a `network` statement on r4 take a route away from r1, two hops away?
Which of the three statements carried the service, and how could you have known before writing the change?

### Discuss (10 min)

Forward AI helped, but did it get everything right? Asked with no context, a chat may blame the router where traffic
drops rather than the change that caused it:
```
workshop ask --prompt predicted-why-broken
workshop ask --prompt predicted-r1-routes
```
The requirements, evaluated on the predicted network, are the judge. An AI answer is advice.

**Behind?** Do steps 1 to 3 (make the change, open the PR, see it go red) and read the check's summary. Skip the AI discussion.

## Finished early?

- **Predict them all.** `workshop matrix` predicts four ways of retiring r4's statements (about two minutes) and
  prints a grid of which tests each one passes. *Before you run it,* write down which rows you expect to pass
  everything. Then compare. Add `--all` for all seven combinations. Only one row is fully green: why is "retire
  everything" not it, even though it clears every stale advertisement?
- Another "looks safe" change: retire only `10.20.20.0/24`, the one statement that *isn't* stale. Guess which tests
  fail, then check with `workshop predict`.
- `cat evidence/evidence.json` for the failed prediction: find the predicted snapshot id and the change set.
