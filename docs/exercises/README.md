# Know Before You Deploy: exercises

Monday, November 16, 2026 · 9:00 AM–1:00 PM · hands-on

**Baseline → Change → Predict → Approve → Deploy → Verify**

| Time | Section | Exercise | Checkpoint |
|---|---|---|---|
| 9:00–9:20 | The Missing Test Stage | [0 · Launch and check your lab](00-launch.md) | `workshop doctor` says READY; `workshop up` running |
| 9:20–10:05 | Establish the Baseline | [1 · Meet Forward, and write down what must remain true](01-baseline.md) | `workshop baseline check` all PASS |
| 10:05–10:45 | Build and Run the Change Pipeline | [2 · Your first change through the pipeline](02-pipeline.md) | a PR with a forward/predict result you can explain |
| 10:45–11:15 | *Break* | | |
| 11:15–12:00 | Break It Before You Deploy It | [3 · A change that looks safe](03-break-it.md) | a red check, and the line that caused it |
| 12:00–12:30 | Fix It and Prove It | [4 · Fix it, and let it through the gate](04-fix-it.md) | a green check, PR merged |
| 12:30–12:50 | Deploy and Verify | [5 · Deploy, collect, compare](05-deploy-verify.md) | `workshop verify compare` says MATCH |
| 12:50–1:00 | Wrap-Up and Q&A | [6 · Beyond the lab](06-beyond.md) | |

## Where this fits in the NAF Framework

The [Network Automation Forum framework](https://reference.networkautomation.forum/Framework/Framework/) names six building blocks. Each step of the loop is one or two of them:

| Step | Framework block | What does the work here |
|---|---|---|
| Baseline | Collector → Observability | Forward's headless collector, then Forward's model and the requirements |
| Change | Intent | `candidate/r4-bgp.eos` and the requirements, in Git |
| Predict | Observability | Forward Predict, judged against the requirements |
| Approve | Orchestrator (with a person) | The pull request and its forward/predict check |
| Deploy | Executor | NAPALM, sending the exact bytes Predict judged |
| Verify | Collector → Observability | The collector again, compared with the prediction |

[The full map](framework.md) says where each block lives in this repository, where the mapping is imperfect, and what to swap when you take this home.

## Why we're doing this

Pushing a change to the network is easy. Knowing what it will do is the hard part.

A syntax check tells you the config is legal. A reviewer reads a diff of one device. Neither can tell you that removing a
line on r4 takes a route away from r2, r3 and r1 (two hops away) until the change is live. That gap is what this workshop is about.

Today you put a test stage in front of the deploy. It asks what the **network** will do after the change, using a model
Forward builds from the real configs, before any router is touched. The lab is small, but the loop is the one you would run
on a real change: **Baseline → Change → Predict → Approve → Deploy → Verify**.

Each exercise starts with **why it matters** and ends with **what you just proved**. If you only have a minute, read those
two parts. The slides tell the same story: they are linked from your seat page.

**The critical path** if you fall behind: 0 → 1 → 3 → 4 → 5. Exercise 2 can be skipped. Each exercise says what
to do if you're behind.

Every exercise has a core part (everyone) and **Finished early?** extras. You never need the extras to keep up.

You never need to leave the editor: the file Explorer, **Source Control** and **GitHub Pull Requests** panels on the
left, and the terminal at the bottom. Each Git step also has the equivalent terminal commands, folded away.

**What does a `workshop` command actually do?** [Every command, in its pieces](commands.md): each one is a few calls
to netlab, NAPALM, the Forward collector, the Forward SDK and Git, and it prints each of them as it runs.

The **Your lab** tab (port 8765) always shows where you are and the next command. Stuck? Look in
[If something goes wrong](stuck.md). In the room, raise a hand. On your own, you can also ask Forward AI in that tab.

**Working on your own?** The clock times in the headings are for the live room: ignore them and take your time. Each
exercise's **Behind?** line is the shortest way through. When you reach `workshop verify compare` saying **MATCH** you have done
the whole loop. Exercise 6 is optional.
