# How it works, and where it stops

This page is for people who already run Ansible or Nornir, already have a CI system, and want to know what's actually
going on before they believe any of it. Everything here comes from the code in `src/workshop/` or from
`docs/api-contract.md` in the workshop repository (not copied into your seat repo), the record of what Forward was
tested to do while we built this. Where something wasn't tested, it says so.

## a. What Predict is, and what it isn't

The headless collector logs in to r1-r4 over SSH, runs show commands, and writes a ZIP of configs and command output.
Forward builds a model from that: forwarding tables, ACLs, BGP state, as Forward computes them from what was collected.
Path searches and NQE queries run against that model.

Predict takes a **change set** (CLI lines for a device, here r4), applies it to the model of a baseline snapshot, and
builds a **predicted snapshot**: the model of a network that doesn't exist yet. Same queries, different snapshot. No
router is touched.

It's static analysis of forwarding and control-plane state. It doesn't tell you:

- how long convergence takes, or what happens during it
- anything about timing, load, queues or hardware limits
- whether the platform has a bug that makes the device do something other than what its config says

What was verified here is narrow: cEOS 4.36, four routers, eBGP, and changes that **remove** `network` statements. The
api-contract records a caveat from the Go SDK docs that on some builds an **added** `network` origination is discarded by
Predict; we never tested additions, so the scope check refuses them (`candidate.py`).

## b. A time the model was wrong

The original scenario edited an ACL entry on r1 by resending sequence number 10 with a different port. Two things went
wrong (api-contract, go/no-go #3):

1. Real cEOS rejects that line (`% Error: Duplicate sequence number`) and leaves the old entry alone.
2. Predict, on the development build we were using then, accepted it and modelled **both** entries:
   `10 permit ... eq 8080` and `10 permit ... eq 8443`. A config no real router could hold. The correct idiom
   (`no 10`, then re-add) made the prediction fail outright on that build.

That was a from-source build, not the release fwd.app runs, and we didn't retest the ACL case on the release. We
switched scenarios instead. But it's the reason the loop doesn't stop at Predict:

- **`workshop deploy render`** applies the same lines twice: Forward's change set (r4's config in the model, before and
  after) and the router's own `compare_config` in an EOS config session that is then discarded. `render.py` compares the
  **changed lines** of the two diffs. If they differ, what Predict judged isn't what the router would do.
- **`workshop verify`** collects the network again after the deploy, judges the same requirements on that real
  collection, and runs live probes from the client container.

Be precise about what each one proves. Render proves **text agreement**: the model and the router change the same
lines. It says nothing about behavior. It's also a step you read, not a gate: it exits 0 either way, and `deploy apply`
doesn't re-run it. Verify's requirement checks are still Forward's model, built from a fresh collection; the probes
(`nc -z` from the client) are the only real packets in the loop.

## c. Requirement kinds, exactly

From `requirements.py` (validation) and `evaluate.py` (judging). Unknown fields, a missing title or an empty set fail at
load time, so a typo can't silently check nothing.

| Kind | What it asks Forward | PASS when | Watch out for |
|---|---|---|---|
| `flow`, `expect: permit` | one path search, src/dst/proto/port, intent `PREFER_VIOLATIONS` (the paths most likely to break it) | **every** path returned is `DELIVERED` and `PERMITTED` | it doesn't care which way the traffic went: that's what `path` is for |
| `flow`, `expect: deny` | one path search, intent `PREFER_DELIVERED` (the paths most likely to get through) | **every** path is `DENIED` **and** has an `ACL_DENY` hop on the `deny_at` device | a blackhole isn't a deny: unreachable fails |
| `path` | one path search, `PREFER_DELIVERED` | the set of distinct **delivered** hop sequences is exactly the one listed | ECMP (two delivered paths) fails; non-delivered paths are ignored if any path is delivered |
| `route` | NQE over `afts.ipv4Unicast.ipEntries` for one device and prefix | **any** next hop has the given IP and protocol BGP | it doesn't say that next hop is the only one, or the one the flow uses |
| `nqe` | your query file, as written | the row count equals `expect_rows` exactly | `expect_rows: 0` also passes when the query is wrong and matches nothing |

For both flow kinds: no paths, a timed-out search, or an outcome string the code doesn't know is INCONCLUSIVE, never
PASS. Any exception while asking is ERROR.

## d. The five status words

| Status | Exit | Raised by | Means |
|---|---|---|---|
| PASS | 0 | `evaluate.py` | every requirement had positive evidence |
| FAIL | 1 | `evaluate.py` | at least one requirement's evidence contradicts it (and none errored) |
| ERROR | 2 | `evaluate.py` (an exception while asking); `predict.py` (candidate out of scope, malformed requirements, Forward's copy of the baseline doesn't hash to its note, a Forward error, the prediction built from the wrong parent); `deploy.py` (evidence not a live PASS, wrong lab or network, wrong `--confirm`, the PR's check didn't pass, the lock is held) | we couldn't get an answer we trust |
| INCONCLUSIVE | 3 | `evaluate.py` (no path, timeout, unknown outcome; an empty set); `predict.py` (Forward timed out) | no evidence either way |
| STALE | 4 | never `evaluate.py`. `predict.py` (no baseline, the newest snapshot isn't this lab's baseline or is a post-change collection, the baseline requirements don't PASS, the requirements file changed between steps); `deploy.py` (candidate or `requirements/candidate.yml` changed, the PR merged at another commit, a newer snapshot exists, the routers drifted) | the evidence was true about a network or a file that isn't current any more |

The overall verdict is ERROR if any requirement errored, else FAIL if any failed, else INCONCLUSIVE if any was, else
PASS. Only PASS is deployable. `verify` uses its own words: MATCH (exit 0), MISMATCH (1), and INCOMPLETE (3) when the
collection failed after the change was applied.

## e. The gates, and what "approval" means here

`workshop deploy gates` (and `apply`, again, just before it writes) checks, in `deploy.py`:

1. `--confirm` is the first 7 hex characters of the candidate's sha256 (of the normalized lines, so comments and spacing
   don't count). You name the change you reviewed.
2. The evidence is `mode: live`, `status: PASS`, and has a predicted snapshot.
3. Its lab id and Forward network id are this lab's.
4. The candidate file at the evidence's `candidate_path` hashes to `candidate_sha256`.
5. `requirements/candidate.yml` hashes to `requirements_sha256`.
6. With `--pr`: the PR is merged, its head commit is the one the evidence was predicted on, and `forward/predict` passed on
   that commit. (`--no-github` skips this; it's for rehearsals.)
7. The evidence's baseline is still the newest non-predicted snapshot in Forward.
8. The live running configs of r1-r4, over NAPALM, hash to the baseline's fingerprint.

What it doesn't do, honestly:

- **The lock is a local `flock`** on a file in the state directory. It stops two `workshop` commands on the same
  machine from writing at once (`deploy apply`, `deploy render`). It doesn't stop a person on the router CLI, and
  `restore` doesn't take it.
- **Gates, then write.** The fingerprint is read, then the change is committed a moment later. There's no atomic
  compare-and-set on the device, so a change in that gap isn't caught (verify would see it).
- **No automatic rollback.** If verify says MISMATCH, nothing undoes the change. `workshop restore` is the recovery: it
  re-applies r4's baseline advertisements over NAPALM, with no prediction and no gate, and you take a new baseline after.
- **The evidence isn't signed.** With `--pr` it comes from the artifact of a successful `forward/predict` run on the PR's
  head commit. With `--evidence --no-github` it's a local file and deploy trusts what it says.

**The merge rule.** Seat repos get a ruleset from the admin tooling (`seatd/internal/ghadmin/ghadmin.go` in the workshop
repository): changes to the default branch only
through a pull request, `forward/predict` required, squash merge only, no force-push or deletion, **0 required
approving reviews**, and the status check is not strict (the branch needn't be up to date with main). Admins can bypass;
attendees are invited with `push`, not admin, so they can't.

So in the workshop, "approval" is the person who clicks merge on a green check. In production I'd want more than that.
These are suggestions, **not tested here**:

- required reviewers and a `CODEOWNERS` file, so the author can't approve their own change
- a merge queue, or strict status checks, so the check ran against what main actually is at merge time
- artifact attestations on the evidence, so deploy can verify where it came from
- a confirmed commit on the device (EOS `configure session ... commit timer`, or your platform's equivalent), so a
  change that cuts off management rolls itself back

## f. What "drift" means

`fingerprint.py` hashes the `show running-config` text of r1-r4. Before hashing it drops blank lines, `!` comment lines
and trailing spaces, and replaces the value after `secret`, `password` or `key` with `<redacted>` (the collector stores
secrets that way, so the live and collected copies have to match). The same function hashes the collector's ZIP,
Forward's copy of the snapshot and the live routers.

So drift means **the configuration text changed**. A link that went down, a BGP session that flapped, a route learned
from somewhere new: none of that changes the fingerprint. Those show up in a new collection, not in the gate.

## g. The evidence file

`evidence/evidence.json`, written by `workshop predict check` (`evidence.py`, `Manifest`). "Deploy checks" means
`deploy.py` refuses on it.

| Field | Deploy checks it? | What it is |
|---|---|---|
| `status`, `mode`, `predicted_snapshot_id` | yes | only `live` + `PASS` + a predicted snapshot is deployable; replay can never deploy |
| `lab_id`, `network_id` | yes | must be this lab and this Forward network |
| `candidate_sha256` | yes | `--confirm` and the candidate file must match it |
| `candidate_path` | yes, indirectly | where deploy finds the candidate file to hash |
| `candidate_text` | used, not checked | the lines `deploy apply` sends. Deploy doesn't re-hash it against `candidate_sha256` |
| `requirements_sha256` | yes | must equal `requirements/candidate.yml`'s hash |
| `baseline_snapshot_id` | yes | must still be the newest snapshot |
| `baseline_fingerprint` | yes | the live routers must hash to it |
| `git.pr`, `git.head_sha` | yes, with `--pr` | the PR must be merged at this commit, with the check passed |
| `git.repo`, `base_sha`, `run_id`, `actor` | no | provenance |
| `change_set_id` | no (render reads it) | the Forward change set |
| `results`, `reason` | no | every requirement's answer, and the summary |
| `requirements_path`, `baseline_status`, `baseline_processed_at` | no | context |
| `forward_release`, `workshop_version`, `created_at`, `schema` | no (`schema` must be 1 to read the file) | versions and time |

## h. One change in flight

There's one candidate file (`candidate/r4-bgp.eos`), so one change at a time. The baseline gate does the rest: deploy
needs the evidence's baseline to be the newest snapshot in Forward, and any new collection (a baseline, or verify's
post-change collection) is newer. So every open PR predicted against the old baseline goes STALE at deploy the moment
anyone collects, and the fix is to re-run the check. That's the safe failure, and it's also why this doesn't scale past
one change per lab without rethinking it.

## i. What to keep, what's scaffolding

| Keep: the pattern | Replace: workshop scaffolding |
|---|---|
| Evidence bound to the candidate's hash, the baseline and the requirements' hash | One candidate file, one device |
| Requirements read from the base branch, so a PR can't edit its own test | The `no network` scope check in `candidate.py` |
| A CLI with distinct exit codes per outcome | `--confirm` with 7 hex characters as the "I reviewed it" step |
| A pinned workflow (actions by commit SHA) with `contents: read` | Seat tokens, one Forward network per attendee |
| Only a live PASS deploys; empty, unknown and timed-out are never green | netlab and containerlab as the network |
| Predict, then render, then verify: model, device diff, reality | The state directory and its JSON step files |

## j. Copying the workflow

`.github/workflows/forward-predict.yml` in your seat repository is the part most people will want. The trust boundary:

- `on: pull_request` (not `pull_request_target`), and `permissions: contents: read`.
- It checks out the **base** commit. Code and requirements come from there.
- It fetches the PR head, fails (exit 2) if the PR changes anything outside `candidate/`, and copies only
  `candidate/r4-bgp.eos` from the head.
- Every action is pinned to a commit SHA. Dependencies install with `uv sync --frozen` from the base branch's lockfile.
- The job only runs when the PR's head repo is this repo. **Fork PRs never run the check**, so they can never merge
  under the ruleset. That's deliberate: seat repos have one attendee, and a fork wouldn't have the seat's Forward token
  anyway. If you take PRs from forks, you'll need a different design for secrets; we didn't build one.
- The three predict steps are separate job steps, and the evidence is uploaded as an artifact even when they fail.

[`examples/gitlab-ci.yml`](../../examples/gitlab-ci.yml) is the same three steps for GitLab. It's untested.

## k. Next to the tools you already use

I'm not going to do a feature comparison. A few things that are true and checkable:

- **Forward** builds its model on a server, from device state that its collector reads over SSH. Predict applies a change
  to that model. Here it's driven only through the Forward Python SDK.
- **Batfish** also analyzes configurations offline. We didn't use or test it here, so I won't compare results.
- **SuzieQ** collects and queries operational state. Also not used here.
- **pyATS** is a test framework that runs against live devices. Not used here; our live checks are the `nc` probes.
- **Nornir and NAPALM** are executors: they push changes. This workshop uses NAPALM for that role (`executor.py`). Nornir
  on top would work the same way, but we didn't try it.

The pattern doesn't depend on which model you use. A test stage that judges behavior, evidence bound to a hash, a gate
that only opens on PASS, and a check after deploy: you can build that around any of them.
