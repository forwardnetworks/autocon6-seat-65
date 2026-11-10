# What each `workshop` command really does

`workshop` is a thin layer over five real tools. Nothing it does is hidden: every command prints each piece as it runs
as an indented line: `$ …` is a shell command, `→ Forward SDK …` a Forward Python SDK call, `→ NAPALM rN …` a NAPALM call
to a router, and a plain indented line says why the call above matters.
This page is the same list in one place, so you can see where each piece comes from and run it yourself.

| Tool | What it does here | Where it comes from |
|---|---|---|
| **netlab + containerlab** | builds the four routers, the client and the service as containers | `lab/topology.yml` |
| **NAPALM** (EOS driver, eAPI) | every config change: open a config session, load the lines, ask the router for its diff, commit or discard | `src/workshop/executor.py` |
| **Forward headless collector** | logs in to r1-r4 over SSH, runs show commands, writes one snapshot ZIP | downloaded from your Forward server |
| **Forward Python SDK** (`forward-sdk`) | uploads snapshots, runs Predict, path search and NQE, asks Forward AI | `pyproject.toml` pins the version |
| **evaluate.py** | compares Forward's answers with the YAML in `requirements/` and gives PASS / FAIL | `src/workshop/evaluate.py` |
| **git + GitHub** | the change, the pull request, the forward/predict check | `.github/workflows/forward-predict.yml` |

Forward **answers questions**; `evaluate.py` **judges** them against your requirements. A person approves the pull
request. The pieces are kept apart so you can see which one decided what.

Exit codes, for every command that gives a verdict: PASS 0, FAIL 1, ERROR 2, INCONCLUSIVE 3, STALE 4.

---

## The loop

### `workshop doctor`: is everything ready?
1. **SDK** `networks.list()` and the server's release: can your seat token reach Forward, and does it see exactly your own network?
2. **$** `which docker netlab containerlab`: the lab tools are installed.
3. Looks for the headless collector (in a Codespace it lives in `/workspaces/.autocon6-collector/<release>/`).
4. **$** `docker ps`: are the six lab containers running?
5. **$** `gh repo view --json viewerPermission`: can you push to your seat repository?

Changes nothing.

### `workshop up`: start the lab at its baseline
Yes, you bring up the lab yourself, with netlab:
1. **$** `netlab up --no-config -p clab` (in `lab/`). netlab turns `lab/topology.yml` into a containerlab topology and
   starts it. `--no-config` stops netlab from configuring the routers: there must be one way to change a router, and that is NAPALM.
   If the lab was stopped, it is brought back with `containerlab deploy --reconfigure` instead.
2. Waits until all four routers answer on eAPI (what NAPALM uses) and SSH (what the collector uses).
3. **napalm**, on each of r1-r4: the lines in `lab/baseline/rN.eos`, applied with
   `load_merge_candidate` → `compare_config` → `commit_config` (or `discard_config` if there is no difference).
4. **napalm** `show ip bgp summary` on each router until every BGP session is Established.

Safe to run again: on a running lab it only re-applies the baseline.
By hand: `cd lab && netlab up --no-config -p clab`, then look at `lab/baseline/r1.eos`, which `workshop up` applies.
`workshop down` is `netlab down --cleanup`.

### `workshop baseline`: give Forward a model of the network as it is
1. **SDK** `client_software.download()`, only if the collector for your server's release isn't there yet (`workshop collector` does just this step).
2. **napalm** `cli('show running-config')` on r1-r4, hashed into a **fingerprint** of the network.
3. Writes the collector's device list, `data_sources.json` (r1-r4, their management addresses, the lab login), in your
   state folder. It holds a password, so it is mode 0600 and never in Git.
4. **$** `fwd-headless createsnapshot --data-sources data_sources.json --snapshot-generated-dir … --snapshot-base-name baseline --threads 4`.
   The collector SSHes to each router, runs its show commands and writes `baseline_<time>.zip`.
5. Reads the configs back out of the ZIP and hashes them again. If the routers changed during the collection, it collects
   again (up to three times, saying why each time).
6. **SDK** `snapshots.upload(zip, note="autocon6:baseline lab=<you> fp=<fingerprint>", process=True)`. The note is how
   every later step finds your baseline and knows what it holds. If the upload fails partway, it checks whether Forward got
   it before sending it again.
7. **SDK** `snapshots.wait_until_processed(id)`: Forward builds its model (every route, ACL and path) from the ZIP.
8. **evaluate.py**: each requirement in `requirements/baseline.yml` becomes a question to Forward (**SDK**
   `path_search.get_paths` for a flow, `nqe.query` for a query), and the answer is compared with the YAML.
9. Writes `baseline.json` in your state folder.

If Forward is slow, it says so and stops waiting. Don't collect again: `workshop baseline judge` picks up the upload when it's done.

**By hand** (exercise 1): `workshop baseline prepare` does steps 1-3 and writes the commands for the rest into
`by-hand.sh`. You run the collector (step 4) and `examples/upload_by_hand.py` (steps 6-7). Then
`workshop baseline judge` checks three things before step 8: the note says baseline and this lab, Forward's copy of the
four configs hashes to the note's fingerprint, and the live routers still hash to it.

### `workshop predict`: what would this change do?
The pull request's **forward/predict** check runs exactly this command in GitHub Actions. You can also run it in your Codespace.
1. Reads `candidate/r4-bgp.eos` and checks it: only r4's BGP section, only `network` / `no network` lines. Its sha256 identifies the change from here on.
2. **SDK** `snapshots.latest_processed()`: your newest baseline. It must carry your lab's baseline note
   (otherwise STALE), Forward's copy of the configs must hash to that note (otherwise ERROR), and the baseline
   requirements must still PASS on it (otherwise STALE).
3. **SDK** `change_sets.create(name, snapshot_id=<baseline>)` → `set_commands("r4", <your lines>)` → `commit` → `predict`.
   Forward applies your lines to its model of r4 and builds the network that would result, as a new snapshot. The
   network itself is not touched.
4. **evaluate.py** asks the requirements in `requirements/candidate.yml` of the **predicted** snapshot.
5. Display only (cannot change the verdict): the route to the service on every router before and after
   (**SDK** `nqe.query`), Forward's routing diff, and the service path before and after (**SDK** `path_search.get_paths`).
6. Writes `evidence/evidence.json`: the verdict, every answer Forward gave, the candidate's hash, the baseline's id and fingerprint.
   This file is the only thing `deploy` will accept.

By hand (exercise 2): `examples/predict_by_hand.py` makes the step 3 SDK calls one at a time.

### `workshop deploy --evidence … --confirm <sha7>`: send exactly what was predicted
1. `--confirm` must be the first 7 characters of the candidate's hash: you say which change you reviewed.
2. Takes an exclusive lock on the lab, so two deploys can't run at once.
3. The gates. Any one failing stops the deploy before anything is sent:
   - The evidence is a live PASS for this lab and network.
   - `candidate/r4-bgp.eos` holds the lines that were predicted, and `requirements/candidate.yml` is unchanged.
   - **$** `gh`: the PR is merged at the commit that was predicted, and its forward/predict check passed. `--no-github` skips this for a local rehearsal.
   - **SDK**: your baseline is still the newest snapshot in Forward.
   - **napalm** `cli('show running-config')`: the four routers still hash to the baseline's fingerprint, so nobody has changed them by hand.
4. **napalm** on r4: the evidence's lines (the exact bytes Predict judged), applied with
   `load_merge_candidate` → `compare_config` → `commit_config`. It prints the diff the router computed.
5. **napalm** `show running-config section router bgp`: reads the change back and checks it is there.
6. Writes `deploy.json`.

### `workshop verify`: did the network do what Forward predicted?
1. **$** `docker exec clab-autocon6-client nc -z -w 3 10.20.20.20 <port>` for each flow requirement: real packets, from the real client.
2. Everything `workshop baseline` does (collector → upload → process), with the note `autocon6:post …`.
3. **evaluate.py** asks the requirements of that new snapshot and compares each verdict with the prediction's.
   MATCH means every one agrees and every probe agrees.
4. Display only: Forward's routing diff, baseline → the network now.
5. Writes `verify.json`.

If Forward is slow, run `workshop verify` again: it waits for the upload that is still processing and does not send a second one.

---

## Recovery

### `workshop restore`: put r4 back
1. **napalm** `show running-config section router bgp` on r4: what it advertises now.
2. **napalm**: `lab/restore/r4-bgp.eos`, plus `no network` for anything extra, applied as above.

No prediction and no gate: this is the break-glass path. That is why a new `workshop baseline` must follow.

### `workshop reset --to N [--rebuild]`: start exercise N again
1. **$** `git stash push --include-untracked` (only if you have changes), `git switch main`, `git pull --ff-only`. Your work is in `git stash list`.
2. `--rebuild` only: **$** `netlab down --cleanup`.
3. `workshop up`, then `workshop restore`.
4. Writes the candidate the exercise starts from (empty; the breaking change for 4; the fixed change for 5).
5. From exercise 2 on: `workshop baseline`.

---

## Looking inside Forward's model

### `workshop path [--port 8080] [--compare predicted]`: follow one flow, hop by hop
1. **SDK** `path_search.get_paths(src, dst, dst_port, include_network_functions=True)` on the snapshot you pick
   (baseline, predicted, post or an id): every hop, and what each device did (ACL permit or deny, route lookup).
2. **SDK** `nqe.query` (the routes query in `src/workshop/data.py`): each router's routes, so each hop shows the longest-prefix route it used.
3. `--compare`: the same for a second snapshot, and which hop changed and why.

### `workshop nqe <file or query>`: ask Forward anything
**SDK** `nqe.query(<query>, snapshot_id=…)`, and the rows as a table. Try `workshop nqe nqe/stale_bgp_advertisements.nqe`.

### `workshop data inventory|routes|vulns|diff`
- `inventory`: **SDK** `nqe.query`, a query over Forward's device inventory.
- `routes`: **SDK** `nqe.query`, the routes query.
- `vulns`: **SDK** `vulnerability_analysis`: the CVEs that match the lab's EOS version.
- `diff`: **SDK** `snapshot_diff_details`, Forward's routing diff between two snapshots.

### `workshop probe`
**$** `docker exec clab-autocon6-client nc -z -w 3 10.20.20.20 <port>` for 8080, 8443 and 2222, and the same from inside
the service container (is it listening at all?). Live packets only; no Forward.

### `workshop status` and `workshop ui`
Read the files the other commands wrote (`baseline.json`, `evidence.json`, `deploy.json`, `verify.json`) and show where you
are in the loop. `ui` also runs **$** `docker ps`, **$** `gh pr list`, and **$** `git fetch` (to tell you about workshop updates).
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
