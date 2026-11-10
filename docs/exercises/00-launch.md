# 0 · Launch and check your lab (9:00–9:20)

*About 10 minutes of introduction, then 10 minutes hands-on. Start `workshop up` as early as you can: it takes a few
minutes.*

Configuration validation asks "is this config well formed?" **Behavioral validation** asks "what will the network do
with it?" Today that second question gets its own stage in the pipeline, and it runs before deploy.

**Framework map.** You are starting the **network** and writing its baseline configuration with the **Executor** (NAPALM). netlab only builds the topology. See [the framework map](framework.md).

**Why this matters.** Every later step depends on starting from a state you can name and rebuild. A lab of your own, with its own Forward network, means you can break things on purpose today. On a network that carries traffic you can't, which is part of why changes there feel risky.

## Core

1. Your Codespace is open (from your seat page). Wait for setup to finish, which takes a few minutes: the terminal shows a prompt starting with `(workshop)` when it is done. If VS Code asks whether you trust the folder, choose **Trust Folder & Continue**.
   The **Your lab** page (port 8765) shows where you are and the next step. If the pane beside your editor is blank, open the **Ports** tab at the bottom, find port **8765**, and click the globe icon to open it in a browser tab. Keep that tab open.
2. In the terminal:
   ```
   workshop doctor
   ```
   Every line should say **OK**, ending with **READY**. A FAIL line tells you what to do. If "you can push to this
   seat repository" fails, accept your repository invitation, then reopen the Codespace from the seat page.
3. Start the lab:
   ```
   workshop up
   ```
   Nothing starts until you run this. Two tools split the work, and you can watch both:
   - **netlab** reads `lab/topology.yml` (four routers, a client and a service, and the links between them) and asks
     **containerlab** to start them. You will see `netlab up --no-config` in the output. `--no-config` matters: netlab
     builds the network but does not configure it.
   - Then `workshop` writes each router's baseline configuration over eAPI with **NAPALM**, so there is one way a change
     reaches a router, and Deploy can check it.

   It takes 3 to 10 minutes on a new Codespace (about 4 after a restart) and the machine will be busy. Leave it running and read on.
4. **Look at what netlab built.** In the terminal, `containerlab inspect --all` lists your lab, `autocon6`, with six nodes
   (and the **Containerlab** icon in the left activity bar shows the same tree: if it stays blank, the terminal is the reliable way):
   - **r1, r2, r3, r4**: Arista cEOS routers in the diamond (AS 65001 to 65004)
   - **client** and **service**: small Linux containers. The client sits behind r1 and the service behind r4, and the
     requirements test whether the client can still reach the service

   To get a prompt on a router: `docker exec -it clab-autocon6-r1 Cli` (type `enable` first), and `docker exec -it clab-autocon6-client sh`
   for the client. In the Containerlab panel, right-click a node for **Connect to SSH** or **Attach shell**, and the lab for **Graph**.
   Try this on r1:
   ```
   show ip bgp summary
   ```
   Two neighbors should be **Established**. `netlab status` (from the `lab/` folder) lists the lab too.

### Open the box: what `workshop up` actually did (optional, while it runs)

`workshop up` is a wrapper. Everything it does leaves files you can read, in a second terminal (the **+** in the terminal panel):

```
ls lab/
sed -n 1,40p lab/clab.yml            # what netlab generated for containerlab: nodes, the cEOS image, the links
grep -B3 ansible_host lab/hosts.yml  # the management addresses: NAPALM and the collector both find the routers here
```

`lab/topology.yml` is about 40 lines you could write by hand. netlab turned it into `clab.yml` (what containerlab runs) and
`hosts.yml` (an inventory). With `--no-config`, netlab stopped there: no router configuration came from netlab.
[Every `workshop` command, in its pieces](commands.md) lists what each of the others does in the same way.

Once `workshop up` finishes, compare what is **on** r4 with what is **in Git**:
```
docker exec clab-autocon6-r4 Cli -p 15 -c "show running-config section router bgp"
sed -n '/router bgp/,$p' lab/baseline/r4.eos
```
They match because NAPALM pushed that file. That is the point of one change path: the file in Git is what is on the router.

## What you have

- **This Codespace:** the lab (netlab defines it, containerlab runs it, NAPALM configures it) and the `workshop` command.
- **A Forward network on fwd.app:** Forward's mathematical model of your lab, built from what the headless collector
  reads off the routers.
- **This GitHub repository:** where changes are proposed, predicted and approved.

### What you just proved

You have an isolated lab and a Forward network that only you can touch. Nothing you do today affects anyone else, and you can rebuild it in minutes.

**Behind?** The one must-do is `workshop up` running. Everything else can wait.

## Finished early?

- `workshop --help`: one short command per step of the loop.
- Open `intent.md`: the change you've been asked to make today.
