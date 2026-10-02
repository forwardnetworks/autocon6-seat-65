# 6 · Beyond the lab (12:50–1:00)

## The same pattern, at scale

- **More tests, same gate.** Each requirement is a behavioral test: a flow, a route, a path, an NQE query. A
  regression suite is more of them, run on every change.
- **Any pipeline.** The gate is a CLI with exit codes (0 PASS; FAIL, ERROR, INCONCLUSIVE and STALE all stop). Drop it
  into the CI you already run.
- **Increasingly autonomous.** Let agents draft the change and iterate against Predict, and keep approval with a
  person.

## Take it further (stretch, or after the workshop)

Reset first: `workshop restore && workshop baseline`.

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
