"""Browser Vision — recognising a site's bot check (pure; no browser, no I/O).

Some sites answer automated browsers with a challenge page instead of
content. Browser Vision does not try to get past one (no CAPTCHA solving, no
disguise): it recognises it, tells the agent to stop, and the commands put
the site on a cooldown to protect the operator's IP. The signals and their
lists come from the manifest's ``defaults.botwall`` block.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BotWallRules(BaseModel):
    """The manifest's ``defaults.botwall`` block, validated when the extension starts.

    Attributes:
        statuses: Main-document HTTP statuses that mean a block (403, 429).
        frame_markers: Substrings of a challenge provider's frame URL.
        text_patterns: Wording of a challenge page, matched case-insensitively.
        text_chars: How much of the main frame's visible text is searched.
        cooldown_minutes: How long a site that showed a bot check is left alone.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    statuses: tuple[int, ...] = Field(min_length=1)
    frame_markers: tuple[str, ...] = Field(min_length=1)
    text_patterns: tuple[str, ...] = Field(min_length=1)
    text_chars: int = Field(gt=0)
    cooldown_minutes: float = Field(gt=0)

    @field_validator("statuses")
    @classmethod
    def _statuses_are_http_codes(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        bad = [code for code in value if not 100 <= code <= 599]
        if bad:
            raise ValueError(f"not HTTP status codes: {bad}")
        return value

    @field_validator("frame_markers", "text_patterns")
    @classmethod
    def _no_blank_entries(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # A blank entry would be a substring of everything: every page a wall.
        if any(not entry.strip() for entry in value):
            raise ValueError("entries must not be blank")
        return value


def detect(status: int | None, frame_urls: list[str], title: str, text: str, rules: BotWallRules) -> str | None:
    """Say whether a page is a bot check, and why.

    Checked in this order, first match wins: the main document's HTTP
    status is in ``rules.statuses``; a frame URL contains one of
    ``rules.frame_markers``; the title or the first ``rules.text_chars``
    characters of the visible text contain one of ``rules.text_patterns``
    (case-insensitive, runs of whitespace read as one space).

    Args:
        status: The main document's HTTP status, ``None`` when it had none.
        frame_urls: The URLs of the page's child frames.
        title: The page title.
        text: The main frame's visible text.
        rules: The validated manifest rules.

    Returns:
        A short reason (e.g. ``HTTP 429``), or ``None`` for a normal page.
    """
    if status is not None and status in rules.statuses:
        return f"HTTP {status}"
    for url in frame_urls:
        lowered_url = url.lower()
        for marker in rules.frame_markers:
            if marker.lower() in lowered_url:
                return f"challenge frame from {marker}"
    # Title and text are searched apart so no pattern spans the two.
    searched = [_flatten(title), _flatten(text[: rules.text_chars])]
    for pattern in rules.text_patterns:
        wanted = _flatten(pattern)
        if any(wanted in part for part in searched):
            return f'the page says "{pattern}"'
    return None


def _flatten(value: str) -> str:
    return " ".join(value.split()).lower()
