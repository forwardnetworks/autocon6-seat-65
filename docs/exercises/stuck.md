# If something goes wrong

**Start an exercise again, whatever state you're in:** `workshop reset --to N` (N = 1 to 5). It keeps your uncommitted work in
`git stash` (`git stash pop` brings it back), switches you to `main` with the latest from GitHub, merges each router's baseline config
back (a line you *added* by hand stays: use `--rebuild` for that), and sets up the start of exercise N (a fresh baseline from exercise 2 on; exercise 4 starts with the
breaking change, exercise 5 with the fixed one, ready for `workshop predict` and a local deploy). Add `--rebuild` if a router itself is broken: the lab is rebuilt from
scratch (3 to 10 minutes). Your pull requests on GitHub are left alone.

Find the line that looks like yours. Most fixes are one command. Run `workshop status` any time to see where you are in the loop and the next command.

| You see | What it means | What to do |
|---|---|---|
| GitHub shows **"Oh no, something unexpected went wrong"** for your Codespace | GitHub's side; its status page failed while the Codespace was starting | Click **Restart and reconnect to codespace** on that page. It takes a minute or two. Your files are kept; run `workshop up` again if your lab was running |
| The **Your lab** pane is blank, or there is no Your lab page | The page is running, but VS Code's built-in browser pane often can't show it | Open the **Ports** tab at the bottom, click the globe icon beside port **8765**, and keep that browser tab open. If there is no port 8765, run `bash .devcontainer/start-ui.sh` |
| After you merge, VS Code asks what to delete | A clean-up prompt | Choose **Delete Local Branch**. Don't choose *Suspend Codespace* (it stops your lab) |
| VS Code asks "Do you trust the authors of the files in this folder?" | The first open of a Codespace | **Trust Folder & Continue** |
| `workshop doctor` has a FAIL line | It says which part | Do what that line says. If it is "you can push to this seat repository", accept your repository invitation, then reopen the Codespace from your seat page |
| **Your lab** page says 404, or is blank, right after your Codespace was stopped and resumed | The page itself is fine: the port forward is stale | Wait a minute or two. If it persists: **Ports** tab, right-click port **8765**, **Stop Forwarding Port**, then **Add Port** and type `8765`, wait a minute and reload. Everything on the page is also a terminal command (shown under each button), so you can keep going meanwhile |
| The editor freezes, or shows **"Disconnected. Attempting to reconnect"**, while `workshop up` runs | Starting four routers uses all of the machine's CPU for about two minutes | Give it a few minutes. If it doesn't come back, reload the browser tab. If the lab isn't up afterwards, run `workshop up` again (it is safe to re-run) |
| `workshop up` or `baseline` says the lab is not running, or the lab is gone after a break | The Codespace stopped (idle) and the lab went with it | `workshop up` (about four minutes), then `workshop baseline` |
| `workshop baseline` fails on `APP-EXISTING` | The routers were still booting | `workshop up` again, then `workshop baseline` |
| `workshop baseline` fails on `STALE-KNOWN` with three rows, or says the lab is not at its starting state | Something changed r4 by hand, or a deploy is still on it | `workshop restore`, then `workshop baseline` |
| The **forward/predict** check is slow | Many people predicting at once | Wait a few minutes. It reruns by itself when you push again |
| The check says **STALE** | A newer snapshot than your baseline exists in Forward (a verify, or another baseline), or the baseline was taken for a different lab. The check never reads your routers | `workshop baseline`, then push your change again |
| The check says **ERROR: changes files outside candidate/** | Your pull request touched other files | Revert them on the branch and push. Only `candidate/r4-bgp.eos` may change |
| You can't merge | The check isn't green | By design. Fix the candidate until it is |
| `workshop deploy` refuses | Not merged yet, a red check, a wrong `--confirm`, a newer baseline, or the routers drifted | Read its message. It prints the exact `--confirm` value it expects |
| `workshop verify` says **MISMATCH** or **INCOMPLETE** | Something changed after you deployed, or the collection failed | INCOMPLETE: run it again. MISMATCH: `workshop restore`, `workshop baseline`, and redo from predict |
| Forward AI says "I wasn't able to produce a complete answer" | The model gave up on that turn | In the browser chat start a **New conversation** and ask again, or ask a narrower question. The `workshop` commands retry once by themselves |
| Forward AI is "still answering" | One question at a time per person | Wait a minute, or skip it. Nothing in the core loop needs AI |
| Nothing here matches | | Send Craig your seat number, what you ran, and what it printed. Everything you ran is already logged, so a short description is enough |

Want to know exactly what a command did before you trust it? [Every `workshop` command, in its pieces](commands.md).
