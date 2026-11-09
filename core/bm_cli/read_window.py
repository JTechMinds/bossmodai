"""BossMod AI — Line windowing for the bounded file reads (``cat`` and ``rr``).

One token budget governs both reads. A read returns the whole lines that
fit, and when it stops early it names the exact ``rr`` command that reads on,
so a read never fails for being long and is never cut silently.

Pure: no I/O and no settings. The handlers in ``core.bm_cli.fs_commands`` read
the file and the budget and pass the token counter; this module owns the
arithmetic and the wording of the read-on note.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from core.context_budget import longest_fitting_prefix


@dataclass(frozen=True, slots=True)
class LineCut:
    """The one line a window shows only in part.

    Only a window's first line is ever cut, and only when that line alone
    renders over the token budget. Sizes are characters, which is what the
    read-on note tells the agent.

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
    max_tokens: int,
    numbered: bool,
    count: Callable[[str], int],
) -> ReadWindow:
    """Take the leading whole lines of ``start:end`` that fit one read's token budget.

    A line costs ``count(rendered + "\\n")``, its rendered text with the
    newline that joins it to the next, and lines are taken while the running
    total stays within ``max_tokens``. The first line is always shown, so a
    read always makes progress: when it alone is over ``max_tokens``, only
    its longest rendered character prefix that fits is shown (found by
    binary search), the cut is recorded, and the window stops there. No
    other line is ever split.

    Args:
        lines: The file's lines without line endings (``str.splitlines()``).
        start: 1-based first line to show.
        end: 1-based last line requested. Callers clamp it to ``len(lines)``.
        max_tokens: The most tokens of lines one read returns.
        numbered: ``True`` renders each line as ``"{n:>4} | {text}"``, as
            ``rr`` shows it; ``False`` renders the bare text, as ``cat`` does.
        count: The token counter (``core.llm.client.count_text_tokens``).

    Returns:
        The window: the shown line numbers, their rendered text, and the cut
        when the first line was shown in part.

    Raises:
        ValueError: Unless ``1 <= start <= end <= len(lines)`` and
            ``max_tokens >= 1``.
    """
    if not 1 <= start <= end <= len(lines):
        raise ValueError(
            f"Read window {start}:{end} must satisfy 1 <= start <= end <= {len(lines)} (the file's line count)."
        )
    if max_tokens < 1:
        raise ValueError(f"Read budget must be at least 1 token, got {max_tokens}.")

    first_text = lines[start - 1]
    first_rendered = _render(start, first_text, numbered=numbered)
    first_cost = count(first_rendered + "\n")
    if first_cost > max_tokens:
        gutter = len(first_rendered) - len(first_text)
        shown = longest_fitting_prefix(first_rendered, max_tokens=max_tokens, count=count, suffix="\n")
        return ReadWindow(
            first=start,
            last=start,
            requested_last=end,
            rendered=(shown,),
            cut=LineCut(line=start, total_chars=len(first_text), shown_chars=max(0, len(shown) - gutter)),
        )

    rendered = [first_rendered]
    used = first_cost
    last = start
    for number in range(start + 1, end + 1):
        candidate = _render(number, lines[number - 1], numbered=numbered)
        cost = count(candidate + "\n")
        if used + cost > max_tokens:
            break
        rendered.append(candidate)
        used += cost
        last = number
    return ReadWindow(first=start, last=last, requested_last=end, rendered=tuple(rendered), cut=None)


def read_on_note(window: ReadWindow, *, path: str, max_tokens: int) -> str | None:
    """Say what a partial read left out and the exact command that reads on.

    The note is runtime-authored and belongs in the result's
    ``authoritative_note``, never in the file-content section, so it cannot be
    mistaken for file text. The path is shell-quoted because the CLI parser
    tokenizes with ``shlex.split``; a virtual path works from any cwd.

    Args:
        window: The window the read showed.
        path: The file's virtual path, unquoted.
        max_tokens: The budget the window was taken with, named in the note.

    Returns:
        ``None`` when the window is complete. Otherwise one line per fact, in
        order: the cut line's sizes, then where the read stopped and the
        ``rr`` command for the rest.
    """
    if window.complete:
        return None
    return _note_text(
        cut=window.cut,
        last=window.last,
        requested_last=window.requested_last,
        next_start=window.next_start,
        path=path,
        max_tokens=max_tokens,
    )


def longest_read_on_note(*, path: str, start: int, first_line_chars: int, requested_last: int, max_tokens: int) -> str:
    """The longest note :func:`read_on_note` can write for one read, to reserve room for it.

    A read sizes its lines so that the whole rendered result, this note
    included, fits the result budget. The note is written only after the
    window is taken, so the room is reserved for its longest form: both
    sentences, with every number at the largest it can be for this read.

    Args:
        path: The file's virtual path, unquoted.
        start: 1-based first line of the read; only it can be cut.
        first_line_chars: Length of that line's text.
        requested_last: The last line requested, clamped to the file.
        max_tokens: An upper bound on the budget the note will name.

    Returns:
        The note text.
    """
    return _note_text(
        cut=LineCut(line=start, total_chars=first_line_chars, shown_chars=first_line_chars),
        last=requested_last,
        requested_last=requested_last,
        next_start=requested_last,
        path=path,
        max_tokens=max_tokens,
    )


def _note_text(
    *, cut: LineCut | None, last: int, requested_last: int, next_start: int | None, path: str, max_tokens: int,
) -> str:
    parts: list[str] = []
    if cut is not None:
        parts.append(
            f"Line {cut.line} has {cut.total_chars:,} characters; only the first {cut.shown_chars:,} are shown."
        )
    if next_start is not None:
        parts.append(
            f"Stopped after line {last} of {requested_last}: "
            f"one read returns up to {max_tokens:,} tokens. "
            f"Read on with: rr {shlex.quote(path)} {next_start}:{requested_last}"
        )
    return "\n".join(parts)
