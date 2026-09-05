"""Helpers used by more than one API router."""

from pathlib import Path

from core import config
from core.llm import context_builder
from core.llm.template_engine import validate_template


_TEXT_FILE_EXTENSIONS = {
    ".txt", ".md", ".json", ".py", ".js", ".ts", ".yaml", ".yml",
    ".toml", ".csv", ".xml", ".html", ".css", ".log", ".cfg", ".ini",
    ".sh", ".bash", ".env", ".sql", ".graphql", ".jsx", ".tsx", ".svg",
    ".rst", ".tex", ".makefile", ".dockerfile", ".gitignore",
}

_RUNTIME_CONTRACT_KEYS = {
    "decision": "runtime_contract_decision",
    "execution": "runtime_contract_execution",
    "trigger_event": "runtime_block_trigger_event",
    "conversation_envelope": "runtime_block_conversation_envelope",
    "file_deliverable_guidance": "runtime_block_file_deliverable_guidance",
    "communication_snapshot": "runtime_block_communication_snapshot",
}


def _validate_authored_prompt_template(template: str) -> None:
    """Validate one authored prompt template against the shared template engine."""
    validate_template(
        template,
        allowed_paths=context_builder.AUTHORED_PROMPT_ALLOWED_PATHS,
    )


def _read_desk_file_preview(path: Path, limit_chars: int = 20_000) -> tuple[str, bool]:
    """Read a bounded UTF-8 preview for one desk file."""
    configured_limit = config.get_int("desk_preview_max_chars")
    if configured_limit is not None and configured_limit > 0:
        limit_chars = configured_limit
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        content = handle.read(limit_chars + 1)
    return content[:limit_chars], len(content) > limit_chars


def _child_virtual_path(parent: str, name: str) -> str:
    if parent in {"", "/"}:
        return f"/{name}"
    return f"{parent.rstrip('/')}/{name}"


def _launch_file_explorer(path: Path, *, opener: str) -> None:
    """Open a directory in the host platform's file explorer."""
    from core.file_explorer import launch
    launch(path, opener)


def _available_folder_opener_options() -> list[dict[str, str]]:
    """Return detected folder opener choices for the current platform."""
    from core.file_explorer import available_options
    return available_options()
