# What each step leaves behind, and what it is for

Every step ends with its evidence, and most write a file or a record in Forward. This is the whole list, in the order the loop makes
them. **Evidence** is something a later step checks. **Record** is something a person reads (a reviewer, a change board, you).
The commands print an `Artifact:` or `Evidence:` line naming each one as it is written.

| Step | Artifact | Where | Kind | What it means in the workflow |
|---|---|---|---|---|
| `workshop up` | the lab's configuration pieces | `lab/node_files/` | reference | What netlab rendered and sent to each router. The starting point everything is compared with. |
| `baseline collect` | the device list | state: `data_sources.json` (mode 0600, holds the lab login) | input | Which routers the headless collector logs in to. Never in Git. |
| `baseline collect` | the snapshot ZIP | state: `snapshots/baseline_*.zip` | evidence | Every router's command output, as collected. Its configs are hashed into the **fingerprint**. |
| `baseline upload`, `wait` | a **Forward snapshot** noted `autocon6:baseline lab=… fp=…` | your Forward network | evidence | Forward's model of the network as it is. The note is how every later step finds it and knows what it holds. |
| `baseline check` | `baseline.json` | state | evidence | The baseline requirements judged PASS on that snapshot. Predict refuses to start from a baseline that does not pass. |
| `baseline check` | raw answers | state: `forward-<snapshot>.json` | record | Exactly what Forward returned to each question (path searches, NQE). The lab page's "structured" views. |
| `predict changeset` | a **change set** | your Forward network | evidence | Your candidate's lines held in Forward against the baseline. Nothing is predicted yet. |
| `predict run` | a **predicted snapshot** | your Forward network | evidence | The network as it would be with your change. Nothing on a router changed. |
| `predict check` | `evidence/evidence.json` | repository folder (the pull request check's artifact) | **evidence** | The verdict, every answer, the candidate's hash, the baseline. **The only file deploy accepts.** |
| `predict check` | `evidence/summary.md` | the pull request check's summary page | record | The verdict and the requirement table a reviewer sees first. |
| `predict check` | `evidence/change-analysis.md` | the check's summary page and artifact | **record** | The change analysis: Predict's analysis page as a report (routing, reachability per flow, Forward's checks, other diffs, Forward AI's reading), each section saying where in Forward it came from. What a reviewer reads before approving. |
| `predict check` | prediction history | state: `predictions/` | record | Your trail of attempts. `workshop predict history`. Deploy never reads it. |
| merge on GitHub | the approval | the pull request | **evidence** | A person approved a green check. Deploy's gates check the PR is merged at the predicted commit. |
| `deploy gates` | `reports/pre-change.md` | state, and a comment on the pull request | **record** | Pre-change readiness: baseline, test, approval, every gate that held, any caveats, and the backout plan. What a change board signs off, written before anything is sent. |
| `deploy apply` | `deploy.json` | state | evidence | What was sent, the router's own diff, any caveats. `readback`, `verify` and `revert` read it. |
| `verify collect`, `upload`, `wait` | a **Forward snapshot** noted `autocon6:post …` | your Forward network | evidence | The real network after the change, collected again by the headless collector. |
| `verify compare` | `verify.json` | state | evidence | Every requirement on the real network against the prediction, and the live probes. MATCH or MISMATCH. |
| `verify compare` | `reports/post-change.md` | state, and a comment on the pull request | **record** | The post-change report: what was sent, read back, probed and re-collected, judged against the prediction. It closes the ticket. |
| `revert apply` | `revert.json` | state | evidence | The backout: the inverse lines, and proof the configs are the baseline's again. |
| `baseline collect` … `wait`, `revert compare` | a new baseline snapshot, and `reports/backout.md` | Forward, state, and a comment on the pull request | **record** | Forward's confirmation of the backout from a fresh collection: requirements, fingerprint, an empty routing diff. |
| `workshop explain` (optional) | `evidence/ai.json` | repository folder | record | Forward AI's review of a prediction. Advisory: it never decides. |

**How to read the pattern.** The loop only ever moves forward on *evidence*: a later step reads an earlier one's file or Forward record and
refuses if it does not hold (a baseline that does not pass, a prediction that is not PASS, a pull request that is not merged, a router that
drifted). The *records* are for people: they show what was proved, by what, and where the data came from. Together they are the change
ticket: proposed (change analysis), approved (the pull request), ready (pre-change), done and checked (post-change), and, if needed,
undone and checked (backout).
