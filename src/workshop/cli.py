"""The ``workshop`` command: one short command per step of the change loop.

    workshop doctor      is everything ready?
    workshop up          start the lab and apply the baseline
    workshop baseline    collect the network and give Forward a baseline
    workshop predict     (the PR check runs this) predict a candidate and judge it
    workshop deploy      deploy the approved, predicted change
    workshop verify      collect again and compare with the prediction
    workshop restore     put r4 back to the baseline (instructor recovery)
    workshop status      where this lab is in the loop
    workshop probe       test the real flows from the client (8080, 8443, 2222) right now

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
import subprocess
import sys
import time
from pathlib import Path

from workshop import config
from workshop import report as report_
from workshop.evidence import EXIT


def log(message: str) -> None:
    print(message, flush=True)


def banner(settings: config.Settings, action: str) -> None:
    log(f"== workshop {action} -- lab {settings.lab_id}, Forward network {settings.network_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workshop", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    sub.add_parser("doctor", help="check tools, Forward access and the lab")
    sub.add_parser("up", help="start the lab and apply the baseline")
    sub.add_parser("down", help="stop and remove the lab")
    sub.add_parser("baseline", help="collect the network and upload a tagged baseline")
    p = sub.add_parser("predict", help="predict a candidate change and judge it")
    p.add_argument("--candidate", default="candidate/r4-bgp.eos")
    p.add_argument("--requirements", default="requirements/candidate.yml")
    p.add_argument("--baseline-requirements", default="requirements/baseline.yml")
    p.add_argument("--pr", type=int)
    p.add_argument("--head-sha")
    p.add_argument("--base-sha")
    p.add_argument("--out", default="evidence", help="directory for evidence.json and summary.md")
    d = sub.add_parser("deploy", help="deploy the approved, predicted change")
    target = d.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=int, help="the merged pull request whose forward/predict evidence to deploy")
    target.add_argument("--evidence", help="an evidence.json from a local predict (rehearsal)")
    d.add_argument("--confirm", required=True, help="the first 7 characters of the candidate sha256 you reviewed")
    d.add_argument("--no-github", action="store_true", help="skip the merged-PR check (local rehearsal only)")
    v = sub.add_parser("verify", help="collect again and compare with the prediction")
    v.add_argument("--evidence", help="default: the evidence the last deploy used")
    sub.add_parser("restore", help="restore r4's baseline advertisements")
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
    dt.add_argument("topic", choices=["inventory", "routes", "vulns", "diff"])
    dt.add_argument("--snapshot", default="baseline", help="baseline | predicted | post | <snapshot id> (diff: the 'before' side)")
    dt.add_argument("--against", help="diff only: the 'after' side (default: predicted if there is one, else post)")
    dt.add_argument("--device", default="", help="only this device (routes, diff)")
    dt.add_argument("--prefix", default="", help="only this prefix, e.g. 10.20.20.0/24 (routes, diff)")
    dt.add_argument("--evidence", default="evidence/evidence.json", help="where 'predicted' comes from")
    dt.add_argument("--json", action="store_true", help="the structured answer as JSON, including Forward's raw response")
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
    for tool in ("docker", "netlab", "containerlab"):
        row(shutil.which(tool) is not None, f"{tool} installed")
    collector = sorted(settings.collector_home.glob("*/fwd/bin/fwd-headless")) if settings.collector_home.exists() else []
    row(True, "headless collector", str(collector[-1].parent.parent.parent.name) if collector else "not downloaded yet (workshop collector)")
    if shutil.which("docker"):
        try:
            states = lab.containers()
            running = sum(1 for s in states.values() if s == "running")
            row(running == 6 or not states, "lab containers", f"{running}/6 running" if states else "lab not started (workshop up)")
        except Exception as exc:  # noqa: BLE001
            row(False, "docker", str(exc)[:160])
    if os.environ.get("CODESPACES") and shutil.which("gh"):
        result = subprocess.run(["gh", "repo", "view", "--json", "viewerPermission", "--jq", ".viewerPermission"], capture_output=True, text=True, check=False)
        permission = result.stdout.strip()
        row(permission in ("WRITE", "MAINTAIN", "ADMIN"), "you can push to this seat repository",
            permission or "no access -- accept your seat invitation, then reopen this Codespace from the seat repository")
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
    banner(settings, "down")
    result = subprocess.run(["netlab", "down", "--cleanup"], cwd=settings.repo_root / "lab", check=False)
    if result.returncode == 0:
        log("lab removed: the six containers and their links are gone. `workshop up` builds it again (your Forward snapshots stay).")
    return 0 if result.returncode == 0 else EXIT["ERROR"]


def _baseline(settings: config.Settings, args) -> int:
    import contextlib

    from workshop import baseline
    from workshop.forward import Forward

    banner(settings, "baseline")
    try:
        snapshot_id, fp, report = baseline.run(settings, log)
    except Exception as exc:  # noqa: BLE001
        log(f"workshop baseline: {exc}")
        return EXIT["ERROR"]
    log(f"baseline snapshot {snapshot_id} (fingerprint {fp[:12]}): Forward's model of your network as it is now")
    with contextlib.suppress(Exception), Forward(settings, quick=True) as fwd:  # display only
        log(report_.inventory_line(fwd.c, snapshot_id))
    log("")
    log("Requirements checked on that model:")
    for line in report_.requirement_lines([r.to_json() for r in report.results]):
        log(line)
    log("")
    log(f"baseline: {report.status.value}")
    for line in report_.baseline_close(report.status.value) + report_.tagline("baseline", report.status.value):
        log(line)
    return EXIT[report.status.value]


def _predict(settings: config.Settings, args) -> int:
    import contextlib

    from workshop import evidence, predict
    from workshop.forward import Forward

    banner(settings, "predict")
    root = settings.repo_root
    git = {k: v for k, v in {"pr": args.pr, "head_sha": args.head_sha, "base_sha": args.base_sha,
                             "repo": os.environ.get("GITHUB_REPOSITORY"), "run_id": os.environ.get("GITHUB_RUN_ID"),
                             "actor": os.environ.get("GITHUB_ACTOR")}.items() if v}
    m = predict.run(settings, predict.Inputs(root / args.candidate, root / args.requirements, root / args.baseline_requirements, git), log=log)
    out = Path(args.out)
    m.write(out / "evidence.json")
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
            for line in report_.requirement_lines(m.results):
                log(line)
            log("")
            log(f"What the change does to the routing tables (baseline {m.baseline_snapshot_id} -> predicted {m.predicted_snapshot_id}):")
            for line in report_.routing_lines(routing):
                log(line)
        else:
            log("")
            log("No predicted network was built, so there are no requirements or routing changes to show for this change.")
        failing = [r["id"] for r in m.results if r["status"] == "FAIL"]
        for line in report_.predict_close(m.status, failing) + report_.tagline("predict", m.status, len(failing)):
            log(line)
    if m.deployable:
        target = f"--pr {args.pr}" if args.pr else f"--evidence {out / 'evidence.json'}"
        log(f"to deploy after merging: workshop deploy {target} --confirm {m.candidate_sha256[:7]}")
    return EXIT.get(m.status, EXIT["ERROR"])


def _deploy(settings: config.Settings, args) -> int:
    from workshop import deploy, github

    banner(settings, "deploy")
    try:
        if args.pr:
            log(f"fetching the forward/predict evidence for PR #{args.pr} ...")
            evidence = github.download_evidence(github.merged_pr(args.pr), settings.state(f"evidence-pr{args.pr}"))
        else:
            evidence = Path(args.evidence)
        deploy.run(settings, evidence, args.confirm, log, check_github=not args.no_github)
    except github.GitHubError as exc:
        log(f"NOT DEPLOYED (ERROR): {exc}")
        return EXIT["ERROR"]
    except deploy.Refused as exc:
        log(f"NOT DEPLOYED ({exc.status}): {exc}")
        return EXIT[exc.status]
    log("deployed. Next: workshop verify")
    return 0


def _verify(settings: config.Settings, args) -> int:
    import contextlib

    from workshop import verify
    from workshop.evidence import Manifest
    from workshop.forward import Forward

    banner(settings, "verify")
    evidence = args.evidence
    if not evidence:
        deployed = settings.state_dir / "deploy.json"
        if not deployed.is_file():
            log("nothing deployed yet: run workshop deploy first (or pass --evidence)")
            return EXIT["ERROR"]
        evidence = json.loads(deployed.read_text())["evidence"]
    result = verify.run(settings, Path(evidence), log)
    if result["outcome"] == "INCOMPLETE":
        log(result["reason"])
        return EXIT["INCONCLUSIVE"]
    log("")
    log("Requirements on the real network, compared with what Predict said:")
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
    for line in report_.verify_close(result["outcome"], result["rows"]) + report_.tagline("verify", result["outcome"]):
        log(line)
    return 0 if result["outcome"] == "MATCH" else EXIT["FAIL"]


def _snapshot_for(settings: config.Settings, which: str, evidence: str) -> str:
    from workshop.evidence import Manifest

    if which.isdigit():
        return which
    if which == "baseline":
        path = settings.state_dir / "baseline.json"
        if path.is_file():
            return str(json.loads(path.read_text())["snapshot"])
        raise LookupError("no baseline yet: run workshop baseline")
    if which == "predicted":
        path = Path(evidence)
        if path.is_file() and Manifest.read(path).predicted_snapshot_id:
            return Manifest.read(path).predicted_snapshot_id
        raise LookupError(f"no predicted snapshot in {evidence}: run workshop predict")
    if which == "post":
        path = settings.state_dir / "verify.json"
        if path.is_file():
            return str(json.loads(path.read_text())["post_snapshot_id"])
        raise LookupError("no post-change snapshot yet: run workshop verify")
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
                log("no baseline yet: run workshop baseline")
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
        log(f"no evidence at {path}: run workshop predict first")
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
            log("no baseline yet: run workshop baseline")
            return EXIT["STALE"]
        outcome = agent.run(settings, fwd, inputs, baseline_id=str(baseline.id), max_iterations=args.max_iterations,
                            log=log, out_dir=Path(args.out), fresh=args.fresh)
    log(f"\nagent finished after {len(outcome.attempts)} attempt(s): {outcome.status}")
    if outcome.status == "PASS":
        log(f"{args.candidate} now holds a change Predict passed. The agent stops here -- a person decides:")
        log(f"  git diff {args.candidate}      # read it")
        log("  commit it on a branch and open a pull request; the forward/predict check judges it again")
        log("  after review and merge: workshop deploy --pr <N> --confirm <sha7>")
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


def _restore(settings: config.Settings, args) -> int:
    from workshop import restore

    banner(settings, "restore")
    restore.run(settings, log)
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
