# 0 · Launch and check your lab (9:00–9:20)

*About 10 minutes of introduction, then 10 minutes hands-on. Start `workshop up` as early as you can: it takes a few
minutes.*

Configuration validation asks "is this config well formed?" **Behavioral validation** asks "what will the network do
with it?" Today that second question gets its own stage in the pipeline, and it runs before deploy.

## Core

1. Your Codespace is open (from your seat page). Wait for setup to finish; the **Your lab** tab opens by itself.
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
   Four Arista cEOS routers, a client and a service start, and their baseline configuration is applied over SSH with
   Netmiko. Leave it running and read on.

## What you have

- **This Codespace:** the lab (containerlab + netlab) and the `workshop` command.
- **A Forward network on fwd.app:** Forward's mathematical model of your lab, built from what the headless collector
  reads off the routers.
- **This GitHub repository:** where changes are proposed, predicted and approved.

**Behind?** The one must-do is `workshop up` running. Everything else can wait.

## Finished early?

- `workshop --help`: one short command per step of the loop.
- Open `intent.md`: the change you've been asked to make today.
