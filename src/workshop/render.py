"""``workshop deploy render``: one change, two applications.

The candidate (candidate/r4-bgp.eos, identified by its sha256) is the one model of the change. It is applied twice:

  * inside Forward, by Predict: the change set holds those lines for r4, and Forward's model of r4 changes accordingly;
  * on the router, by NAPALM: the same lines staged in an EOS configuration session, and r4's own diff of what it would do.

This shows both, side by side, from their sources: Forward's copy of r4 before and after the change set's commit, and r4's own
``compare_config`` of a session that is then discarded (nothing on the router changes). If the two disagree, that is a finding:
the model and the device read the change differently, and deploy should not go ahead until someone understands why.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from workshop import candidate, executor, lab, show, term
from workshop.config import Settings
from workshop.evidence import Manifest
from workshop.forward import Forward


@dataclass
class Rendered:
    sha: str
    lines: list[str]  # the change itself
    forward_commands: str  # what Forward's change set holds for r4
    forward_diff: list[str]  # r4's configuration in Forward's model: baseline -> after the change set
    router_diff: list[str]  # r4's own compare_config of the staged session
    agree: bool


def _changed(diff: list[str]) -> set[str]:
    """The added and removed lines of a diff, without their +/- and indentation, ignoring headers and blank lines."""
    out = set()
    for line in diff:
        if line.startswith(("+++", "---", "@@")):
            continue
        if line[:1] in "+-" and line[1:].strip():
            out.add(line[0] + " " + " ".join(line[1:].split()))
    return out


def _section(text: str, start: str = "router bgp") -> list[str]:
    lines = text.splitlines()
    begin = next((i for i, line in enumerate(lines) if line.startswith(start)), None)
    if begin is None:
        return []
    end = next((i for i in range(begin + 1, len(lines)) if lines[i] and not lines[i].startswith(" ")), len(lines))
    return lines[begin:end]


def build(settings: Settings, m: Manifest, log=None) -> Rendered:
    lines = candidate.normalize(m.candidate_text)
    with Forward(settings) as fwd:
        handle = fwd.c.change_sets.handle(m.change_set_id)
        show.sdk(log, f'change_sets.handle("{m.change_set_id}").head().device_to_changes["r4"]   the commands Forward holds for r4')
        head = handle.head()
        held = ((head.device_to_changes or {}).get(candidate.DEVICE))
        commands = (held.commands if held else "") or ""
        show.sdk(log, f'devices.file("r4", "configuration.txt", snapshot_id="{m.baseline_snapshot_id}")   r4 in Forward\'s model, before')
        before = fwd.c.devices.file(candidate.DEVICE, "configuration.txt", snapshot_id=m.baseline_snapshot_id)
        show.sdk(log, f'committed_device_config("{head.commit_id}", "r4")   r4 in Forward\'s model, after the change set')
        after = handle.committed_device_config(str(head.commit_id), candidate.DEVICE)
    forward_diff = list(difflib.unified_diff(_section(before), _section(after), "r4 in Forward (baseline)", "r4 in Forward (with the change)", lineterm="", n=1))
    show.napalm(log, candidate.DEVICE, "load_merge_candidate · compare_config · discard_config   a dry run: r4's own diff, then thrown away")
    router = executor.preview(settings, candidate.DEVICE, lab.mgmt_ips(settings)[candidate.DEVICE], lines)
    router_diff = [line for line in router.splitlines() if line.strip()]
    agree = _changed(forward_diff) == _changed(router_diff)
    return Rendered(m.candidate_sha256, lines, commands, forward_diff, router_diff, agree)


def lines_for(r: Rendered) -> list[str]:
    out = [f"The change: candidate/r4-bgp.eos, sha256 {r.sha[:12]}, {len(r.lines)} lines. One model, applied two ways:", ""]
    for line in r.lines:
        out.append(f"    {line}")
    out += ["", "1. Inside Forward (Predict). The change set holds these commands for r4:"]
    out += [f"    {line}" for line in r.forward_commands.splitlines() if line.strip()] or ["    (none: was the change set committed?)"]
    out += ["   and r4's configuration in Forward's model changes like this:"]
    out += [f"    {term.paint(line) if line[:1] in '+-' and not line.startswith(('+++', '---')) else line}" for line in r.forward_diff] or ["    (no difference)"]
    out += ["", "2. On the router (NAPALM, a dry run: staged, compared, discarded). r4's own diff of the same lines:"]
    out += [f"    {line}" for line in r.router_diff] or ["    (no difference: r4 already matches)"]
    out.append("")
    both = sorted(_changed(r.router_diff))
    if r.agree:
        out.append(f"✓ Forward and r4 agree: the same {len(both)} line(s) change in both " + ("(" + "; ".join(re.sub(r"\s+", " ", b) for b in both) + ")" if both else ""))
        out.append("  What Predict judged is what the router will do. `workshop deploy apply` sends exactly these lines.")
    else:
        out.append("✗ Forward and r4 do NOT agree on what this change does. Stop here and find out why before deploying:")
        out.append(f"  only in Forward: {sorted(_changed(r.forward_diff) - _changed(r.router_diff))}")
        out.append(f"  only on r4:      {sorted(_changed(r.router_diff) - _changed(r.forward_diff))}")
    return out
