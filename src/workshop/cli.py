"""The ``workshop`` command: one step at a time through the change loop. Each step does one thing, prints the real call it
makes, and ends with its evidence and the next step.

    workshop doctor                                      is everything ready?
    workshop up                                          netlab builds and configures the lab
    workshop baseline collect | upload | wait | check    Forward gets a model of the network, then the requirements are checked
    workshop predict changeset | run | check             (the PR check runs these three) predict a change and judge it
    workshop predict history                             every prediction you finished, and what each attempt moved
    workshop deploy gates | render | apply | readback    send exactly what was predicted, with NAPALM
                    --with-caveats ID,ID,...             deploy a FAIL on purpose (local evidence only), naming every failing requirement
    workshop revert plan | apply | compare               back out exactly what was deployed, checked; Forward confirms it after a collection
    workshop verify probe | collect | upload | wait | compare    did the network do what Forward predicted?

Looking inside Forward's model:

    workshop data inventory | topology | interfaces | routes | vulns | diff | blast | checks   what Forward knows
    workshop path [--port N] [--compare predicted]        one flow, hop by hop
    workshop nqe <file or query>                         ask Forward anything
    workshop probe                                       real packets from the client, right now

Recovery: workshop revert (checked, the inverse of the deploy) · workshop restore (break-glass: r4 back to its baseline, over NAPALM) · workshop reset --to N (a netlab redeploy) · workshop status

Forward AI advisers (optional; they advise, Predict and the requirements decide):

    workshop ask         ask Forward AI about a snapshot (--list shows the sample prompts)
    workshop propose     let Predict's config assist draft the candidate from intent.md
    workshop explain     review a prediction and, if it failed, ask why
    workshop agent       draft, predict and take advice in a loop until the gate passes
    workshop ui          your loop, the network and the advisers in a browser tab
    workshop matrix      predict every way of retiring r4's advertisements and compare them
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from workshop import config, trace
from workshop import report as report_
from workshop.evidence import EXIT


def log(message: str) -> None:
    print(message, flush=True)


trace.set_sink(log)  # every Forward, router, Docker and GitHub call prints one line (workshop.trace)


STEPS = {
    "baseline": ["collect", "upload", "wait", "check"],
    "predict": ["changeset", "run", "check"],
    "deploy": ["gates", "render", "apply", "readback"],
    "verify": ["probe", "collect", "upload", "wait", "compare"],
}


def step_line(stage: str, step: str) -> str:
    """Where this step sits in its stage: 'Baseline · step 2 of 4:  collect ✓ → upload ◀ → wait → check'."""
    order = STEPS[stage]
    i = order.index(step)
    chain = " → ".join(f"{s} ✓" if n < i else f"{s} ◀" if n == i else s for n, s in enumerate(order))
    return f"{stage.capitalize()} · step {i + 1} of {len(order)}:  {chain}"


def banner(settings: config.Settings, action: str) -> None:
    log(f"== workshop {action} -- lab {settings.lab_id}, Forward network {settings.network_id}")
    stage, _, step = action.partition(" ")
    if step in STEPS.get(stage, []):
        log(step_line(stage, step))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workshop", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    sub.add_parser("doctor", help="check tools, Forward access and the lab")
    sub.add_parser("up", help="build the lab: netlab creates and configures it")
    sub.add_parser("down", help="stop and remove the lab")
    bl = sub.add_parser("baseline", help="one step at a time: collect, upload, wait, check (or prepare/judge to run the collector yourself)")
    bl.add_argument("step", nargs="?", choices=["collect", "upload", "wait", "check", "prepare", "judge"],
                    help="collect: run the collector · upload: send the ZIP to Forward · wait: Forward builds its model · check: integrity "
                         "checks and the baseline requirements. prepare/judge: you run the collector and upload yourself. No step: all of them")
    p = sub.add_parser("predict", help="one step at a time: changeset, run, check")
    p.add_argument("step", nargs="?", choices=["changeset", "run", "check", "history"],
                   help="changeset: check the baseline, create the change set in Forward · run: Forward builds the predicted network · "
                        "check: the requirements on it, and the evidence. No step: all three (the PR check runs that)")
    p.add_argument("--candidate", default="candidate/r4-bgp.eos")
    p.add_argument("--requirements", default="requirements/candidate.yml")
    p.add_argument("--baseline-requirements", default="requirements/baseline.yml")
    p.add_argument("--pr", type=int)
    p.add_argument("--head-sha")
    p.add_argument("--base-sha")
    p.add_argument("--out", default="evidence", help="directory for evidence.json and summary.md")
    d = sub.add_parser("deploy", help="one step at a time: gates, render, apply, readback")
    d.add_argument("step", nargs="?", choices=["gates", "render", "apply", "readback"],
                   help="gates: every check, nothing sent · render: the change in Forward and on r4, side by side (a dry run) · "
                        "apply: send it with NAPALM · readback: read it back from r4. No step: apply and readback")
    target = d.add_mutually_exclusive_group()
    target.add_argument("--pr", type=int, help="the merged pull request whose forward/predict evidence to deploy")
    target.add_argument("--evidence", help="an evidence.json from a local predict (rehearsal)")
    d.add_argument("--confirm", help="the first 7 characters of the candidate sha256 you reviewed (gates, apply)")
    d.add_argument("--no-github", action="store_true", help="skip the merged-PR check (local rehearsal only)")
    d.add_argument("--with-caveats", default="", metavar="IDS",
                   help="deploy a FAIL on purpose (local evidence only): name every failing requirement you accept, comma-separated. "
                        "For the exercise that shows Predict was right; back it out with `workshop revert`")
    v = sub.add_parser("verify", help="one step at a time: probe, collect, upload, wait, compare")
    v.add_argument("step", nargs="?", choices=["probe", "collect", "upload", "wait", "compare"],
                   help="probe: real packets from the client · collect/upload/wait: the network after the change, into Forward · "
                        "compare: each requirement against the prediction. No step: all of them")
    v.add_argument("--evidence", help="default: the evidence the last deploy used")
    sub.add_parser("restore", help="restore r4's baseline advertisements")
    rv = sub.add_parser("revert", help="back out exactly what was deployed, checked, to the baseline (plan, then apply)")
    rv.add_argument("step", choices=["plan", "apply", "compare"],
                    help="plan: what would be sent and where it returns to (read-only) · apply: send it · "
                         "compare: after the baseline steps, Forward confirms the network is the original baseline")
    rv.add_argument("--confirm", help="apply: the first 7 characters of the revert's hash (plan prints it)")
    a = sub.add_parser("ask", help="ask Forward AI about a snapshot")
    a.add_argument("question", nargs="?", help="your question (or use --prompt)")
    a.add_argument("--prompt", help="a sample prompt id from prompts/")
    a.add_argument("--snapshot", help="baseline | predicted | post | <snapshot id> (default: the prompt's, else baseline)")
    a.add_argument("--evidence", default="evidence/evidence.json", help="where 'predicted' comes from")
    a.add_argument("--list", action="store_true", help="list the sample prompts")
    pr = sub.add_parser("propose", help="let Predict's config assist draft the candidate from intent.md")
    pr.add_argument("--candidate", default="candidate/r4-bgp.eos")
    pr.add_argument("--write", action="store_true", help="write the draft to the candidate file")
    e = sub.add_parser("explain", help="review a prediction and, if it failed, ask Forward AI why")
    e.add_argument("--evidence", default="evidence/evidence.json")
    e.add_argument("--no-chat", action="store_true", help="only the fast summaries, no AI chat")
    e.add_argument("--chat-timeout", type=float, default=300)
    ag = sub.add_parser("agent", help="draft, predict and take advice in a loop, stopping before approval")
    ag.add_argument("--max-iterations", type=int, default=3)
    ag.add_argument("--candidate", default="candidate/r4-bgp.eos")
    ag.add_argument("--requirements", default="requirements/candidate.yml")
    ag.add_argument("--baseline-requirements", default="requirements/baseline.yml")
    ag.add_argument("--out", default="evidence/agent")
    ag.add_argument("--fresh", action="store_true", help="ignore the current candidate and start from a new draft")
    mx = sub.add_parser("matrix", help="predict every way of retiring r4's advertisements and compare them")
    mx.add_argument("--all", action="store_true", help="every non-empty combination (7) instead of the four worth comparing")
    u = sub.add_parser("ui", help="your loop, the network and the advisers in a browser tab")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--evidence", default="evidence/evidence.json")
    dt = sub.add_parser("data", help="what Forward knows: devices, routes, CVEs, and what a change did to the routing tables")
    dt.add_argument("topic", choices=["inventory", "topology", "interfaces", "routes", "vulns", "diff", "blast", "checks"])
    dt.add_argument("--snapshot", default="baseline", help="baseline | predicted | post | <snapshot id> (diff: the 'before' side)")
    dt.add_argument("--against", help="diff and checks: the 'after' side (diff defaults to predicted if there is one, else post; checks only compares if you give one)")
    dt.add_argument("--device", default="", help="only this device (routes, diff); for blast, where traffic starts: r1, or r1 Ethernet3 (default r1)")
    dt.add_argument("--prefix", default="", help="only this prefix, e.g. 10.20.20.0/24 (routes, diff); for blast, where traffic is going (default 10.20.20.0/24)")
    dt.add_argument("--evidence", default="evidence/evidence.json", help="where 'predicted' comes from")
    dt.add_argument("--json", action="store_true", help="the structured answer as JSON, including Forward's raw response")
    rs = sub.add_parser("reset", help="start an exercise again from a known point (keeps your work in git stash)")
    rs.add_argument("--to", type=int, required=True, choices=[1, 2, 3, 4, 5], help="the exercise to start again")
    rs.add_argument("--rebuild", action="store_true", help=argparse.SUPPRESS)  # every reset rebuilds now; kept so old notes still work
    rs.add_argument("--yes", action="store_true", help="don't ask first")
    pa = sub.add_parser("path", help="trace one flow through Forward's model, hop by hop, with the ACL and route behind each hop")
    pa.add_argument("--port", type=int, default=8080, help="destination TCP port (8080 the app, 2222 and 8443 blocked)")
    pa.add_argument("--to", default="10.20.20.20", help="destination address (default: the service)")
    pa.add_argument("--from", dest="src", default="10.10.10.10", help="source address (default: the client)")
    pa.add_argument("--snapshot", default="baseline", help="baseline | predicted | post | a snapshot id")
    pa.add_argument("--compare", default="", help="a second snapshot (e.g. predicted): trace both and say what changed")
    pa.add_argument("--evidence", default="evidence/evidence.json", help="where 'predicted' comes from")
    nq = sub.add_parser("nqe", help="run an NQE query (a file such as nqe/stale_bgp_advertisements.nqe, or the query text) and show its rows")
    nq.add_argument("query", help="a .nqe file, or the query itself in quotes")
    nq.add_argument("--snapshot", default="baseline", help="baseline | predicted | post | a snapshot id")
    nq.add_argument("--evidence", default="evidence/evidence.json", help="where 'predicted' comes from")
    sub.add_parser("collector", help="download the headless collector for the server's release")
    sub.add_parser("status", help="where this lab is in the loop")
    sub.add_parser("probe", help="test the real flows from the client (8080, 8443, 2222) right now")
    sub.add_parser("version", help="print versions")
    sub.add_parser("yolo")  # no help text, so it is not listed: a joke that teaches the gate, and it always refuses
    args = parser.parse_args(argv)

    if args.command == "version":
        return _version()
    if args.command == "yolo":
        for line in report_.yolo_lines():
            log(line)
        return EXIT["ERROR"]
    try:
        settings = config.load()
    except config.SettingsError as exc:
        log(f"workshop: {exc}")
        return EXIT["ERROR"]
    from workshop import telemetry
    from workshop.executor import DeployError
    from workshop.lab import LabError

    handler = globals()[f"_{args.command}"]
    started = time.time()
    code = EXIT["ERROR"]
    try:
        code = handler(settings, args)
        return code
    except KeyboardInterrupt:
        code = 130
        return code
    except (LabError, DeployError) as exc:  # a missing lab, or a router that refused or can't be reached: a message, exit 2 (ERROR)
        log(f"workshop {args.command}: {exc}")
        code = EXIT["ERROR"]
        return code
    finally:
        telemetry.report_workshop(args.command, code, started, EXIT)  # beta activity log; silent when not configured


def _version() -> int:
    for name in ("workshop", "forward-sdk", "napalm"):
        try:
            log(f"{name} {importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            log(f"{name} (not installed)")
    return 0


def _doctor(settings: config.Settings, args) -> int:
    from workshop import lab
    from workshop.forward import Forward

    banner(settings, "doctor")
    ok = True

    def row(good: bool, what: str, detail: str = "") -> None:
        nonlocal ok
        ok &= good
        log(f"  {'OK  ' if good else 'FAIL'} {what}{(' -- ' + detail) if detail else ''}")

    try:
        with Forward(settings) as fwd:
            release = fwd.release()
            networks = fwd.network_ids()
        row(True, "Forward reachable", f"release {release}")
        row(networks == [settings.network_id], "seat token sees exactly its own network", f"{networks}")
    except Exception as exc:  # noqa: BLE001 - doctor reports, it does not crash
        row(False, "Forward reachable", f"{type(exc).__name__}: {str(exc)[:160]}")
    for tool, why in (("docker", ""), ("containerlab", ""), ("sshpass", "netlab checks the routers' SSH with it before configuring them")):
        row(shutil.which(tool) is not None, f"{tool} installed", "" if shutil.which(tool) else why)
    row(lab.netlab_has_netmiko(), "netlab can configure the routers (Netmiko installed beside it)",
        "" if lab.netlab_has_netmiko() else "run: uv sync --frozen")
    collector = sorted(settings.collector_home.glob("*/fwd/bin/fwd-headless")) if settings.collector_home.exists() else []
    row(True, "headless collector", str(collector[-1].parent.parent.parent.name) if collector else "not downloaded yet (workshop collector)")
    try:  # the Docker SDK reports a missing or stopped docker daemon itself
        states = lab.containers()
        running = sum(1 for s in states.values() if s == "running")
        row(running == 6 or not states, "lab containers", f"{running}/6 running" if states else "lab not started (workshop up)")
    except Exception as exc:  # noqa: BLE001
        row(False, "docker", str(exc)[:160])
    if os.environ.get("CODESPACES"):
        from workshop import github

        try:
            permission = github.can_push(settings.repo_root)
        except github.GitHubError as exc:
            permission = f"NONE ({exc})"
        row(permission in ("WRITE", "MAINTAIN", "ADMIN"), "you can push to this seat repository",
            permission if permission in ("WRITE", "MAINTAIN", "ADMIN") else f"{permission} -- accept your seat invitation, then reopen this Codespace from the seat repository")
    log("READY" if ok else "NOT READY -- fix the FAIL lines above")
    return 0 if ok else EXIT["ERROR"]


def _up(settings: config.Settings, args) -> int:
    from workshop import lab

    banner(settings, "up")
    try:
        lab.up(settings, log)
    except lab.LabError as exc:
        log(f"workshop up: {exc}")
        return EXIT["ERROR"]
    log("lab is up at baseline")
    for line in report_.up_close():
        log(line)
    return 0


def _down(settings: config.Settings, args) -> int:
    from workshop import lab

    banner(settings, "down")
    try:
        lab.netlab(settings, log, "down", "--cleanup")
    except lab.LabError as exc:
        log(f"workshop down: {exc}")
        return EXIT["ERROR"]
    log("lab removed: the six containers and their links are gone. `workshop up` builds it again (your Forward snapshots stay).")
    return 0


def _baseline(settings: config.Settings, args) -> int:
    import contextlib

    from workshop import baseline
    from workshop.forward import Forward

    banner(settings, "baseline" + (f" {args.step}" if getattr(args, "step", None) else ""))
    if getattr(args, "step", None) == "prepare":
        try:
            env = baseline.prepare(settings, log)
        except Exception as exc:  # noqa: BLE001
            log(f"workshop baseline prepare: {exc}")
            return EXIT["ERROR"]
        for line in report_.by_hand_lines(env):
            log(line)
        return 0
    from workshop import steps

    step = getattr(args, "step", None)
    try:
        if step == "collect":
            steps.collect_step(settings, "baseline", log)
            return 0
        if step == "upload":
            steps.upload_step(settings, "baseline", log)
            return 0
        if step == "wait":
            steps.wait_step(settings, "baseline", log)
            return 0
        if step == "check":
            snapshot_id, fp, report = baseline.judge(settings, log, steps.processed_snapshot(settings, "baseline")["snapshot"])
        elif step == "judge":
            snapshot_id, fp, report = baseline.judge(settings, log)
        else:
            snapshot_id, fp, report = baseline.run(settings, log)
    except Exception as exc:  # noqa: BLE001
        log(f"workshop baseline{' ' + step if step else ''}: {exc}")
        return EXIT["ERROR"]
    log(f"baseline snapshot {snapshot_id} (fingerprint {fp[:12]}): Forward's model of your network as it is now")
    with contextlib.suppress(Exception), Forward(settings, quick=True) as fwd:  # display only
        log(report_.inventory_line(fwd.c, snapshot_id))
        log("")
        log("The route to the service on every router, in Forward's model:")
        for line in report_.service_routes(fwd.c, snapshot_id):
            log(line)
    rows = [r.to_json() for r in report.results]
    with _display_only():
        log("")
        log("Requirements checked on that model:")
        for line in report_.requirement_table(rows, on=f"your baseline (snapshot {snapshot_id})"):
            log(line)
        log("")
        log("Each requirement in detail:")
        for line in report_.requirement_lines(rows):
            log(line)
    log("")
    log(f"baseline: {report.status.value}")
    with _display_only():
        for line in report_.baseline_close(report.status.value) + report_.tagline("baseline", report.status.value):
            log(line)
    return EXIT[report.status.value]


@contextmanager
def _display_only():
    """Tables and explanations: a bug drawing them is reported in one line and never changes the verdict or exit code."""
    try:
        yield
    except Exception as exc:  # noqa: BLE001
        log(f"  (could not draw this part: {type(exc).__name__}: {str(exc)[:120]}; the verdict below is unaffected)")


def _predict(settings: config.Settings, args) -> int:
    import contextlib

    from workshop import evidence, predict
    from workshop.forward import Forward

    if getattr(args, "step", None) == "history":  # read-only: the trail of every prediction finished on this seat
        from workshop import history

        for line in history.render(history.entries(settings)):
            log(line)
        return 0
    banner(settings, "predict" + (f" {args.step}" if getattr(args, "step", None) else ""))
    root = settings.repo_root
    git = {k: v for k, v in {"pr": args.pr, "head_sha": args.head_sha, "base_sha": args.base_sha,
                             "repo": os.environ.get("GITHUB_REPOSITORY"), "run_id": os.environ.get("GITHUB_RUN_ID"),
                             "actor": os.environ.get("GITHUB_ACTOR")}.items() if v}
    inputs = predict.Inputs(root / args.candidate, root / args.requirements, root / args.baseline_requirements, git)
    step = getattr(args, "step", None)
    state = settings.state("steps/predict.json")
    if step == "changeset":
        settings.state("steps").mkdir(exist_ok=True)
        m = predict.prepare(settings, inputs, log=log)
        m.write(state)
    if step == "changeset" and m.status == predict.PENDING:
        log("")
        log(f"Evidence: change set {m.change_set_id} in your Forward network, on baseline {m.baseline_snapshot_id}, holding r4's {len(m.candidate_text.splitlines())} lines")
        log(f"  (sha256 {m.candidate_sha256[:12]}). Nothing is predicted yet. Next: workshop predict run")
        return 0
    if step in ("run", "check"):
        if not state.is_file():
            log("nothing to work on yet: run `workshop predict changeset` first")
            return EXIT["ERROR"]
        m = evidence.Manifest.read(state)
        if step == "run":
            m = predict.predict(settings, m, log=log)
            m.write(state)
        if step == "run" and m.status == predict.PENDING:
            log("")
            log(f"Evidence: predicted snapshot {m.predicted_snapshot_id}, the network as it would be with your change (baseline {m.baseline_snapshot_id} untouched)")
            with _display_only(), Forward(settings, quick=True) as fwd:
                log("What the change does to the routing tables, in Forward's model:")
                for line in report_.routing_lines(report_.routing_table(fwd.c, str(m.baseline_snapshot_id), str(m.predicted_snapshot_id))):
                    log(line)
            log("Nothing is judged yet. Next: workshop predict check")
            return 0
        if step == "check":
            if not m.predicted_snapshot_id:
                log("not predicted yet: run `workshop predict run` first")
                return EXIT["ERROR"]
            m = predict.judge(settings, m, inputs.requirements, log=log)
        state.unlink(missing_ok=True)  # finished (judged, or stopped): the evidence below is the result
    elif step != "changeset":
        m = predict.run(settings, inputs, log=log)
    else:
        state.unlink(missing_ok=True)  # the change set step stopped: write that as the evidence, so the PR says why
    out = Path(args.out)
    m.write(out / "evidence.json")
    with contextlib.suppress(Exception):  # the trail is display only: keeping it never changes the verdict or the exit code
        from workshop import history

        history.record(settings, m, out=str(args.out))
    routing = None
    if m.predicted_snapshot_id:
        with contextlib.suppress(Exception), Forward(settings, quick=True) as fwd:  # display only: short timeout, no retries
            routing = report_.routing_table(fwd.c, str(m.baseline_snapshot_id), str(m.predicted_snapshot_id))
    for snap in {str(m.baseline_snapshot_id), str(m.predicted_snapshot_id)} - {"", "None"}:  # Forward's raw answers travel with the evidence
        raw = settings.state_dir / f"forward-{snap}.json"
        if raw.is_file():
            shutil.copy(raw, out / raw.name)
    summary = evidence.markdown(m, routing)
    (out / "summary.md").write_text(summary)
    _append_summary(summary)
    log(f"{m.status}: {m.reason}")
    with contextlib.suppress(Exception):  # display only: whatever happens below, the verdict above and the exit code stand
        if m.predicted_snapshot_id and m.results:
            log("")
            log("Requirements, judged on the network that would result:")
            for line in report_.requirement_table(m.results, on=f"the predicted network (snapshot {m.predicted_snapshot_id})"):
                log(line)
            log("")
            log("Each requirement in detail:")
            for line in report_.requirement_lines(m.results):
                log(line)
            log("")
            log(f"What the change does to the routing tables (baseline {m.baseline_snapshot_id} -> predicted {m.predicted_snapshot_id}):")
            for line in report_.routing_lines(routing):
                log(line)
            from workshop import paths
            with contextlib.suppress(Exception), Forward(settings, quick=True) as fwd:  # display only, and on its own: a failure here must not hide the verdict lines below
                before = paths.trace(fwd.c, str(m.baseline_snapshot_id), src="10.10.10.10", dst="10.20.20.20", port=8080, label="baseline")
                after = paths.trace(fwd.c, str(m.predicted_snapshot_id), src="10.10.10.10", dst="10.20.20.20", port=8080, label="predicted")
                for line in paths.compare(before, after):
                    log(line)
            log("")
            log("Drill down: workshop path --compare predicted (hop by hop) · workshop path --port 2222 --snapshot predicted ·")
            log("            workshop nqe nqe/stale_bgp_advertisements.nqe --snapshot predicted · workshop data diff --device r1")
        else:
            log("")
            log("No predicted network was built, so there are no requirements or routing changes to show for this change.")
        failing = [r["id"] for r in m.results if r["status"] == "FAIL"]
        for line in report_.predict_close(m.status, failing) + report_.tagline("predict", m.status, len(failing)):
            log(line)
    if m.deployable:
        target = f"--pr {args.pr}" if args.pr else f"--evidence {out / 'evidence.json'}"
        log(f"to deploy after merging, one step at a time: workshop deploy gates {target} --confirm {m.candidate_sha256[:7]}, then render, apply, readback")
    return EXIT.get(m.status, EXIT["ERROR"])


def _deploy(settings: config.Settings, args) -> int:
    from workshop import deploy, github, render

    step = getattr(args, "step", None)
    banner(settings, "deploy" + (f" {step}" if step else ""))
    if step == "readback":
        try:
            deploy.readback(settings, log)
        except deploy.Refused as exc:
            log(f"READBACK FAILED ({exc.status}): {exc}")
            return EXIT[exc.status]
        log("Next: workshop verify probe")
        return 0
    if not (args.pr or args.evidence):
        log("say which change: --pr N (a merged pull request) or --evidence evidence/evidence.json (a local prediction)")
        return EXIT["ERROR"]
    if step != "render" and not args.confirm:
        log("say which candidate you reviewed: --confirm <the first 7 characters of its sha256> (predict printed it)")
        return EXIT["ERROR"]
    try:
        if args.pr:
            log(f"fetching the forward/predict evidence for PR #{args.pr} from GitHub ...")
            evidence = github.download_evidence(github.merged_pr(args.pr), settings.state(f"evidence-pr{args.pr}"))
            log(f"  {evidence}")
        else:
            evidence = Path(args.evidence)
        caveats = [c.strip() for c in args.with_caveats.split(",") if c.strip()]
        if caveats and args.pr:
            log("NOT DEPLOYED (ERROR): caveats are for a local prediction (--evidence): a red pull request cannot merge")
            return EXIT["ERROR"]
        if step == "gates":
            deploy.check(settings, evidence, args.confirm, log, check_github=not args.no_github, caveats=caveats)
            src = f"--pr {args.pr}" if args.pr else f"--evidence {args.evidence}"
            log(f"Every gate holds; nothing was sent. Next: workshop deploy render {src} (Forward's change next to r4's own dry run), then apply")
            return 0
        if step == "render":
            from workshop.evidence import Manifest

            with deploy.lab_lock(settings):  # its dry run opens a config session on r4: never alongside an apply
                rendered = render.build(settings, Manifest.read(evidence), log)
            for line in render.lines_for(rendered):
                log(line)
            return 0
        if step == "apply":
            deploy.apply(settings, evidence, args.confirm, log, check_github=not args.no_github, caveats=caveats)
            log("Sent and committed on r4" + (" WITH CAVEATS (Predict said FAIL)" if caveats else "") + ". Next: workshop deploy readback")
            return 0
        deploy.run(settings, evidence, args.confirm, log, check_github=not args.no_github, caveats=caveats)
    except github.GitHubError as exc:
        log(f"NOT DEPLOYED (ERROR): {exc}")
        return EXIT["ERROR"]
    except deploy.Refused as exc:
        log(f"NOT DEPLOYED ({exc.status}): {exc}")
        return EXIT[exc.status]
    except Exception as exc:  # noqa: BLE001 - render's dry run or Forward: a sentence, not a traceback
        log(f"workshop deploy {step or ''}: {type(exc).__name__}: {exc}")
        return EXIT["ERROR"]
    log("deployed. Next: workshop verify probe")
    return 0


def _verify(settings: config.Settings, args) -> int:
    import contextlib

    from workshop import verify
    from workshop.evidence import Manifest
    from workshop.forward import Forward

    banner(settings, "verify" + (f" {args.step}" if getattr(args, "step", None) else ""))
    evidence = args.evidence
    if not evidence:
        deployed = settings.state_dir / "deploy.json"
        if not deployed.is_file():
            log("nothing deployed yet: run the deploy steps first, `workshop deploy gates --pr N --confirm <sha7>` (or pass --evidence)")
            return EXIT["ERROR"]
        evidence = json.loads(deployed.read_text())["evidence"]
    from workshop import show, steps

    step = getattr(args, "step", None)
    try:
        if step == "probe":
            probes = verify.probe(settings, Path(evidence), log)
            log("")
            log(f"Evidence: {sum(p['ok'] for p in probes)} of {len(probes)} flows behave as the requirements say, on the real network. Next: workshop verify collect")
            return 0
        if step == "collect":
            steps.collect_step(settings, "post", log)
            return 0
        if step == "upload":
            steps.upload_step(settings, "post", log, extra=f"after {Manifest.read(Path(evidence)).candidate_sha256[:12]}")
            return 0
        if step == "wait":
            steps.wait_step(settings, "post", log)
            return 0
        if step == "compare":
            got = steps.processed_snapshot(settings, "post")
            probed = steps.load(settings, "probes", "workshop verify probe")
            if probed.get("evidence") != str(evidence):
                log("those probes were for a different deploy: run `workshop verify probe` again")
                return EXIT["ERROR"]
            log("checking the collection is of the network as it is now ...")
            show.napalm(log, "r1-4", "cli('show running-config')   hashed and compared with the post-change collection's fingerprint")
            if steps.live_fingerprint(settings) != got["fingerprint"]:
                log("the routers changed after `workshop verify collect`: run `workshop verify probe`, `collect`, `upload` and `wait` again, then compare")
                return EXIT["ERROR"]
            probes = probed["probes"]
            result = verify.compare(settings, Path(evidence), got["snapshot"], got["fingerprint"], probes, log)
        else:
            result = verify.run(settings, Path(evidence), log)
    except Exception as exc:  # noqa: BLE001 - a Forward or file error is ERROR, never a FAIL of the change
        log(f"workshop verify{' ' + step if step else ''}: {exc}")
        log(f"The change is applied; only the check did not finish. Run `workshop verify{' ' + step if step else ''}` again.")
        return EXIT["ERROR"]
    if result["outcome"] == "INCOMPLETE":
        log(result["reason"])
        return EXIT["INCONCLUSIVE"]
    with _display_only():
        log("")
        log("Requirements on the real network, compared with what Predict said:")
        for line in report_.requirement_table(result["rows"], compare=True):
            log(line)
        log("")
        log("Each requirement in detail:")
        for line in report_.requirement_lines(result["rows"], compare=True):
            log(line)
    with contextlib.suppress(Exception):  # display only: the verdict above never depends on this
        before = Manifest.read(Path(evidence)).baseline_snapshot_id
        with Forward(settings, quick=True) as fwd:
            table = report_.routing_table(fwd.c, str(before), str(result["post_snapshot_id"]))
        log("")
        log(f"What really changed in the routing tables (baseline {before} -> the network now, snapshot {result['post_snapshot_id']}):")
        for line in report_.routing_lines(table):
            log(line)
    log("")
    log(f"verify: {result['outcome']} (post-change snapshot {result['post_snapshot_id']})")
    with _display_only():
        for line in report_.verify_close(result["outcome"], result["rows"]) + report_.tagline("verify", result["outcome"]):
            log(line)
    return 0 if result["outcome"] == "MATCH" else EXIT["FAIL"]


def _snapshot_for(settings: config.Settings, which: str, evidence: str) -> str:
    from workshop.evidence import Manifest

    if which.isdigit():
        return which
    if which == "baseline":
        from workshop import steps

        found = steps.baseline_snapshot(settings)
        if found:
            return found
        raise LookupError("no baseline in Forward yet: run `workshop baseline collect`, `upload` and `wait` first")
    if which == "predicted":
        path = Path(evidence)
        if path.is_file() and Manifest.read(path).predicted_snapshot_id:
            return Manifest.read(path).predicted_snapshot_id
        raise LookupError(f"no predicted snapshot in {evidence}: run the predict steps (`workshop predict changeset`, `run`, `check`)")
    if which == "post":
        path = settings.state_dir / "verify.json"
        if path.is_file():
            return str(json.loads(path.read_text())["post_snapshot_id"])
        raise LookupError("no post-change snapshot yet: run the verify steps, starting with `workshop verify probe`")
    raise LookupError(f"unknown snapshot {which!r}: use baseline, predicted, post or a snapshot id")


def _answer(answer) -> None:
    for line in answer.summary.splitlines():
        log(f"  {line}")
    if answer.tools:
        log(f"  (Forward AI used: {', '.join(dict.fromkeys(answer.tools))})")


def _ask(settings: config.Settings, args) -> int:
    from workshop import ai
    from workshop.forward import Forward

    prompts = ai.sample_prompts(settings.repo_root)
    if args.list:
        for p in prompts.values():
            log(f"  {p.id:<24} [{p.role}, {p.snapshot}] {p.prompt}")
        return 0
    sample = prompts.get(args.prompt) if args.prompt else None
    if args.prompt and sample is None:
        log(f"no sample prompt {args.prompt!r}; workshop ask --list shows them")
        return EXIT["ERROR"]
    question = args.question or (sample.prompt if sample else "")
    if not question:
        log("ask a question, or pick one with --prompt (workshop ask --list)")
        return EXIT["ERROR"]
    try:
        snapshot = _snapshot_for(settings, args.snapshot or (sample.snapshot if sample else "baseline"), args.evidence)
    except LookupError as exc:
        log(f"workshop ask: {exc}")
        return EXIT["ERROR"]
    banner(settings, "ask")
    log(f"asking Forward AI about snapshot {snapshot} (answers take a minute or two; one question at a time) ...")
    try:
        with Forward(settings) as fwd:
            answer = fwd.ask_ai(question, snapshot)
            _save_transcript(settings, fwd, answer.chat_id)
    except ai.AiUnavailable as exc:
        log(f"workshop ask: {exc}")
        return EXIT["INCONCLUSIVE"]
    _answer(answer)
    if sample:
        log(f"\nwhat to look for: {sample.look_for}\nwhy it matters: {sample.why}")
    return 0


def _save_transcript(settings: config.Settings, fwd, chat_id: str) -> None:
    from workshop.forward import AiUnavailable

    try:
        text = fwd.ai_transcript(chat_id)
    except AiUnavailable:
        return
    path = settings.state(f"ai/{chat_id}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _propose(settings: config.Settings, args) -> int:
    from workshop import ai
    from workshop.forward import Forward

    banner(settings, "propose")
    try:
        with Forward(settings) as fwd:
            baseline = fwd.latest_baseline()
            if baseline is None:
                log("no baseline yet: run the baseline steps, starting with `workshop baseline collect`")
                return EXIT["STALE"]
            log("asking Predict's config assist to draft r4's change from intent.md ...")
            proposal = ai.author(fwd, lab_id=settings.lab_id, baseline_id=str(baseline.id), goal=ai.intent(settings.repo_root))
    except ai.AiUnavailable as exc:
        log(f"workshop propose: {exc}")
        return EXIT["INCONCLUSIVE"]
    log(proposal.file_text().rstrip())
    marker = settings.state("ai/proposed.txt")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("drafted\n")
    if not proposal.ok:
        log("this draft is outside the change's scope and cannot be used: " + "; ".join(proposal.problems))
        return EXIT["ERROR"]
    if args.write:
        (settings.repo_root / args.candidate).write_text(proposal.file_text())
        log(f"written to {args.candidate}. Review it (git diff), then open a pull request -- Predict judges it there.")
    else:
        log("not written (add --write). Nothing is judged until Predict evaluates it.")
    return 0


def _explain(settings: config.Settings, args) -> int:
    from workshop import ai, requirements
    from workshop.evidence import Manifest
    from workshop.forward import Forward

    path = Path(args.evidence)
    if not path.is_file():
        log(f"no evidence at {path}: run the predict steps first (`workshop predict changeset`, `run`, `check`)")
        return EXIT["ERROR"]
    m = Manifest.read(path)
    banner(settings, "explain")
    log(f"prediction: {m.status} -- {m.reason}")
    report: dict = {"evidence": str(path), "status": m.status}
    with Forward(settings) as fwd:
        rev = ai.review(fwd, m)
        report["review"] = rev.to_json()
        if rev.config:
            log(f"\nReviewer -- what the change alters:\n  {rev.config}")
        if rev.impact:
            log(f"\nReviewer -- what it would affect:\n  {rev.impact}")
        for note in rev.notes:
            log(f"  ({note})")
        if m.status == "FAIL" and not args.no_chat:
            log("\nTroubleshooter -- asking Forward AI why, on the predicted snapshot (a minute or two) ...")
            try:
                reqs = requirements.load(settings.repo_root / "requirements" / "candidate.yml", root=settings.repo_root)
                answer = ai.troubleshoot(fwd, m, reqs, timeout=args.chat_timeout)
                report["troubleshooter"] = {"chat_id": answer.chat_id, "summary": answer.summary, "tools": list(answer.tools)}
                _answer(answer)
            except ai.AiUnavailable as exc:
                log(f"  ({exc})")
    (path.parent / "ai.json").write_text(json.dumps(report, indent=2) + "\n")
    _append_summary(_explain_markdown(report))
    log("\nThis is advice. The verdict above came from Predict and the requirements, and it is unchanged.")
    return 0


def _explain_markdown(report: dict) -> str:
    rev = report.get("review", {})
    parts = ["\n### Forward AI review (advisory -- the gate result above is Predict's)\n"]
    if rev.get("name"):
        parts.append(f"**{rev['name']}**: {rev.get('description', '')}\n")
    if rev.get("config"):
        parts.append(f"**What the change alters:** {rev['config']}\n")
    if rev.get("impact"):
        parts.append(f"**What it would affect:** {rev['impact']}\n")
    if report.get("troubleshooter"):
        parts.append(f"**Troubleshooter:** {report['troubleshooter']['summary']}\n")
    parts += [f"_({note})_\n" for note in rev.get("notes", [])]
    return "\n".join(parts)


def _append_summary(text: str) -> None:
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as handle:
            handle.write(text)


def _agent(settings: config.Settings, args) -> int:
    from workshop import agent, predict
    from workshop.forward import Forward

    banner(settings, "agent")
    root = settings.repo_root
    inputs = predict.Inputs(root / args.candidate, root / args.requirements, root / args.baseline_requirements, {})
    with Forward(settings) as fwd:
        baseline = fwd.latest_baseline()
        if baseline is None:
            log("no baseline yet: run the baseline steps, starting with `workshop baseline collect`")
            return EXIT["STALE"]
        outcome = agent.run(settings, fwd, inputs, baseline_id=str(baseline.id), max_iterations=args.max_iterations,
                            log=log, out_dir=Path(args.out), fresh=args.fresh)
    log(f"\nagent finished after {len(outcome.attempts)} attempt(s): {outcome.status}")
    if outcome.status == "PASS":
        log(f"{args.candidate} now holds a change Predict passed. The agent stops here -- a person decides:")
        log(f"  git diff {args.candidate}      # read it")
        log("  commit it on a branch and open a pull request; the forward/predict check judges it again")
        log("  after review and merge: workshop deploy gates --pr <N> --confirm <sha7>, then render, apply, readback")
    else:
        log("no passing change within the budget. Read evidence/agent/agent.json, then take it from here yourself.")
    return EXIT.get(outcome.status, EXIT["ERROR"])


def _matrix(settings: config.Settings, args) -> int:
    from workshop import matrix, requirements

    banner(settings, "matrix")
    log("Predicting each combination against your baseline (about half a minute each). Nothing is written and nothing is deployed.")
    rows = matrix.run(settings, log, everything=args.all)
    log("")
    ids = matrix.requirement_ids(settings)
    log(matrix.table(rows, ids))
    titles = {r.id: r.title for r in requirements.load(settings.repo_root / "requirements" / "candidate.yml", root=settings.repo_root).requirements}
    for line in report_.matrix_notes(rows, titles):
        log(line)
    log("\nEach line is a real Forward prediction judged by your requirements. Which combinations are safe? Which pass every test?")
    return 0


def _ui(settings: config.Settings, args) -> int:
    from workshop import ui

    ui.serve(settings, settings.repo_root / args.evidence, args.port, log)
    return 0


def _revert(settings: config.Settings, args) -> int:
    from workshop import deploy, revert

    banner(settings, f"revert {args.step}")
    try:
        if args.step == "plan":
            revert.plan(settings, log)
            return 0
        if args.step == "compare":
            result = revert.compare(settings, log)
            log("")
            if result["outcome"] == "MATCH":
                log(f"revert: MATCH (snapshot {result['snapshot']}). What this means: Forward agrees the network is the one you started with:")
                log("the requirements hold, the configs are the original baseline's, and no route differs. The backout is verified, and that snapshot is your baseline now.")
                return 0
            log(f"revert: MISMATCH (snapshot {result['snapshot']}): " + "; ".join(result["reasons"]) + ".")
            log("The configs may be back, but Forward does not see the same network. Look at the differences above before you change anything else.")
            return EXIT["FAIL"]
        if not args.confirm:
            log("say which revert you reviewed: --confirm <the first 7 characters of its hash> (`workshop revert plan` prints it)")
            return EXIT["ERROR"]
        revert.apply(settings, args.confirm, log)
    except deploy.Refused as exc:
        log(f"NOT REVERTED ({exc.status}): {exc}")
        return EXIT[exc.status]
    log("")
    log("What this means: the deployed change is backed out, and the routers' configs are exactly the baseline's again (checked over NAPALM).")
    log("Now let Forward confirm it with a fresh collection: `workshop baseline collect`, `upload`, `wait`, then `workshop revert compare`.")
    return 0


def _restore(settings: config.Settings, args) -> int:
    from workshop import deploy, restore

    banner(settings, "restore")
    try:
        with deploy.lab_lock(settings):  # never alongside a deploy or another restore
            restore.run(settings, log)
    except deploy.Refused as exc:
        log(f"NOT RESTORED ({exc.status}): {exc}")
        return EXIT[exc.status]
    return 0


def _collector(settings: config.Settings, args) -> int:
    from workshop import collect
    from workshop.forward import Forward

    banner(settings, "collector")
    with Forward(settings) as fwd:
        path = collect.ensure(settings, fwd, log)
    log(f"collector: {path}")
    return 0


def _probe(settings: config.Settings, args) -> int:
    from workshop import lab

    banner(settings, "probe")
    results = []
    for port in lab.SERVICE_PORTS:
        p = lab.probe(port)
        results.append((port, p.reachable, p.listener_up))
    for line in report_.probe_lines(results, lab.SERVICE_IP):
        log(line)
    return 0


def _reset(settings: config.Settings, args) -> int:
    from workshop import reset

    banner(settings, "reset")
    log(f"This puts you at the start of {reset.STARTS[args.to]['what']}.")
    log("Your uncommitted work is kept in `git stash`; the lab is removed and netlab builds and configures it again (3 to 10 minutes).")
    if not args.yes:
        if not sys.stdin.isatty():
            log("Add --yes to run it without a question.")
            return EXIT["ERROR"]
        if input("Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
            log("Nothing changed.")
            return 0
    try:
        reset.run(settings, args.to, log)
    except Exception as exc:  # noqa: BLE001 - say what stopped it; every step before it is already done and safe to repeat
        log(f"workshop reset: {exc}")
        log("Every step is safe to run again: `workshop reset --to N` once more, or ask a proctor.")
        return EXIT["ERROR"]
    return 0


def _path(settings: config.Settings, args) -> int:
    from workshop import data, paths
    from workshop.forward import Forward

    evidence = settings.repo_root / args.evidence
    try:
        which = [(args.snapshot, data.snapshot_for(settings, evidence, args.snapshot))]
        if args.compare:
            which.append((args.compare, data.snapshot_for(settings, evidence, args.compare)))
        with Forward(settings) as fwd:
            traces = [paths.trace(fwd.c, snap, src=args.src, dst=args.to, port=args.port, label=label) for label, snap in which]
    except data.DataError as exc:
        log(f"workshop path: {exc}")
        return EXIT["ERROR"]
    except Exception as exc:  # noqa: BLE001 - Forward unreachable or slow: say so in one line
        log(f"workshop path: Forward could not answer ({type(exc).__name__}: {str(exc)[:160]})")
        return EXIT["ERROR"]
    for t in traces:
        for line in paths.render(t):
            log(line)
    if len(traces) == 2:
        for line in paths.compare(*traces):
            log(line)
    log("")
    log("Try: --port 2222 (blocked by r1's ACL) · --snapshot predicted · --compare predicted · --to 10.20.30.1 (a stale prefix)")
    return 0


def _nqe(settings: config.Settings, args) -> int:
    from workshop import data, show, term
    from workshop.forward import Forward

    path = settings.repo_root / args.query
    query = path.read_text() if args.query.endswith(".nqe") and path.is_file() else args.query
    try:
        snap = data.snapshot_for(settings, settings.repo_root / args.evidence, args.snapshot)
        show.sdk(log, f'nqe.query(<{"the query in " + args.query if query is not args.query else "your query"}>, snapshot_id="{snap}")')
        with Forward(settings) as fwd:
            rows = [dict(r) for r in fwd.c.nqe.query(query, snapshot_id=snap)]
    except data.DataError as exc:
        log(f"workshop nqe: {exc}")
        return EXIT["ERROR"]
    except Exception as exc:  # noqa: BLE001 - a query error from Forward is the most useful thing to show
        log(f"workshop nqe: Forward did not run the query: {str(exc)[:400]}")
        return EXIT["ERROR"]
    log("")
    log("The query, exactly as Forward ran it:")
    for line in query.strip().splitlines():
        log(f"  {line}")
    log("")
    log(f"{len(rows)} row(s) on {args.snapshot} snapshot {snap}")
    if rows:
        cols = list(rows[0])
        for line in term.table(cols, [[("" if r.get(c) is None else r.get(c)) for c in cols] for r in rows[:60]], max_width=90):
            log(line)
        if len(rows) > 60:
            log(f"... and {len(rows) - 60} more")
    log("")
    for line in term.how_to_read("NQE is Forward's query language over its model of the network: every row is one thing the query "
                                 "selected, and the columns are what its `select` asked for. A requirement of kind nqe passes or fails on "
                                 "how many rows come back. Run the same query with --snapshot predicted to see the network that would result."):
        log(line)
    return 0


def _data(settings: config.Settings, args) -> int:
    from workshop import data
    from workshop.forward import Forward

    evidence = settings.repo_root / args.evidence
    try:
        snap = data.snapshot_for(settings, evidence, args.snapshot)
        against = ""
        if args.topic == "diff":
            for which in ([args.against] if args.against else ["predicted", "post"]):
                try:
                    if which == "predicted":  # the prediction's own baseline, and only for the change in the candidate file now
                        snap, against = data.prediction_pair(settings, evidence)
                    else:
                        against = data.snapshot_for(settings, evidence, which)
                    break
                except data.NoSuchSnapshot:
                    continue  # that step has not run: try the next (a stale prediction is an error, not a reason to skip)
            if not against:
                raise data.NoSuchSnapshot("there is nothing to compare yet: run workshop predict (or deploy and verify) first")
        elif args.topic == "checks" and args.against:
            against = data.snapshot_for(settings, evidence, args.against)
        with Forward(settings) as fwd:
            table = data.ask(fwd.c, args.topic, snap, against=against, device=args.device, prefix=args.prefix)
    except data.DataError as exc:
        log(f"workshop data: {exc}")
        return EXIT["ERROR"]
    except Exception as exc:  # noqa: BLE001 - Forward unreachable or slow: say so in one line
        log(f"workshop data: Forward could not answer ({type(exc).__name__}: {str(exc)[:160]})")
        return EXIT["ERROR"]
    log(json.dumps(table.to_json(), indent=2, default=str) if args.json else data.render(table))
    return 0


def _status(settings: config.Settings, args) -> int:
    banner(settings, "status")
    from workshop import candidate

    def read(name: str):
        path = settings.state_dir / name
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    try:
        changes = len(candidate.load(settings.repo_root / "candidate" / "r4-bgp.eos").changes)
    except (candidate.CandidateError, OSError):
        changes = 0
    base, deployed, verified = read("baseline.json"), read("deploy.json"), read("verify.json")
    # a deploy or verify from an earlier loop is history, not progress
    if not base:
        deployed = verified = None
    for line in report_.status_lines(base, deployed, verified, changes):
        log(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
