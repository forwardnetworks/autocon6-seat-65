# Working in this repository (for coding agents and the people using them)

This is an AutoCon6 workshop seat: a four-router lab in this Codespace, a Forward network that models it, and a
change loop that proves a change is safe **before** it reaches the routers. Bring any coding agent you like. These
are the rules it has to work within, and the tools it can use.

## The loop

Baseline → Change → Predict → Approve → Deploy → Verify. The intent is in `intent.md`. The machine-checked version
of it is `requirements/candidate.yml`.

## What an agent may change

- **Only `candidate/r4-bgp.eos`.** The pull request check refuses a PR that changes anything else, and the candidate
  itself may only contain `router bgp 65004` / `address-family ipv4` followed by `[no] network <prefix>` lines.
  `workshop predict` checks that before calling Forward.
- Do not edit `requirements/`, `src/`, `lab/`, `.github/` or `.devcontainer/`. The check runs the code and
  requirements from `main`, not from the pull request, so edits there don't change the verdict. They just fail the PR.

## What an agent must not do

- **Deploy.** `workshop deploy` needs a merged pull request whose `forward/predict` check passed, and a person typing
  the first 7 characters of the candidate hash (`--confirm`). Leave that step to the human.
- **Change the routers any other way.** No SSH edits, no `netlab`/`containerlab` config pushes. A router that drifts
  from the baseline makes every later step return STALE.

## Useful commands

| Command | What it tells you |
|---|---|
| `workshop status` | where this lab is in the loop |
| `workshop predict` | predicts `candidate/r4-bgp.eos` against the current baseline and judges it (the same thing the PR check does) |
| `workshop explain` | Forward AI's review of the last prediction: what it changes, what it affects, and why it failed if it did |
| `workshop ask "<question>"` | a Forward AI chat grounded in a snapshot (`--snapshot baseline|predicted|post`); one question at a time, 1–2 minutes |
| `workshop propose` | Predict's config assist drafts the candidate from `intent.md` (`--write` to save it) |
| `workshop agent` | draft → predict → take advice → draft again, stopping at a passing prediction |

Exit codes are the same for every command: **0 PASS, 1 FAIL, 2 ERROR, 3 INCONCLUSIVE, 4 STALE.** Only 0 is a
pass. Treat anything else as "not safe yet", including an empty or missing answer.

## How to judge your own work

The requirements, evaluated by Forward on the **predicted** network, are the judge. They are not your reasoning, and
not an AI chat answer. Forward AI can be confidently wrong, so check its advice with a prediction.
`evidence/evidence.json` records exactly what was predicted and against which baseline.
