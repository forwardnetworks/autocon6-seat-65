# Know Before You Deploy: exercises

Monday, November 16, 2026 · 9:00 AM–1:00 PM · hands-on

**Baseline → Change → Predict → Approve → Deploy → Verify**

| Time | Section | Exercise | Checkpoint |
|---|---|---|---|
| 9:00–9:20 | The Missing Test Stage | [0 · Launch and check your lab](00-launch.md) | `workshop doctor` says READY; `workshop up` running |
| 9:20–9:50 | Establish the Baseline | [1 · What must remain true](01-baseline.md) | `workshop baseline` all PASS |
| 9:50–10:45 | Build and Run the Change Pipeline | [2 · Your first change through the pipeline](02-pipeline.md) | a PR with a forward/predict result you can explain |
| 10:45–11:15 | *Break* | | |
| 11:15–12:00 | Break It Before You Deploy It | [3 · A change that looks safe](03-break-it.md) | a red check, and the line that caused it |
| 12:00–12:30 | Fix It and Prove It | [4 · Fix it, and let it through the gate](04-fix-it.md) | a green check, PR merged |
| 12:30–12:50 | Deploy and Verify | [5 · Deploy, collect, compare](05-deploy-verify.md) | `workshop verify` says MATCH |
| 12:50–1:00 | Wrap-Up and Q&A | [6 · Beyond the lab](06-beyond.md) | |

**The critical path** if you fall behind: 0 → 1 → 3 → 4 → 5. Exercise 2 can be skipped. Each exercise says what
to do if you're behind.

Every exercise has a core part (everyone) and **Finished early?** extras. You never need the extras to keep up.

You never need to leave the editor: the file Explorer, **Source Control** and **GitHub Pull Requests** panels on the
left, and the terminal at the bottom. Each Git step also has the equivalent terminal commands, folded away.

The **Your lab** tab (port 8765) always shows where you are and the next command. Stuck? Raise a hand, or ask Forward
AI in that tab.
