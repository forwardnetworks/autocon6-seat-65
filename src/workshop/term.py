"""Terminal tables: boxed, aligned, with the verdict words in colour when the output is a real terminal.

Colour only when stdout is a TTY and ``NO_COLOR`` is unset, so the lab page (which captures the output) and CI logs get plain
text. Display only.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Sequence
from typing import Any

GOOD = {"PASS", "DELIVERED", "PERMIT", "PERMITTED", "MATCH", "OPEN", "ACL_PERMIT", "as predicted"}
BAD = {"FAIL", "BLACKHOLE", "DROPPED", "DENY", "DENIED", "ACL_DENY", "MISMATCH", "INADMISSIBLE", "UNREACHABLE", "LOOP", "removed"}
WARN = {"STALE", "ERROR", "INCONCLUSIVE", "changed", "added"}
_WORD = re.compile(r"as predicted|[A-Za-z_]+")  # the phrase first, or "as" alone would win
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def color_on() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(text: str) -> str:
    """Colour the verdict words inside ``text`` (green good, red bad, yellow neither)."""
    if not color_on():
        return text

    def one(m: re.Match) -> str:
        w = m.group(0)
        code = "32" if w in GOOD else "31" if w in BAD else "33" if w in WARN else ""
        return f"\x1b[1;{code}m{w}\x1b[0m" if code else w
    return _WORD.sub(one, text)


def width(text: str) -> int:
    return len(_ANSI.sub("", text))


def table(columns: Sequence[str], rows: Sequence[Sequence[Any]], *, max_width: int = 52) -> list[str]:
    """A boxed table. Cells longer than ``max_width`` are cut with an ellipsis; nothing wraps (terminals differ)."""
    cells = [[_cut(str(c), max_width) for c in row] for row in rows]
    widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(columns)]
    line = lambda l, m, r: l + m.join("─" * (w + 2) for w in widths) + r
    out = [line("┌", "┬", "┐"), "│" + "│".join(f" {h.ljust(w)} " for h, w in zip(columns, widths, strict=True)) + "│",
           line("├", "┼", "┤")]
    out += ["│" + "│".join(" " + paint(c) + " " * (w - len(c) + 1) for c, w in zip(r, widths, strict=True)) + "│" for r in cells]
    out.append(line("└", "┴", "┘"))
    return out


def _cut(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def heading(text: str) -> list[str]:
    return ["", text, "─" * min(len(text), 100)]


def how_to_read(text: str) -> list[str]:
    """The line under a table that says what it means. Wrapped at about 110 characters."""
    words, out, cur = text.split(), [], "How to read this:"
    for w in words:
        if len(cur) + len(w) + 1 > 110:
            out.append(cur)
            cur = "  " + w
        else:
            cur += " " + w
    return [*out, cur]
