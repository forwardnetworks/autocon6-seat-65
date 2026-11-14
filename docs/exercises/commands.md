# What each `workshop` command really does

`workshop` is a thin layer over real tools, and you run it one step at a time. Each step does one thing, prints every
call it makes as an indented line, and ends with its evidence (a file, a snapshot id, a verdict) and the next step:

    → netlab       netsim.cli.up.run(['-p', 'clab'])   netlab's own Python, the code behind `netlab up -p clab`
    → Forward SDK  snapshots.upload(…)                 a call through the Forward Python SDK
    → NAPALM r4    load_merge_candidate · compare_config · commit_config
    → Docker SDK   clab-autocon6-client: exec_run(['nc', …])
    → git          switch main   (GitPython)
    $ fwd-headless createsnapshot …                    the one program it runs as a process: the collector has no API

The workshop calls libraries, not command lines. You can always use the command lines yourself: `netlab status`,
`netlab connect r1`, `docker exec -it clab-autocon6-r4 Cli`, `git`, `gh`.

| Tool | What it does here | Where it comes from |
|---|---|---|
| **netlab** (+ containerlab underneath) | builds the four routers, the client and the service as containers, and gives the routers their starting configuration, pushed with Netmiko. Once. | `lab/topology.yml`, `lab/*.j2`; netlab 26.10 pinned in `pyproject.toml` |
| **NAPALM** (EOS driver, eAPI) | every config change after that: open a config session, load the lines, ask the router for its diff, commit or discard | `src/workshop/executor.py` |
| **Forward headless collector** | logs in to r1-r4 over SSH, runs show commands, writes one snapshot ZIP | downloaded from your Forward server |
| **Forward Python SDK** (`forward-sdk`) | uploads snapshots, runs Predict, path search and NQE, asks Forward AI | `src/workshop/forward.py` |
| **evaluate.py** | compares Forward's answers with the YAML in `requirements/` and gives PASS / FAIL | `src/workshop/evaluate.py` |
| **Git and GitHub** | the change, the pull request, the forward/predict check (GitPython and GitHub's REST API when the workshop needs them) | `.github/workflows/forward-predict.yml` |

Forward **answers questions**; `evaluate.py` **judges** them against your requirements. A person approves the pull
request. The pieces are kept apart so you can see which one decided what.

Exit codes, for every step that gives a verdict:

| Status | Exit | Means |
|---|---|---|
| PASS | 0 | every requirement had positive evidence; the only verdict that can deploy |
| FAIL | 1 | a requirement's evidence contradicts it |
| ERROR | 2 | no answer we trust (a Forward error, an out-of-scope candidate, a refused gate) |
| INCONCLUSIVE | 3 | no evidence either way (no path found, a timeout) |
| STALE | 4 | true once, but about a baseline, file or router state that has changed since |

Who raises each, and exactly when: [how it works, section d](how-it-works.md#d-the-five-status-words). Each step keeps its
result in `$WORKSHOP_STATE_DIR/$WORKSHOP_LAB_ID/steps/`; a step whose input is missing tells you which step to run first.

How Predict models a change, what each requirement kind really checks, and where the gates stop:
[how it works, and where it stops](how-it-works.md).

---

## The lab

### `workshop doctor`: is everything ready?
**SDK** `networks.list()`: does your seat token reach Forward, and see exactly your own network? Then: docker, containerlab
and sshpass installed, Netmiko importable (netlab configures the routers with it), the collector downloaded, the six
containers (**Docker SDK** `containers.list`), and whether you can push to your seat repository (**GitHub API**). Changes nothing.

### `workshop up`: build the lab and its starting configuration
1. **netlab** `up -p clab` (in `lab/`): netlab turns `lab/topology.yml` into a containerlab topology and starts the six
   containers. Then it renders each router's configuration (its modules: interfaces, routing, BGP and BGP policy, plus
   the workshop's templates `lab/no-mgmt-default.j2`, `lab/client-in.j2`, `lab/stale-adverts.j2`) into
   `lab/node_files/`, and sends it to each router over SSH with **Netmiko**.
2. Waits until all four routers answer on eAPI (NAPALM) and SSH (the collector), then until every BGP session is up.

A stopped lab (a Codespace restart) is rebuilt: **netlab** `down --cleanup`, then `up`. A lab whose bring-up stopped
before configuring is finished with **netlab** `initial`. A running, configured lab is left as it is. netlab configures
a router only when it builds it; every change after that goes through NAPALM.

Look around with netlab itself (from `lab/`): `netlab status` (the lab and its nodes), `netlab inspect --node r1`
(everything netlab knows about r1), `netlab connect r1` (a CLI session on r1), `ls node_files/r4` (the configuration
pieces netlab sent). `workshop down` is **netlab** `down --cleanup`.

---

## Baseline: give Forward a model of the network as it is

### `workshop baseline collect`
1. **SDK** `client_software.download()`, only if the collector for your server's release isn't there yet.
2. Writes the collector's device list, `data_sources.json` (r1-r4, their management addresses, the lab login). It holds
   a password, so it is mode 0600 and never in Git.
3. **NAPALM** `cli('show running-config')` on r1-r4, hashed into a **fingerprint** of the network.
4. **$** `fwd-headless createsnapshot --data-sources data_sources.json … --snapshot-base-name baseline --threads 4`: the
   collector SSHes to each router, runs its show commands and writes `baseline_<time>.zip`.
5. Reads the configs back out of the ZIP and hashes them again. If the routers changed during the collection, it collects
   again (up to three times, saying why).

Evidence: the ZIP, its size and file count, the fingerprint.

### `workshop baseline upload`
**SDK** `snapshots.upload(zip, note="autocon6:baseline lab=<you> fp=<fingerprint>", process=True)`. The note is how every
later step finds your baseline and knows what it holds. If Forward is still processing an earlier upload of exactly this
network, it waits for that one instead of queueing a second. If an upload fails partway, it checks whether Forward got it
before sending it again. Evidence: the snapshot id.

### `workshop baseline wait`
**SDK** `snapshots.wait_until_processed(id)`, showing Forward's queue position and processing stage as it goes. Forward
builds its model: the modelled forwarding behaviour of the collected devices. Evidence: the devices Forward modelled. If it runs out of time, run it again:
it waits for the same upload.

### `workshop baseline check`
Three integrity checks, each printed: the snapshot's note says *baseline* and *your lab*; Forward's copy of the four
configs (**SDK** `devices.file`) hashes to the note's fingerprint; the live routers (**NAPALM**) still hash to it. Then
**evaluate.py**: each requirement in `requirements/baseline.yml` becomes a question to Forward (**SDK**
`path_search.get_paths` for a flow, `nqe.query` for the rest), compared with the YAML. Writes `baseline.json`.

**Run the collector and the upload yourself:** `workshop baseline prepare` writes the device list, fingerprints the routers
and prints the commands (the collector, then `python examples/upload_by_hand.py`); `workshop baseline judge` is `check` for
the newest baseline you uploaded.

---

## Predict: what would this change do?

The pull request's **forward/predict** check runs these three, one job step each.

### `workshop predict changeset`
1. Reads `candidate/r4-bgp.eos`: only r4's BGP section, only `network` / `no network` lines. Its sha256 identifies the
   change from here on.
2. **SDK** `snapshots.latest_processed()`: your newest baseline. It must carry your lab's baseline note (else STALE),
   Forward's copy of the configs must hash to it (else ERROR), and the baseline requirements must still PASS (else STALE).
3. **SDK** `change_sets.create(name, snapshot_id=<baseline>)` → `set_commands("r4", <your lines>)` → `commit`.

Evidence: the change set's id. Nothing is predicted yet.

### `workshop predict run`
**SDK** `predict(…)`, then `wait_until_processed`: Forward applies the change set to its model of r4 and builds the
network that would result, as a new snapshot. The network itself is not touched. Evidence: the predicted snapshot's id,
and Forward's routing diff (what the change adds and removes).

### `workshop predict check`
**evaluate.py** asks the requirements in `requirements/candidate.yml` of the **predicted** snapshot. Writes
`evidence/evidence.json`: the verdict, every answer Forward gave, the candidate's hash, the baseline's id and fingerprint.
This file is the only thing `deploy` will accept. Also shows (display only) the route to the service on every router and
the service path, before and after.

The raw SDK calls are in `examples/predict_by_hand.py`.

---

## Deploy: send exactly what was predicted

`--pr N` gets the evidence of a merged pull request from GitHub (**GitHub API**: the PR, its check runs, the check's
artifact). `--evidence evidence/evidence.json --no-github` deploys a local prediction instead: a rehearsal.

### `workshop deploy gates --pr N --confirm <sha7>`
Every gate, read-only; any one failing stops here:
- `--confirm` is the first 7 characters of the candidate's hash: you say which change you reviewed;
- the evidence is a live PASS for this lab and network;
- `candidate/r4-bgp.eos` holds the lines that were predicted, and `requirements/candidate.yml` is unchanged;
- **GitHub API**: the PR is merged at the commit that was predicted, and its forward/predict check passed;
- **SDK**: your baseline is still the newest snapshot in Forward;
- **NAPALM** `cli('show running-config')`: the four routers still hash to the baseline's fingerprint.

### `workshop deploy render --pr N`: one change, two applications
The candidate is one model of the change, applied twice. This shows both, side by side:
- **inside Forward**: **SDK** `change_sets.handle(id).head()` (the commands the change set holds for r4), and r4's
  configuration in Forward's model before (`devices.file` on the baseline) and after (`committed_device_config`), as a diff;
- **on the router**: **NAPALM** `load_merge_candidate` → `compare_config` (r4's own diff) → `discard_config`. Nothing on
  r4 changes.

Then it checks the two diffs change the same lines. If they don't, stop: what Predict judged is not what the router would do.

### `workshop deploy apply --pr N --confirm <sha7>`
An exclusive lock on the lab, the gates once more, then **NAPALM** on r4: the evidence's lines (the exact bytes Predict
judged), `load_merge_candidate` → `compare_config` → `commit_config`. Prints the diff the router computed. Writes `deploy.json`.

### `workshop deploy readback`
**NAPALM** `show running-config section router bgp` on r4, checked against every line that was sent.

---

## Verify: did the network do what Forward predicted?

### `workshop verify probe`
**Docker SDK** `exec_run(['nc', '-z', '-w', '3', '10.20.20.20', <port>])` in the client container, for each flow
requirement: real packets, from the real client. The same in the service container tells a blocked port from a service
that isn't listening.

### `workshop verify collect`, `upload`, `wait`
The baseline's three steps again, on the network as it is now. The note says `autocon6:post …`.

### `workshop verify compare`
**evaluate.py** asks the candidate requirements of that new snapshot and compares each verdict with the prediction's.
MATCH means every one agrees and every probe agrees. Then Forward's routing diff, baseline → the network now. Writes
`verify.json`.

---

## Recovery

### `workshop restore`: put r4 back
1. **napalm** `show running-config section router bgp` on r4: what it advertises now.
2. **napalm**: `lab/restore/r4-bgp.eos`, plus `no network` for anything extra, applied as above.

No prediction and no gate: this is the break-glass path. That is why the baseline steps must follow (`workshop baseline collect` first).

### `workshop reset --to N`: start exercise N again
The one command that does several things, because it's for getting unstuck:
1. **git** (GitPython): `stash push --include-untracked` (only if you have changes), `switch main`, `pull --ff-only`. Your
   work is in `git stash list`. If the update changed `uv.lock`, it tells you to run `uv sync --frozen`.
2. A netlab redeploy: **netlab** `down --cleanup`, then `workshop up` (netlab builds and configures the lab again). Every
   router is exactly as netlab first configured it, whatever was changed since.
3. Writes the candidate the exercise starts from (empty; the breaking change for 4; the fixed change for 5).
4. From exercise 2 on: a fresh baseline (the four baseline steps, in a row).

---

## Looking inside Forward's model

### `workshop path [--port 8080] [--compare predicted]`: follow one flow, hop by hop
1. **SDK** `path_search.get_paths(src, dst, dst_port, include_network_functions=True)` on the snapshot you pick
   (baseline, predicted, post or an id): every hop, and what each device did (ACL permit or deny, route lookup).
2. **SDK** `nqe.query` (the routes query in `src/workshop/data.py`): each router's routes, so each hop shows the longest-prefix route it used.
3. `--compare`: the same for a second snapshot, and which hop changed and why.

### `workshop nqe <file or query>`: ask Forward anything
**SDK** `nqe.query(<query>, snapshot_id=…)`, and the rows as a table. Try `workshop nqe nqe/stale_bgp_advertisements.nqe`.

### `workshop data inventory|topology|interfaces|routes|vulns|diff|blast|checks`
- `inventory`: **SDK** `nqe.query`, a query over Forward's device inventory.
- `topology`: **SDK** `network_topology.get_topology`: the links Forward inferred between the routers, each once.
- `interfaces`: **SDK** `device_interfaces.list_device_interfaces` per router (`--device r1` for one): address, description, ACLs, state.
- `checks`: **SDK** `checks.get_checks`: Forward's own checks on a snapshot; with `--against predicted`, which ones a change moves.

Every table ends with the exact NQE query or SDK call behind it; `--json` adds Forward's raw answer. The lab page shows the same tables
from the same code.
- `routes`: **SDK** `nqe.query`, the routes query.
- `vulns`: **SDK** `vulnerability_analysis`: the CVEs that match the lab's EOS version.
- `diff`: **SDK** `snapshot_diff_details`, Forward's routing diff between two snapshots.
- `blast`: **SDK** `blast_radius.get_blast_radius`: from a device (`--device r1`) or an interface (`--device "r1 Ethernet3"`), how many addresses in a prefix (`--prefix 10.20.20.0/24`) traffic can reach. Run it on the baseline and on the predicted snapshot to see how much a change cuts off. Needs no internet node; the answer for a cut-off subnet is empty.

### `workshop probe`
**Docker SDK** `exec_run(['nc', '-z', '-w', '3', '10.20.20.20', <port>])` in the client container for 8080, 8443 and 2222,
and the same inside the service container (is it listening at all?). Live packets only; no Forward.

### `workshop status` and `workshop ui`
Read the files the other commands wrote (`baseline.json`, `evidence.json`, `deploy.json`, `verify.json`) and show where you
are in the loop. `ui` also asks the **Docker SDK** for the containers, the **GitHub API** for your pull requests and
their checks, and **git** (GitPython) `fetch` to tell you about workshop updates.
It serves the **Your lab** tab on port 8765. Its buttons run the same `workshop` commands, and you see their output.

---

## Forward AI (advice only: it never decides a gate)

- `workshop ask`: **SDK** `ai.start` / `ai.get`, a chat with Forward AI about one snapshot.
- `workshop explain`: Predict Assist's summaries of the change and its impact
  (**SDK** `predict_assist.assist_config_diff_summary`, `assist_impact_diff_summary`, `assist_change_set_overview`). If the prediction failed, it
  also asks Forward AI why. The **forward/advice** check runs this.
- `workshop propose`: **SDK** `predict_assist.assist_change_set_commands`: Forward drafts r4's lines from `intent.md`.
  Nothing is judged until Predict evaluates it.
- `workshop agent`: propose → predict → explain, in a loop until the gate passes, stopping before approval.
- `workshop matrix`: `workshop predict` on every way of retiring r4's advertisements, side by side.


---

## Iterating, deploying with caveats, and backing out

### `workshop predict history`
Read-only. Every prediction you finished on this seat, oldest first: its hash, the lines it retires, the verdict, the requirements that
did not pass, and what moved since the attempt before ("APP-EXISTING FAIL -> PASS"). Kept in the seat's state folder, outside the
repository. Deploy never reads it: only `evidence/evidence.json`, the newest, can deploy.

### `workshop deploy gates|apply --evidence evidence/evidence.json --no-github --confirm <sha7> --with-caveats ID,ID,...`
A deploy **with caveats**: the one way past a FAIL, for showing that Predict was right. Only a live FAIL (never ERROR, INCONCLUSIVE or
STALE: nobody knows what those would do), only a local prediction (a red pull request cannot merge), and only when you name every
failing requirement, exactly; a wrong or partial list is refused and the message names the right one. The gates print the caveats
loudly, and the deploy record keeps them. `workshop verify` then judges the real network against the *prediction*, probes included:
if the requirements Predict said would fail did fail, that is a MATCH, and it says "Predict was right".

### `workshop revert plan` / `workshop revert apply --confirm <sha7>` / `workshop revert compare`
Back out exactly what was deployed. `plan` (read-only) shows the inverse lines and the state they return to: the baseline the change was
predicted against, which `baseline check` already judged PASS. `apply` needs the revert's hash confirmed, refuses if r4 no longer holds
what was deployed (then it is a job for `restore`), sends the inverse over NAPALM, and proves the whole network is back to the
baseline's fingerprint, byte for byte (the configs, read over NAPALM). Then Forward confirms it, as `verify` does for a deploy: the
headless collector takes a fresh snapshot (`workshop baseline collect`, `upload`, `wait`; it becomes your baseline) and `compare` judges it:
the baseline requirements PASS (**SDK** path searches and NQE, as in `baseline check`), the fingerprint is the original baseline's, and
Forward's routing diff (**SDK** `snapshot_diff_details`) from the original baseline to the new snapshot is empty. MATCH, or it says what differs.
`workshop restore` stays the break-glass path: no record, no checks.
