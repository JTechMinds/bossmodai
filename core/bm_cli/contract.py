"""BossMod AI — Prompt-visible BossMod CLI contract."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BossModCliCall(BaseModel):
    """A structured request to query the controlled BossMod CLI surface."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["bm_cli"]
    command: str
    content: str | None = None
    thought: str = Field(default="")

    @model_validator(mode="after")
    def _validate_shape(self) -> "BossModCliCall":
        if not self.command.strip():
            raise ValueError('"bm_cli" requires a non-empty "command"')
        if self.content is not None and not isinstance(self.content, str):
            raise ValueError('"bm_cli" content must be a string when provided')
        return self


def maybe_parse_bm_cli_call(payload: Any) -> BossModCliCall | None:
    """Return a validated BossMod CLI call when the payload requests one."""
    if not isinstance(payload, dict):
        return None
    if payload.get("action") != "bm_cli":
        return None
    return BossModCliCall.model_validate(payload)


def render_bm_cli_guidance() -> str:
    """Render the authoritative starter guidance for BossMod CLI access."""
    return "\n".join(
        [
            "BOSSMOD CLI:",
            '- Use {"action":"bm_cli","command":"...","thought":"..."} when you need authoritative self/project information.',
            '- BossMod CLI behaves like a bounded virtual shell rooted at "/" with "/me" and "/projects" mounts.',
            '- Your personal /me workspace is versioned with a local Git repo. "/me/scratchpad" stays untracked for throwaway work.',
            '- Your default working directory is "/me". Use "pwd" and "cd" to inspect or change it.',
            '- For write-style commands, include a separate "content" field instead of cramming long text into the command string.',
            "- BossMod CLI results are turn-local. Use them for the current reply/action; they do not become normal chat memory.",
            "- Starter commands:",
            "  - pwd",
            "  - cd <path>",
            "  - ls [path]",
            "  - cat <path>",
            "  - mkdir <path>",
            "  - write <path>   (requires content)",
            "  - append <path>   (requires content)",
            "  - status",
            "  - runtime",
            "  - activity",
            "  - current-task",
            "  - tasks",
            "  - recent-work",
            "  - location",
            "  - git status",
            "  - git log [limit]",
            "  - git diff [path]",
            "  - git show <revision> [path]",
            "  - git restore [--source <revision>] <path>",
        ]
    )
