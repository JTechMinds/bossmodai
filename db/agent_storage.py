"""BossMod AI — Normalize personal artifact storage to immutable storage keys."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from core.bm_cli.filesystem import (
    agent_artifact_dir,
    agents_artifact_root,
    legacy_agent_artifact_dir,
    transitional_agent_id_artifact_dir,
)
from core.models import Agent
from db.agents import list_agents
from db.artifacts import get_artifact_by_absolute_path, list_artifacts
from db.crud import execute

logger = logging.getLogger(__name__)


def normalize_agent_personal_storage_roots() -> None:
    """Move old personal storage roots into immutable human-readable storage keys."""
    for agent in list_agents():
        normalize_agent_personal_storage(agent)


def normalize_agent_personal_storage(agent: Agent) -> None:
    """Normalize one agent's personal storage into the canonical storage-key root."""
    target_root = agent_artifact_dir(agent.storage_key).resolve()
    legacy_roots = _legacy_roots_for_agent(agent, target_root)
    if not legacy_roots:
        return

    path_updates: dict[str, str] = {}
    for legacy_root in legacy_roots:
        path_updates.update(_move_legacy_root(legacy_root, target_root))

    _rewrite_artifact_paths(agent.id, path_updates)


def _legacy_roots_for_agent(agent: Agent, target_root: Path) -> list[Path]:
    """Return distinct legacy personal roots that still need migration."""
    roots: list[Path] = []
    seen: set[Path] = set()

    def remember(path: Path | None) -> None:
        if path is None:
            return
        resolved = path.resolve()
        if resolved == target_root or resolved in seen or not resolved.exists():
            return
        seen.add(resolved)
        roots.append(resolved)

    remember(legacy_agent_artifact_dir(agent.name))
    remember(transitional_agent_id_artifact_dir(agent.id))

    agents_root = agents_artifact_root().resolve()
    for artifact in list_artifacts(agent_id=agent.id, limit=5_000):
        root = _artifact_personal_root(Path(artifact.absolute_path), agents_root)
        remember(root)

    return roots


def _artifact_personal_root(path: Path, agents_root: Path) -> Path | None:
    """Return the personal root implied by one absolute artifact path."""
    try:
        resolved = path.resolve()
        relative = resolved.relative_to(agents_root)
    except (FileNotFoundError, ValueError):
        try:
            relative = path.relative_to(agents_root)
        except ValueError:
            return None
    if not relative.parts:
        return None
    return agents_root / relative.parts[0]


def _move_legacy_root(source_root: Path, target_root: Path) -> dict[str, str]:
    """Move one legacy personal root into the immutable target root."""
    if not source_root.exists() or source_root == target_root:
        return {}

    source_label = source_root.name
    updates: dict[str, str] = {}

    for directory in sorted(
        (item for item in source_root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.relative_to(source_root).parts),
    ):
        (target_root / directory.relative_to(source_root)).mkdir(parents=True, exist_ok=True)

    for source_file in sorted(
        (item for item in source_root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(source_root).as_posix(),
    ):
        relative = source_file.relative_to(source_root)
        destination = target_root / relative
        final_destination = _move_file(source_file, destination, source_label)
        updates[str(source_file.resolve())] = str(final_destination.resolve())

    _prune_empty_tree(source_root)
    logger.info("Normalized legacy personal storage %s -> %s", source_root, target_root)
    return updates


def _move_file(source: Path, destination: Path, source_label: str) -> Path:
    """Move one file into the target root without losing conflicting data."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.move(str(source), str(destination))
        return destination
    if destination.is_dir():
        raise RuntimeError(f"Cannot migrate file {source} over directory {destination}")
    if _files_match(source, destination):
        source.unlink()
        return destination

    conflict_destination = _build_conflict_path(destination, source_label)
    shutil.move(str(source), str(conflict_destination))
    logger.warning(
        "Preserved conflicting migrated personal file at %s while normalizing %s",
        conflict_destination,
        source,
    )
    return conflict_destination


def _files_match(source: Path, destination: Path) -> bool:
    """Return whether two files have the same content."""
    if source.stat().st_size != destination.stat().st_size:
        return False
    return source.read_bytes() == destination.read_bytes()


def _build_conflict_path(destination: Path, source_label: str) -> Path:
    """Return a stable non-destructive conflict path under the target root."""
    stem = destination.stem
    suffix = destination.suffix
    candidate = destination.with_name(f"{stem}__migrated_{source_label}{suffix}")
    index = 2
    while candidate.exists():
        candidate = destination.with_name(f"{stem}__migrated_{source_label}_{index}{suffix}")
        index += 1
    return candidate


def _prune_empty_tree(root: Path) -> None:
    """Delete any empty legacy directories after migration."""
    for directory in sorted(
        (item for item in root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.relative_to(root).parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()
    if root.exists() and root.is_dir() and not any(root.iterdir()):
        root.rmdir()


def _rewrite_artifact_paths(agent_id: str, path_updates: dict[str, str]) -> None:
    """Rewrite tracked artifact absolute paths to their normalized locations."""
    for old_path, new_path in path_updates.items():
        if old_path == new_path:
            continue
        current = get_artifact_by_absolute_path(old_path)
        if current is None or current.agent_id != agent_id:
            continue
        existing = get_artifact_by_absolute_path(new_path)
        if existing is None:
            execute(
                """
                UPDATE artifacts
                SET absolute_path = $1,
                    updated_at = current_timestamp
                WHERE id = $2
                """,
                [new_path, current.id],
            )
            continue

        if existing.agent_id == agent_id:
            execute("DELETE FROM artifacts WHERE id = $1", [current.id])
            continue

        raise RuntimeError(f"Artifact path collision while normalizing {old_path} -> {new_path}")
