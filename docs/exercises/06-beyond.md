# 6 · Beyond the lab (12:50–1:00)

**Framework map.** Every block here has a swap point: a different Collector or Executor, a second source of Intent, a different Orchestrator. [The framework map](framework.md) lists where each one lives and what is vendor-specific today.

## Monday morning

You don't need the whole pipeline to start. Pick one kind of change you make often and write three requirements for it:
what must stay reachable, what must stay blocked, and what path it should take. Run Predict on every pull request,
report-only at first, and make the check required once you trust it. Keep AI as an adviser: the requirements decide.

## The same pattern, bigger

- **More tests, same gate.** Each requirement is a behavioral test: a flow, a route, a path, an NQE query. A
  regression suite is more of them, run on every change.
- **Any pipeline.** The gate is a CLI with exit codes (0 PASS; FAIL, ERROR, INCONCLUSIVE and STALE all stop). Drop it
  into the CI you already run. [`examples/gitlab-ci.yml`](../../examples/gitlab-ci.yml) is a sketch for GitLab.
- **More autonomy.** Let agents draft the change and iterate against Predict, and keep approval with a person.

**Not tested here: what to validate first.** Everything above ran on four cEOS routers, one change at a time, one
attendee per lab. Before you believe it bigger, find out: how long collection and Predict take on your network, how
many changes are in flight at once (here, a new collection makes every open PR STALE), which of your platforms and
change types Predict models correctly (it got an ACL edit wrong once; see [how it works](how-it-works.md#b-a-time-the-model-was-wrong)),
and what an agent does when the check keeps failing.

## Take it further (stretch, or after the workshop)

Reset first: `workshop reset --to 5` (a netlab redeploy and a fresh baseline).

| Adviser | Try | What it is |
|---|---|---|
| Analyst | `workshop ask --list`, then any prompt | a Forward AI chat grounded in one snapshot |
| Author | `workshop propose` (then `--write`) | Predict's config assist drafting the candidate from `intent.md` |
| Reviewer | `workshop explain` after a prediction | diff and impact summaries of the predicted change |
| Troubleshooter | `workshop explain` after a failed prediction | a chat on the predicted network, given the failures |
| All of them | `workshop agent` | draft → predict → advice → draft again, stopping before approval |

- Ask a what-if: *"If r2 failed, which path would traffic to 10.20.20.20 take?"* How would you **prove** the answer
  with a prediction instead of trusting it?
- **Bring your own agent.** Claude Code, Copilot or anything else is welcome here. `AGENTS.md` gives it the rules.

## For sceptics

You've done the loop and you don't trust it yet. Good. [How it works, and where it stops](how-it-works.md) is the
long version; these are ways to poke at it yourself. Start each from a fresh baseline: `workshop reset --to 5` (it
writes the fixed candidate and collects a new baseline).

1. **Write your own requirement.** Copy `requirements/candidate.yml` to `requirements/scratch.yml` (keep `set: candidate`,
   don't commit it) and add a `route` requirement: r1 reaches `10.20.20.0/24` via r3. Find r3's address on the r1 link
   with `netlab inspect --node r3` (from `lab/`), and give it an id like `ROUTE-VIA-R3`. Run the three steps, with
   the same flags on each: `workshop predict changeset --requirements requirements/scratch.yml --out evidence/scratch`, then `run` and `check`.
   *Expected: FAIL on your requirement only: r1's route to the service is via r2 (10.255.0.2), which local preference prefers.*
2. **Try to deploy it.** `workshop deploy gates --evidence evidence/scratch/evidence.json --no-github --confirm <sha7>`
   with a scratch run that passes (drop your failing requirement).
   *Expected: `NOT DEPLOYED (STALE): requirements/candidate.yml changed since the prediction; re-run the check`. Deploy
   compares the evidence with `requirements/candidate.yml`'s hash, so evidence judged against any other file can't
   deploy. (The message says the file changed; really, the evidence was judged against a different one.)*
3. **Drift.** Predict the fixed candidate (`workshop predict changeset`, `run`, `check`). Then change r4 by hand:
   `docker exec -it clab-autocon6-r4 Cli`, then `enable`, `configure`, `router bgp 65004`, `address-family ipv4`,
   `no network 10.20.40.0/24`, `end`. Now `workshop deploy gates --evidence evidence/evidence.json --no-github --confirm <sha7>`.
   *Expected: `NOT DEPLOYED (STALE): the routers' configs are not what was collected for the prediction; run the baseline
   steps (`workshop baseline collect` first) and re-run the check`, exit 4.* Put it back with `workshop restore`, then
   the four baseline steps.
4. **Find a requirement that passes for the wrong reason.** Read the table in [how it works, section c](how-it-works.md#c-requirement-kinds-exactly).
   Two hints: a `route` passes if *any* next hop matches, and an `nqe` with `expect_rows: 0` passes whenever the query
   returns nothing. Copy `nqe/stale_bgp_advertisements.nqe` to `nqe/scratch.nqe`, change
   `where device.platform.os == OS.ARISTA_EOS` to `where device.name == "r9"`, and run both on the baseline:
   `workshop nqe nqe/stale_bgp_advertisements.nqe` and `workshop nqe nqe/scratch.nqe`.
   *Expected: 2 rows, then 0. A requirement on your copy with `expect_rows: 0` would PASS on a network that still has
   both stale advertisements. A test that can't fail isn't a test.*
5. **Make the model and the router disagree.** Run `workshop deploy render --evidence evidence/evidence.json` after a
   passing prediction, and read both diffs.
   *Expected: they agree. Then read [the ACL story](how-it-works.md#b-a-time-the-model-was-wrong) for when they didn't,
   and note that render compares text, not behavior.*
6. **Read the gate.** `src/workshop/deploy.py`, function `gates`, is about 30 lines. Check each refusal against
   [section e](how-it-works.md#e-the-gates-and-what-approval-means-here) and find the gap between the last gate and
   the write.
   *Expected: there is one. The fingerprint is read, then the commit happens a moment later, with no compare-and-set.*

## Before you try this at work

These are questions for your network and for Forward, not facts we checked:

- Which of your vendors and platforms the collector and the model support, and for which kinds of change. Ask Forward.
- Where the collector runs, what it can reach, and how it gets credentials (here: one container, lab passwords in a
  0600 file).
- How often you collect. The gate needs a baseline that is current; decide what "current" means for you.
- Licensing and capacity for the devices you'd model. Ask Forward.
- Who approves. Here it's whoever merges a green check, with zero required reviewers.

## Tell us how it went

If you are testing this before the event, send Craig what was unclear, slow or broken, with your seat number. At the event,
tell a proctor on your way out.
