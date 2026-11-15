# Go deeper

Links out, for when you want more than the workshop shows. Each one says what you'll find and where it connects to
what you did today. You never need any of them to finish the loop: they're jump-off points for afterwards, for a side
quest, or for when something breaks and you want to understand why rather than just fix it.

**Pick by what you want:**

| You want to… | Start here |
|---|---|
| Try this on your own network | [Monday morning](06-beyond.md#monday-morning), then [Before you try this at work](06-beyond.md#before-you-try-this-at-work), then Forward's [Predict overview](https://docs.fwd.app/latest/application/predict/overview/) |
| Understand why the gate said what it said | [How it works, and where it stops](how-it-works.md) |
| Write better tests (requirements) | [Path analysis](#forward-the-parts-you-used) and [NQE](#forward-the-parts-you-used) below, then [SQ4](side-quests.md#sq4--write-your-own-test) and [SQ11](side-quests.md#sq11--ask-forward-anything-nqe) |
| Put the gate in your own CI | [The pipeline](#the-pipeline-github), and [`examples/gitlab-ci.yml`](../../examples/gitlab-ci.yml) in this repository |
| Know what each command really called | [Every command, in its pieces](commands.md), and the **HTTP** fold under each call on the Your lab page |
| Rebuild the lab on your laptop | [The tools in the loop](#the-tools-in-the-loop): netlab, containerlab, NAPALM |

## The idea

- **[NAF Framework](https://reference.networkautomation.forum/Framework/Framework/)**: the six building blocks of
  network automation (Intent, Collector, Observability, Orchestrator, Executor, Presentation). Our
  [framework map](framework.md) shows where each one sits in this repository. *Connects to: the whole loop.*
- **[Forward Predict: overview](https://docs.fwd.app/latest/application/predict/overview/)** and
  **[reviewing Predict results](https://docs.fwd.app/latest/application/predict/reviewing-results/)**: Predict as the
  product presents it. Today you drove it only through the SDK, from a pull request. *Connects to: exercises 2–4.*

## Forward, the parts you used

| Topic | What you'll find | You used it in |
|---|---|---|
| [Path analysis](https://docs.fwd.app/latest/application/search/path-analysis/overview/) ([reading results](https://docs.fwd.app/latest/application/search/path-analysis/understanding-results/), [troubleshooting](https://docs.fwd.app/latest/application/search/path-analysis/troubleshooting/)) | How Forward traces a packet hop by hop through the model, and what each outcome means (delivered, dropped, blocked by an ACL) | `workshop path` (exercise 1); the `flow` and `path` requirements (`APP-EXISTING`, `MGMT-DENY`, `PATH-SERVICE`) |
| [NQE](https://docs.fwd.app/latest/application/nqe/) ([tutorial](https://docs.fwd.app/latest/application/nqe/nqe-tutorial/), [language](https://docs.fwd.app/latest/application/nqe/language/), [library](https://docs.fwd.app/latest/application/nqe/nqe-library/)) | Forward's query language over the model: every device, interface, route and config line as data | `nqe/stale_bgp_advertisements.nqe` behind `STALE-KNOWN` and `STALE-CLEARED` (exercise 1), and the `route` requirements; [SQ11](side-quests.md#sq11--ask-forward-anything-nqe) |
| [Snapshot diffs](https://docs.fwd.app/latest/application/diffs/) | What changed between two snapshots: routes, configs, devices | "3 routes removed on r1, r2, r3" after the bad prediction (exercise 3); `workshop data diff` |
| [Verify checks](https://docs.fwd.app/latest/application/verify/) ([path checks](https://docs.fwd.app/latest/application/verify/checks-path/), [NQE checks](https://docs.fwd.app/latest/application/verify/checks-nqe/)) | Checks you save inside Forward and run on every snapshot | Your requirements become exactly these: `workshop baseline check` registers the critical flows as path checks and the stale-advertisement query as an NQE check, and Forward judges every snapshot against them |
| [Forward AI](https://docs.fwd.app/latest/application/forward-ai/using-forward-ai/) ([limits and scope](https://docs.fwd.app/latest/application/forward-ai/limits-and-scope/), [MCP server](https://docs.fwd.app/latest/application/forward-ai/mcp-server/)) | Asking the model questions in plain language, what it can and can't answer, and connecting your own agent to it | `workshop ask`, `workshop explain`, the Your lab page's chat; [SQ3](side-quests.md#sq3--ask-forward-ai-then-check-it); Copilot with the MCP server: [SQ14](side-quests.md#sq14--copilot-with-forwards-tools) |
| [Network collection](https://docs.fwd.app/latest/getting-started/network-collections/) | How Forward collects from real devices | The headless collector in `workshop baseline collect` |
| [API reference](https://docs.fwd.app/latest/api/) ([NQE API](https://docs.fwd.app/latest/api/nqe/)) | Every endpoint the SDK wraps | The **HTTP** fold under each Forward SDK call on the Your lab page shows which ones you hit |
| Python SDK: [forward-sdk on PyPI](https://pypi.org/project/forward-sdk/), [source](https://github.com/forwardnetworks/forward-python-sdk) | The library every `workshop` call into Forward goes through | `src/workshop/forward.py`, and every **→ Forward SDK** line in your terminal |
| Go SDK: [source](https://github.com/forwardnetworks/forward-go-sdk) | The same API from Go | The seat and claim service (`seatd/`, not in your seat repo) |

## The tools in the loop

| Tool | What it does here | Where |
|---|---|---|
| [netlab](https://netlab.tools/) | Turns `lab/topology.yml` into four configured routers. from `lab/`, `netlab status`, `netlab inspect --node r4` and `netlab connect r1` work in your Codespace | `workshop up`, `workshop reset` (exercise 0) |
| [containerlab](https://containerlab.dev/) | Runs the routers and hosts as containers; netlab drives it | Under `workshop up`; the Containerlab panel in the editor |
| [NAPALM](https://napalm.readthedocs.io/en/latest/) ([what each driver supports](https://napalm.readthedocs.io/en/latest/support/)) | Pushes the change: loads the candidate, shows the router's own diff, commits | `workshop deploy render`, `apply` (exercise 5), `workshop revert`, `workshop restore` |
| [EOS configuration sessions](https://www.arista.com/en/um-eos/eos-configure-session) | The router feature NAPALM uses on EOS: stage a change, diff it, commit it in one step or throw it away | Why `deploy render` can show r4's diff before anything is written |
| [GitPython](https://gitpython.readthedocs.io/), [PyGithub](https://pygithub.readthedocs.io/), [Docker SDK](https://docker-py.readthedocs.io/) | The libraries behind every **→ Git**, **→ GitHub API** and **→ Docker SDK** line | [Every command, in its pieces](commands.md) |
| [uv](https://docs.astral.sh/uv/) | Installs the pinned Python packages | `uv sync --frozen` |

## The pipeline (GitHub)

- **[Status checks](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/about-status-checks)**:
  how a check like `forward/predict` reports on a pull request. *Exercise 2.*
- **[Rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)**:
  how `main` refuses a direct push and a merge without a green check. *Exercises 2 and 4.*
- **[GitHub Actions](https://docs.github.com/en/actions)**: the runner behind the check. Ours is
  `.github/workflows/forward-predict.yml`: the three predict steps as three job steps.
- **Taking it to another CI:** the gate is a command with exit codes, so any CI can run it.
  [`examples/gitlab-ci.yml`](../../examples/gitlab-ci.yml) is a sketch.

## When it's the tool, not you

Most problems have a one-line fix in [If something goes wrong](stuck.md). When the problem is the platform itself:

- **[GitHub status](https://www.githubstatus.com/)**: if Codespaces, Actions or pull requests are slow for everyone,
  look here first.
- **[Codespaces troubleshooting](https://docs.github.com/en/codespaces/troubleshooting)** and
  **[port forwarding](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace)**:
  when the Codespace won't start, or the Your lab page (port 8765) won't open.
- **[netlab](https://netlab.tools/)** and **[containerlab](https://containerlab.dev/)** docs: when `workshop up`
  fails inside netlab or containerlab rather than in our code (the error names the tool).
- **[Path analysis troubleshooting](https://docs.fwd.app/latest/application/search/path-analysis/troubleshooting/)**:
  when a path answer surprises you.

## Next to the tools you already use

We didn't use or test these here, so there's no comparison. They're what people often ask about, and
[how it works, section k](how-it-works.md#k-next-to-the-tools-you-already-use) says where each would sit.

- [Batfish](https://batfish.org/): offline analysis of configurations
- [SuzieQ](https://suzieq.readthedocs.io/): collecting and querying operational state
- [pyATS](https://pubhub.devnetcloud.com/media/pyats/docs/): testing against live devices
- [Nornir](https://nornir.readthedocs.io/): a Python automation framework, often with NAPALM underneath

## People

- [Network Automation Forum](https://networkautomation.forum/): the community behind AutoCon.
- [ipSpace.net blog](https://blog.ipspace.net/): Ivan Pepelnjak, netlab's author, on network automation and design.
- During the event: `#ac6-ws-a4` in Slack, or `@wsa4` there for a quick answer.
