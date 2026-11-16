# Side quests

*For when you're ahead, or just curious. Every one is optional, none blocks the core steps, and each starts from where you already are. Pick any, in any order.*

If you're **behind**, skip this page. The core steps are the only thing that matters.

| You can start this when | Side quest | About |
|---|---|---|
| your lab is running | [SQ1 · Walk the route](#sq1--walk-the-route) | 5 min |
| your baseline is PASS | [SQ2 · Interrogate the model](#sq2--interrogate-the-model) | 10 min |
| your baseline is PASS | [SQ3 · Ask Forward AI](#sq3--ask-forward-ai-then-check-it) | 5 min |
| your first check has a verdict | [SQ4 · Write your own test](#sq4--write-your-own-test) | 10 min |
| you've seen a red check | [SQ5 · Which lines are stale?](#sq5--which-lines-are-stale) | 5 min |
| you've seen a red check | [SQ6 · Every way of retiring the lines](#sq6--every-way-of-retiring-the-lines) | 10 min |
| you've seen a red check | [SQ7 · Let an adviser draft it](#sq7--let-an-adviser-draft-it) | 10 min |
| you've seen a red check | [SQ10 · Blast radius, before and after](#sq10--blast-radius-before-and-after) | 10 min |
| exercise 2, first part done (your own query) | [SQ11 · Ask Forward anything (NQE)](#sq11--ask-forward-anything-nqe) | 10 min |
| you've verified (MATCH) | [SQ8 · Cheat the gate](#sq8--cheat-the-gate) | 10 min |
| you've verified (MATCH) | [SQ9 · Catch the drift](#sq9--catch-the-drift) | 10 min |
| you've verified (MATCH) | [SQ12 · Deploy it anyway, then back it out](#sq12--deploy-it-anyway-then-back-it-out) | 15 min |
| any time after a prediction | [SQ13 · Your trail of attempts](#sq13--your-trail-of-attempts) | 2 min |
| your baseline is PASS, and you have Copilot | [SQ14 · Copilot, with Forward's tools](#sq14--copilot-with-forwards-tools) | 10 min |

Your lab page's **Go further** card tracks SQ3, SQ4, SQ6 and SQ7 for you, and its **Explore what Forward knows** box runs SQ2 and SQ10 from the page. The others are for you to notice things.

---

## SQ1 · Walk the route

**Why:** before you ask Forward anything, look at the real routers. You'll recognise this view later.

```
docker exec -it clab-autocon6-r1 Cli
show ip route 10.20.20.0/24
show ip bgp 10.20.20.0/24
```

(One line, no prompt: `docker exec clab-autocon6-r1 Cli -p15 -c "show ip bgp 10.20.20.0/24"`.)

**You should see:** the route is `B E 10.20.20.0/24 [200/0] via 10.255.0.2`, and the BGP table has **two paths**. The one through
`65002 65004` has local preference 200 and is marked best. The one through `65003 65004` has local preference 100.

**Think about it:** the diamond has two ways to the service, and local preference picks r2. If r2 failed, which way would traffic go?
How would you *prove* it with a prediction rather than guess?

## SQ2 · Interrogate the model

**Why:** Forward answers from the snapshot you collected, not from the routers. Ask it the same question as SQ1 and compare.

```
workshop data inventory
workshop data routes --prefix 10.20.20.0/24
workshop data vulns
```

**You should see:**
- **inventory:** four devices, with `no data` under END OF SUPPORT. These are lab images, so Forward has no lifecycle dates for them. On a real network that column is filled in.
- **routes:** `10.20.20.0/24` is in all four routing tables. r1 learns it by BGP via `10.255.0.2` on `et1`, the same answer SQ1 gave you from the router. On r4 it is directly connected.
- **vulns:** some entries say `none: configuration not affected` and some say `could not be checked`. Matching a version number is not the same as being exposed: Forward reads each device's configuration to decide.

**Think about it:** where did this answer come from, and how stale can it be? (Hint: `workshop baseline` made the snapshot.)

## SQ3 · Ask Forward AI, then check it

```
workshop ask --list
workshop ask --prompt baseline-stale
```

Or ask in your own words, about the baseline (the default), the prediction (`--snapshot predicted`, after `workshop
predict run`) or the network after your change (`--snapshot post`, after `workshop verify compare`):

```
workshop ask "Which BGP networks does r4 advertise that it has no route for?"
workshop ask "If r2 failed, which path would traffic from the client to 10.20.20.20 take?"
workshop ask --snapshot predicted "Why can't the client reach 10.20.20.20 on TCP 8080?"   # once you've predicted a change
```

Read the answer, then **check it yourself**: SQ1's commands, or a query from SQ2. Forward AI shows its work: when it ran NQE queries, the terminal (and the chat on the Your lab page, in **How it got that**) lists them under the answer, so you can run the same query with `workshop nqe`. AI advises, Predict decides. Does the AI agree with the router?

## SQ4 · Write your own test

r1's access list also blocks HTTPS (TCP 8443), but no requirement says it must stay blocked. Write that requirement. Your lab page's
**Go further** card has the hints, and [exercise 2](02-pipeline.md) has the full test.

## SQ5 · Which lines are stale?

*Do this after you've seen a red check, or it spoils the surprise.*

**Why:** the red check told you a line was wrong. Now find out *why* those lines are different, from the router itself.

```
docker exec -it clab-autocon6-r4 Cli
show running-config section router bgp
show ip route 10.20.20.0/24
show ip route 10.20.30.0/24
```

**You should see:** under `address-family ipv4` there are three `network` statements: `10.20.20.0/24`, `10.20.30.0/24` and `10.20.40.0/24`.
`10.20.20.0/24` is **directly connected** on `Ethernet3`. `10.20.30.0/24` has **no route at all**. Look higher in the config too:
r4 has `bgp advertise-inactive`, which tells it to announce a `network` statement even when nothing backs it.

**Think about it:** which statements carry traffic, and which are just text? Which of them could you have known were safe to remove
*before* anyone predicted it? What would a reviewer reading only the diff have missed?

## SQ6 · Every way of retiring the lines

A regression suite runs the same tests against many changes. **Guess first:** of the seven ways to retire r4's statements, which pass every test?
Then `workshop matrix` prints the grid (`--all` tries all seven; it takes a minute or two).

## SQ7 · Let an adviser draft it

Forward's assist can draft the change from `intent.md`, review a prediction, or iterate on its own. It never approves.

```
workshop propose           # then --write to keep the draft
workshop explain           # after a prediction
workshop agent             # draft, predict, advise, draft again, stop before approval
```

Does the draft pass? Would you have written the same lines? The tests are the judge, not the adviser. You are welcome to point your own coding agent at this repository: `AGENTS.md` gives it the rules.

## SQ10 · Blast radius, before and after

*After you've seen a red check. You need a baseline and a prediction of a change that failed (the one that retires all three prefixes).*

**Why:** a requirement tells you *that* a change is bad. Blast radius tells you *how much* it cuts off.

```
workshop data blast --snapshot baseline  --device r1 --prefix 10.20.20.0/24
workshop data blast --snapshot predicted --device r1 --prefix 10.20.20.0/24
```

**You should see:** on the baseline, "From r1, traffic can reach 254 of the 256 addresses in 10.20.20.0/24" (the network and broadcast addresses are the two that
are not). On the predicted snapshot of the bad change, "no packet that can reach any address in 10.20.20.0/24: the whole subnet is cut off from there."

**Think about it:** that is a before-and-after you got without touching a router. Try a different start, `--device "r1 Ethernet3"` (a device and an interface, quoted), or a
smaller destination like `10.20.20.20/32`. Which of the three network statements on r4 does the answer depend on?
Add `--json` to see Forward's own answer, including a sample packet it would send.

## SQ11 · Ask Forward anything (NQE)

**Why:** every table in `workshop data` is a query. NQE is Forward's query language, and you can write your own.

```
workshop nqe nqe/stale_bgp_advertisements.nqe
workshop nqe 'foreach d in network.devices select {name: d.name, os: d.platform.os, version: d.platform.osVersion}'
```

**You should see:** the first prints r4's two stale `network` statements: this is exactly the question the STALE-KNOWN requirement asks. The second lists your four routers.
Open `nqe/stale_bgp_advertisements.nqe` and read how it decides a statement is stale.

**Think about it:** change the second query to list something else about the devices. What would you ask Forward about your own network? Use `--snapshot predicted` to ask the
same question of the network a change *would* produce.

## SQ8 · Cheat the gate

*After MATCH. Start from a fresh baseline: `workshop reset --to 5`.*

Try to deploy a change that was judged against a *different* requirements file. The gate refuses, and the reason is the point.
The steps are in [exercise 6, "For sceptics", 1 and 2](06-beyond.md#for-sceptics).

## SQ9 · Catch the drift

*After MATCH. Start from a fresh baseline: `workshop reset --to 5`.*

Predict the fixed candidate, then change r4 **by hand** and try to deploy what Predict approved. The steps are in
[exercise 6, "For sceptics", 3](06-beyond.md#for-sceptics).

**You should see:** `NOT DEPLOYED (STALE): the routers' configs are not what was collected for the prediction`. Nothing was written. The prediction
was about a router that no longer exists, so the deploy refuses to act on it. Then `workshop restore` and the baseline steps put you back.

## SQ12 · Deploy it anyway, then back it out

*After MATCH. Start from `workshop reset --to 4`: a fresh baseline, and the breaking change (retire all three) in the candidate. This breaks your lab's service on purpose, then puts it back.*

**Why:** the gate stops a FAIL. Here you deploy one anyway, the way a change goes out "with caveats" in real life, and watch Predict be
proved right on the real network. Then you back it out the safe way.

```
workshop predict                                                  # the candidate retires all three: FAIL, and it names what breaks
workshop deploy gates --evidence evidence/evidence.json --no-github --confirm HASH
```
The gate refuses, and tells you the one way past it: `--with-caveats` with every failing requirement named. Name only one and it refuses
again: you have to accept all of them, by name.
```
workshop deploy apply --evidence evidence/evidence.json --no-github --confirm HASH --with-caveats APP-EXISTING,ROUTE-SERVICE,PATH-SERVICE
workshop deploy readback
workshop verify --evidence evidence/evidence.json
```
**You should see:** the live probe says `TCP 8080: blocked (expected blocked, as Predict said: APP-EXISTING FAILs)`, every requirement comes
out "as predicted", and verify ends with "Predict was right". The service is down, exactly as forecast.

Now back it out:
```
workshop revert plan                       # the inverse lines, and the baseline they return to
workshop revert apply --confirm HASH     # the hash plan printed: configs back, checked over NAPALM
workshop baseline collect                  # the headless collector again, then:
workshop baseline upload
workshop baseline wait
workshop revert compare                    # Forward confirms it: requirements, fingerprint, routing diff
```
**You should see:** apply says "the network is back to the baseline, byte for byte" (the configs, read over NAPALM). Then compare, after a
fresh collection, says Forward agrees: the baseline requirements PASS, the fingerprint is the original baseline's, and "no route differs
anywhere". That is the backout verified the same way a deploy is: by collecting the real network again, not by trusting the write.

**Think about it:** what did the gate cost you here, and what did it save? In production, who should be allowed to type `--with-caveats`?
`workshop restore` would also have put r4 back. What does `revert` check that `restore` doesn't?

## SQ13 · Your trail of attempts

```
workshop predict history
```
Every prediction you finished, with what moved between attempts. The Predict step on your lab page shows the same list.

## SQ14 · Copilot, with Forward's tools

*Any time after your baseline is PASS. Needs GitHub Copilot on your own GitHub account.*

**Why:** an agent you already use, with read-only access to Forward's model of your network. It can trace paths and write
and run NQE queries, the same tools Forward AI uses. It still only advises: the requirements decide.

**Set up for you:** when your Codespace starts, `workshop mcp` writes `.vscode/mcp.json` (Forward's MCP server, with your
seat's key) and `.github/copilot-instructions.md` (your lab's network ID, which every tool call needs, and the rules).
Both stay out of Git. Open **Copilot Chat**, choose **Agent** mode, and in the tools list turn on **forward** (start it if
VS Code asks).

Try:

- *"Trace a path from the client 10.10.10.10 to the service 10.20.20.20 on TCP 8080."* Then `workshop path --port 8080`
  (add `--snapshot post` once you've verified, so both read the same snapshot). Same hops?
- *"Which BGP network statements on r4 have no route behind them?"* It writes an NQE query and runs it. Compare its
  answer with `STALE-KNOWN` in `workshop baseline check`, and run its query yourself with `workshop nqe`.
- *"If r4 stopped advertising 10.20.20.0/24, what would break?"* It answers from a snapshot of the network as it is.
  How would you **prove** the answer instead of trusting it?

Without a snapshot id it reads your network's latest processed snapshot. Give it one to be sure, including your
prediction's: after a prediction, `evidence/evidence.json` has `baseline_snapshot_id` and `predicted_snapshot_id`. Not working? Run `workshop mcp` again, then **Developer: Reload
Window**. On a Copilot Business or Enterprise plan, your organization's "MCP servers in Copilot" policy has to be on.

