# How this workshop maps to the NAF Framework

The [Network Automation Forum reference framework](https://reference.networkautomation.forum/Framework/Framework/) names six building blocks around the network: **Intent**, **Observability**, **Orchestrator**, **Executor**, **Collector** and **Presentation**. Every step of the loop you run here lands in one of them. Knowing which one tells you what to swap when you take this home.

## The map

| Framework block | What the framework says it does | In this workshop | Where you meet it |
|---|---|---|---|
| **Intent** | Holds the desired state and the expectations, and exposes them | `intent.md` (what the change is for), `requirements/*.yml` (what must stay true: service reachable, MGMT-DENY holds), `candidate/r4-bgp.eos` (the desired change), all versioned in Git | Exercises 1 and 3 |
| **Collector** | Reads the live state of the network | The Forward **headless collector** (`fwd-headless createsnapshot`) reads the routers over SSH; `workshop probe` tests the real flows from the client | `workshop baseline collect`, `workshop verify collect` |
| **Observability** | Keeps the actual state, processes it, flags differences from intent | **Forward** builds a model from the snapshot and answers questions about it (path searches, NQE). **Predict** builds the model of a network that doesn't exist yet. The judging is ours: `src/workshop/evaluate.py` compares Forward's answers with the requirements. PASS, FAIL, ERROR, INCONCLUSIVE and STALE are the flags. The same model answers questions as data: devices, routes, CVEs and what a change does to the routing tables | `workshop predict check`, the forward/predict check, `workshop data` |
| **Orchestrator** | Coordinates the other blocks in response to events, without touching devices | The **GitHub pull-request workflow** (the event is the PR) and the `workshop` command sequence. It decides the order: baseline, predict, approve, deploy, verify. It never writes to a router itself | The PR, the check, `workshop agent` |
| **Executor** | Makes the change on the network | **NAPALM** (eAPI) stages the exact candidate bytes in a configuration session, shows the router's own diff, then commits. `workshop deploy` and `workshop restore` use it. **netlab/containerlab** builds the lab and gives it its starting configuration (Netmiko), once. Guarded by a lock, a drift check, and an approval bound to the candidate hash | `workshop deploy` |
| **Presentation** | The user-facing interfaces, with authentication and authorization | The `workshop` CLI, the **Your lab** page (`workshop ui`), GitHub's PR page, the room board, Forward AI chat. Access control is the seat token and the repository invitation | The lab page, the PR |
| *Network infrastructure* | What the blocks manage | Four cEOS routers (AS 65001 to 65004), a client and a service, in containerlab | `workshop up` |

## One loop, block by block

| Step | Block doing the work | The hand-off to the next block |
|---|---|---|
| Baseline | Collector → Observability | A tagged snapshot: "what is true now" |
| Change | Intent | A candidate file in Git |
| Predict | Observability | `evidence.json`: the verdict, the candidate hash, the baseline id |
| Approve | Orchestrator (with a human) | A merged PR bound to that hash |
| Deploy | Executor | The same bytes, written to r4 |
| Verify | Collector → Observability | A new snapshot compared with the prediction |

The point of the workshop is the **Observability → Orchestrator** hand-off. Most pipelines run Executor straight after Intent, with a syntax check in between. Here Observability gets a vote first, and the Orchestrator won't continue on anything but PASS.

## Where the mapping is imperfect (and what we did about it)

- **Intent is mostly requirements, not a full intent store.** The framework's Intent block is a source of truth for the whole network. Ours covers one change on one router plus the checks that must stay true. We say "intent" for the change and "requirements" for the checks, and keep them in Git.
- **Orchestrator is split.** GitHub Actions orchestrates the approval path, and the `workshop` CLI orchestrates the local one. Both only call the other blocks, which is what the framework wants.
- **Forward is two blocks at once.** Its collector is the Collector, its model and checks are Observability. We name them separately on every page so the boundary stays visible.
- **The Executor is vendor-neutral in its API, not in its commands.** NAPALM gives every platform the same calls (`load_merge_candidate`, `compare_config`, `commit_config`; we don't use its `rollback`: restore re-applies r4's baseline, and `workshop reset` is a netlab redeploy), but the candidate is still EOS syntax and the lab is one NOS. NAPALM is imported in one module (`src/workshop/executor.py`), but swapping the Executor (Netmiko, scrapli, Nornir) is more than that file: the code around it reads EOS text. `render.py` cuts the `router bgp` section out of the config and compares diffs in EOS's format, `restore.py` and `deploy readback` (`readback_problems` in `deploy.py`) parse `network` lines from `show running-config section router bgp`, `fingerprint.py` strips `!` comments and redacts secrets the way EOS writes them, and `candidate.py`'s scope check knows EOS's BGP syntax. A different NOS touches all of those.
- **Observability and the Collector are not independent.** The headless collector exists only to feed Forward. The framework treats them as separable, and in a larger build you would feed Observability from several collectors.

## Where each block lives in the repository

| Block | Files |
|---|---|
| Intent | `intent.md`, `requirements/`, `candidate/`, `nqe/` |
| Collector | `src/workshop/collect.py`, `src/workshop/baseline.py`, `src/workshop/lab.py` (probe) |
| Observability | `src/workshop/predict.py`, `src/workshop/evaluate.py`, `src/workshop/forward.py` |
| Orchestrator | `template/.github/workflows/forward-predict.yml`, `src/workshop/cli.py`, `src/workshop/agent.py` |
| Executor | `src/workshop/executor.py`, `src/workshop/deploy.py`, `src/workshop/restore.py` |
| Presentation | `src/workshop/ui.py`, `src/workshop/ui_assets/`, `docs/exercises/` |
