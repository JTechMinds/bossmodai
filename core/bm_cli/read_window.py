"""BossMod AI — Line windowing for the bounded file reads (``cat`` and ``rr``).

One character budget governs both reads. A read returns the whole lines that
fit, and when it stops early it names the exact ``rr`` command that reads on,
so a read never fails for being long and is never cut silently.

Pure: no I/O and no settings. The handlers in ``core.bm_cli.fs_commands`` read
the file and the budget; this module owns the arithmetic and the wording of
the read-on note.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LineCut:
    """The one line a window shows only in part.

    Only a window's first line is ever cut, and only when that line alone
    renders longer than the budget.

    Attributes:
        line: 1-based number of the cut line.
        total_chars: Length of the line's full text, without the gutter.
        shown_chars: Text characters shown, without the gutter. 0 when the
            budget is smaller than the numbered gutter, never negative.
    """

    line: int
    total_chars: int
    shown_chars: int


@dataclass(frozen=True, slots=True)
class ReadWindow:
    """The lines one read shows, rendered as the model sees them.

    Attributes:
        first: 1-based number of the first line shown.
        last: 1-based number of the last line shown.
        requested_last: The last line the caller asked for, already clamped
            to the file's last line.
        rendered: One rendered string per shown line, ``first`` to ``last``.
        cut: The first line when it was shown only in part, else ``None``.
    """

    first: int
    last: int
    requested_last: int
    rendered: tuple[str, ...]
    cut: LineCut | None

    @property
    def next_start(self) -> int | None:
        """The line a follow-up read starts at, or ``None`` when none was left out."""
        return self.last + 1 if self.last < self.requested_last else None

    @property
    def complete(self) -> bool:
        """Whether every requested line was shown whole."""
        return self.next_start is None and self.cut is None


def _render(number: int, text: str, *, numbered: bool) -> str:
    return f"{number:>4} | {text}" if numbered else text


def take_read_window(
    lines: Sequence[str],
    *,
    start: int,
    end: int,
    max_chars: int,
    numbered: bool,
) -> ReadWindow:
    """Take the leading whole lines of ``start:end`` that fit one read's budget.

    A line costs its rendered length plus 1 for the newline that joins it to
    the next, and lines are taken while the running total stays within
    ``max_chars``. The first line is always shown, so a read always makes
    progress: when it alone renders longer than ``max_chars``, only its first
    ``max_chars`` rendered characters are shown, the cut is recorded, and the
    window stops there. No other line is ever split.

    Args:
        lines: The file's lines without line endings (``str.splitlines()``).
        start: 1-based first line to show.
        end: 1-based last line requested. Callers clamp it to ``len(lines)``.
        max_chars: The most rendered characters one read returns.
        numbered: ``True`` renders each line as ``"{n:>4} | {text}"``, as
            ``rr`` shows it; ``False`` renders the bare text, as ``cat`` does.

    Returns:
        The window: the shown line numbers, their rendered text, and the cut
        when the first line was shown in part.

    Raises:
        ValueError: Unless ``1 <= start <= end <= len(lines)`` and
            ``max_chars >= 1``.
    """
    if not 1 <= start <= end <= len(lines):
        raise ValueError(
            f"Read window {start}:{end} must satisfy 1 <= start <= end <= {len(lines)} (the file's line count)."
        )
    if max_chars < 1:
        raise ValueError(f"Read budget must be at least 1 character, got {max_chars}.")

    first_text = lines[start - 1]
    first_rendered = _render(start, first_text, numbered=numbered)
    if len(first_rendered) > max_chars:
        gutter = len(first_rendered) - len(first_text)
        return ReadWindow(
            first=start,
            last=start,
            requested_last=end,
            rendered=(first_rendered[:max_chars],),
            cut=LineCut(line=start, total_chars=len(first_text), shown_chars=max(0, max_chars - gutter)),
        )

    rendered = [first_rendered]
    used = len(first_rendered) + 1
    last = start
    for number in range(start + 1, end + 1):
        candidate = _render(number, lines[number - 1], numbered=numbered)
        cost = len(candidate) + 1
        if used + cost > max_chars:
            break
        rendered.append(candidate)
        used += cost
        last = number
    return ReadWindow(first=start, last=last, requested_last=end, rendered=tuple(rendered), cut=None)


def read_on_note(window: ReadWindow, *, path: str, max_chars: int) -> str | None:
    """Say what a partial read left out and the exact command that reads on.

    The note is runtime-authored and belongs in the result's
    ``authoritative_note``, never in the file-content section, so it cannot be
    mistaken for file text. The path is shell-quoted because the CLI parser
    tokenizes with ``shlex.split``; a virtual path works from any cwd.

    Args:
        window: The window the read showed.
        path: The file's virtual path, unquoted.
        max_chars: The budget the window was taken with, named in the note.

    Returns:
        ``None`` when the window is complete. Otherwise one line per fact, in
        order: the cut line's sizes, then where the read stopped and the
        ``rr`` command for the rest.
    """
    if window.complete:
        return None
    parts: list[str] = []
    if window.cut is not None:
        cut = window.cut
        parts.append(
            f"Line {cut.line} has {cut.total_chars:,} characters; only the first {cut.shown_chars:,} are shown."
        )
    if window.next_start is not None:
        parts.append(
            f"Stopped after line {window.last} of {window.requested_last}: "
            f"one read returns up to {max_chars:,} characters. "
            f"Read on with: rr {shlex.quote(path)} {window.next_start}:{window.requested_last}"
        )
    return "\n".join(parts)
