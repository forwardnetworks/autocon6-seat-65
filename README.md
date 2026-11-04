# AutoCon6 — Know Before You Deploy

Your own lab: four routers, a client and a service, and a Forward network that models them.

**The loop:** Baseline → Change → Predict → Approve → Deploy → Verify.

| Step | Where | Command / action |
|---|---|---|
| 0. Check your setup | Codespace terminal | `workshop doctor` |
| 1. Start the lab | Codespace terminal | `workshop up` |
| 2. Baseline | Codespace terminal | `workshop baseline` |
| 3. Change | Editor → `candidate/r4-bgp.eos` | edit, commit on a branch, open a pull request |
| 4. Predict | GitHub → your pull request | read the **forward/predict** check |
| 5. Approve | GitHub | merge only a green check |
| 6. Deploy | Codespace terminal | `git pull`, then `workshop deploy --pr <number> --confirm <sha>` |
| 7. Verify | Codespace terminal | `workshop verify` |


The goal is written down in `intent.md`. The tests that prove it are in `requirements/`. The step-by-step exercises are in
[`docs/exercises/`](docs/exercises/README.md). The **Your lab** tab (port 8765) shows where you are and what to do next. Something not working? See [`docs/exercises/stuck.md`](docs/exercises/stuck.md). Want the story behind each exercise? The slides are on your seat page. Wondering where each step fits in the [NAF Framework](https://reference.networkautomation.forum/Framework/Framework/)? See [`docs/exercises/framework.md`](docs/exercises/framework.md).

## Forward AI (optional)

Forward AI can explain and advise (`workshop ask`, `workshop explain`), and the lab page has an optional corner for it. It never decides: Forward Predict and the requirements do.

The **Your lab** tab also keeps a private checklist of **challenges** (the core steps, and stretch ones like `workshop matrix`,
which predicts every way of retiring r4's statements), with hints you can reveal one level at a time.

## Beta notice

During the beta, the terminal commands you run in this Codespace, and the result of each `workshop` command, are sent to the
workshop team's log so we can see where people get stuck. Anything that looks like a password, token or seat code is masked
before it leaves your Codespace. File contents, editor keystrokes and your pull requests' text are not sent (the pull requests
are visible on GitHub anyway). It is off in any seat that has no `TELEMETRY_KEY` secret.
