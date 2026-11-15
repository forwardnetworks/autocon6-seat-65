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

**Why it's a closed loop.** Most change automation is open-loop: push the change, then find out from monitoring or
from users. Here each stage hands the next one a piece of evidence, and the last stage checks the first stage's
promise: Verify collects the network again and runs your tests on it. **MATCH** means your tests give the same
verdicts on the real network as they gave on the prediction. **MISMATCH** means reality disagrees: the change is still
live (nothing rolls back by itself), so you put r4 back with `workshop restore`, take a fresh baseline and work out
why. No change deploys without a prediction (`workshop restore` is the break-glass exception), and nothing is done
until reality has been checked against it.

**What that's worth.** Every change leaves evidence you can show someone: what the network would do (your tests, on
the predicted network), which merged pull request approved exactly which change, proof that what was deployed is what
was tested, and proof that your tests give the same answers on the real network afterwards. In exercise 3 you'll see the case it's built for: a change that is valid
config, passes a hygiene check, and still takes the service down two routers away. It gets caught in a pull request,
not in a change window.

Each exercise starts with **why it matters** and ends with **what you just proved**. If you only have a minute, read those
two parts. The slides tell the same story: they are linked from your seat page.

**The critical path** if you fall behind: 0 → 1 → 3 → 4 → 5. Exercise 2 can be skipped. Each exercise says what
to do if you're behind.

Every exercise has a core part (everyone) and **Finished early?** extras. You never need the extras to keep up.

You never need to leave the editor: the file Explorer, **Source Control** and **GitHub Pull Requests** panels on the
left, and the terminal at the bottom. Each Git step also has the equivalent terminal commands, folded away.

**What does each step leave behind, and what is it for?** [Artifacts](artifacts.md): every file and Forward record the loop makes, which are evidence a later step checks, and which are records for people (the change analysis, the pre- and post-change reports, the backout).

**Ahead of the room?** [Side quests](side-quests.md).

**Want to read further, or understand a tool when it breaks?** [Go deeper](go-deeper.md): Forward's docs for each
feature you use, netlab, containerlab, NAPALM, GitHub's checks and rulesets, and where to look when the platform
itself is the problem.

**What does a `workshop` command actually do?** [Every command, in its pieces](commands.md): each one is a few calls
to netlab, NAPALM, the Forward collector, the Forward SDK and Git, and it prints each of them as it runs.

**How does it actually work, and where does it stop?** [How it works](how-it-works.md): what Predict models and what it
doesn't, what each requirement kind checks exactly, the five result words, what the gates do and don't protect.

The **Your lab** tab (port 8765) always shows where you are and the next command. Stuck? Look in
[If something goes wrong](stuck.md). In the room, raise a hand.

**Curious about your network? Ask Forward AI.** Any time, in your own words: `workshop ask "which routers carry
10.20.20.0/24, and from where?"` asks about your baseline. Add `--snapshot predicted` once you've run `workshop predict
run`, or `--snapshot post` once `workshop verify compare` has run. Or use the chat under **Optional: Forward AI
explains and advises**, near the bottom of the Your lab page. Answers take a minute or
two. It's an adviser: check what it says with `workshop path` or `workshop data`, because the requirements decide,
not the AI. Nothing in the loop needs it.

**Two helpers, different jobs.** *Forward AI* (above) sees your network's model: ask it what the network does.
*`@wsa4` in Slack* knows the workshop: the exercises, the commands, what each result means and, once you link your seat
(`@wsa4 i am CODE`, code on your seat page), your last `workshop` results. Ask it "what next?" or "why did that fail?".
It suggests the command; you run it. Want another hint? Reply in the same Slack thread with `@wsa4`: it remembers what it
already told you there and goes one step further, up to the answer if you ask for it. A person comes over only when it's
something you can't fix yourself.

**Working on your own?** The clock times in the headings are for the live room: ignore them and take your time. Each
exercise's **Behind?** line is the shortest way through. When you reach `workshop verify compare` saying **MATCH** you have done
the whole loop. Exercise 6 is optional.
