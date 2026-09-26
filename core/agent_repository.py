"""BossMod AI — the one owner of everything an agent owns.

``AgentRepository`` is the only public way to create or delete an agent. It
holds the single declaration of an agent's footprint, rows and files, and
composes the data layer (``db``) with the filesystem layer
(``core.bm_cli.filesystem``, ``core.agent_loop.standing_prefs``). The SQL
stays in ``db/`` as functions only this module calls.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import db
from core.agent_loop import standing_prefs
from core.agent_loop.meeting_orchestrator import end_meetings_hosted_by, end_meetings_without_host
from core.bm_cli import filesystem
from core.models import Agent
from core.models.message import HUMAN_SENDER_ID
from core.tasking.service import cancel_tasks_as_operator
from core.tasking.transitions import TERMINAL_TASK_STATUSES, is_terminal_task_status

logger = logging.getLogger(__name__)

# Values the agent-reference columns (messages.from_agent/to_agent,
# tasks.requester_id/created_by/…, channels.created_by) hold that were never
# agent ids. Every messages writer sends from or to an agent id or
# HUMAN_SENDER_ID (to_agent may also be NULL); tasks and threads name
# HUMAN_SENDER_ID as the operator. The orphan cleanup never treats these as a
# deleted agent.
NON_AGENT_ACTOR_IDS: frozenset[str] = frozenset({HUMAN_SENDER_ID})

# Agent-reference columns that delete_agent_rows deliberately leaves as they
# are. Every other ``agent_id`` / ``*_agent_id`` / ``from_agent`` /
# ``to_agent`` column must be handled there; tests/test_agent_repository.py
# checks the live schema against this, so a new table cannot be forgotten.
SHARED_OR_RETAINED: frozenset[tuple[str, str]] = frozenset(
    {
        # Add agent's Recent: hire configuration outlives the agent.
        ("agent_snapshots", "agent_id"),
        # The key ledger: a retired row is what keeps the key from reissue.
        ("agent_storage_keys", "agent_id"),
    }
)

ORPHAN_CANCEL_REASON = "Owner was deleted"
ORPHAN_MEETING_END_REASON = "host was deleted"


class AgentRepository:
    """Create and delete agents, and own the full list of what an agent owns.

    What an agent owns, and what a delete does with it:

    - Private rows, deleted: ``agents``, ``agent_state``, ``agent_cli_state``,
      ``agent_prompt_history_policies``, ``agent_storage_identities``,
      notifications and their links, DMs (``messages`` from or to it),
      ``diagnostics`` and their steps, ``activities`` and ``work_snapshots``,
      ``agent_triggers``, ``artifacts`` rows, CLI audit (``bm_cli_events``)
      and approvals, host-path consent, agent-scoped ``cli_policy_rules``
      (deleted, never detached: a NULL ``agent_id`` means a global rule),
      thread and meeting membership and response candidates.
    - Shared history, kept with the agent detached (id set to NULL): channel
      and meeting messages, meetings it created or hosted, task events, tasks
      (owner, requester, assignee, creator), threads it created, thread host
      work state, Telegram session targets.
    - Open tasks it owns or is assigned: cancelled first with the reason
      ``Owner <name> was deleted`` and mirrored to their origin thread.
      Closed tasks keep their history untouched.
    - Meetings it hosts that have not finished: ended next, since a meeting
      without its host never starts. An assembling one is canceled, an
      active one ended; the transcript says ``Meeting ended: host <name> was
      deleted.``, open rounds close, and other participants leave the
      meeting and resume their paused work. Finished meetings are untouched.
    - Retained: its ``agent_snapshots`` row (Add agent's Recent) and its
      ``agent_storage_keys`` ledger row, stamped retired. Keys only ever
      increase and are never reissued, so a new hire never inherits files.
    - Files (``owned_paths``): the ``/me`` workspace and the system-owned
      standing prefs file, both keyed by the storage key.

    A delete runs in this order: the caller has already reset the agent's
    runtime (no turn running); open tasks are cancelled and hosted meetings
    ended while the agent row still exists, since the origin mirror and the
    meeting line need it; one transaction removes
    or detaches every row and retires the key; the files go after commit. A
    file that cannot be removed raises with its path. It cannot leak to a
    later agent, because its key is never issued again.
    """

    def create(self, **fields: Any) -> Agent:
        """Hire one agent. The only creation path.

        Args:
            **fields: ``db.create_agent``'s keyword arguments (``name``,
                ``role``, models, credentials, desk, ``floor_id`` …).

        Returns:
            The created agent, with a storage key the ledger has never issued.

        Raises:
            ValueError: The named floor does not exist.
        """
        return db.create_agent(**fields)

    def owned_paths(self, storage_key: str) -> tuple[Path, ...]:
        """Return every file-system path an agent with ``storage_key`` owns.

        The single disk declaration: the ``/me`` workspace directory and the
        standing prefs file. ``filesystem.agent_artifact_dir`` creates the
        workspace directory when it is missing, as it does for every caller.

        Raises:
            ValueError: ``storage_key`` is empty or could escape its root.
        """
        return (
            filesystem.agent_artifact_dir(storage_key),
            standing_prefs.standing_prefs_file(storage_key),
        )

    def delete(self, agent_id: str) -> list[dict[str, Any]]:
        """Delete one agent and everything it owns.

        Precondition: the caller has awaited
        ``services.reset_agent_runtime(agent_id)``, so no turn of this agent
        is running. This method is synchronous and does not touch the runtime.

        Args:
            agent_id: The agent to delete.

        Returns:
            The lines to broadcast: the origin-thread lines posted for the
            cancelled open tasks, in the shape ``cancel_tasks_as_operator``
            returns, then one ``{"meeting_message": {...}, "agent_ids":
            [...]}`` per ended hosted meeting: its ``Meeting ended`` line
            (``broadcast_meeting_message`` keyword arguments minus
            ``agent_id``) and every other participant to paint it for.

        Raises:
            LookupError: No such agent (or it vanished before its rows went).
            OSError: A file could not be removed; the message names the path.
                The rows are already deleted and committed by then.
            RuntimeError: From ``db.delete_agent_rows``; nothing was deleted.
        """
        agent = db.get_agent(agent_id)
        if agent is None:
            raise LookupError(f"Agent {agent_id} not found")
        _cancelled, posted_lines = cancel_tasks_as_operator(
            self._open_task_ids(agent.id),
            reason=f"Owner {agent.name} was deleted",
        )
        # Before the rows go: the host detach in delete_agent_rows then only
        # touches meetings that are already over.
        for ended in end_meetings_hosted_by(agent.id, reason=f"host {agent.name} was deleted"):
            posted_lines.append(
                {"meeting_message": {**ended.message}, "agent_ids": list(ended.released_agent_ids)}
            )
        if not db.delete_agent_rows(agent.id):
            raise LookupError(f"Agent {agent_id} disappeared before its rows were deleted")
        for path in self.owned_paths(agent.storage_key):
            _remove_owned_path(path)
        return posted_lines

    def delete_all(self) -> int:
        """Delete every agent through ``delete``, then clear both per-agent roots.

        Same precondition as ``delete`` for every agent. Clearing the agents
        root and the standing prefs root also removes files no live agent
        owns (legacy name-keyed folders); both roots are recreated on next use.

        Returns:
            How many agents were deleted.

        Raises:
            Whatever ``delete`` raises, for the first agent that fails; the
            agents before it stay deleted.
        """
        agents = db.list_agents()
        for agent in agents:
            self.delete(agent.id)
        shutil.rmtree(filesystem.agents_artifact_root())
        filesystem.ensure_artifact_roots()
        shutil.rmtree(filesystem.standing_prefs_root())
        return len(agents)

    def purge_orphans(self) -> dict[str, int]:
        """Clean up what deletes before this repository left behind. Idempotent.

        Runs once per app start, from ``main.py``'s lifespan after
        ``db.init_db()`` and before the runtime worker starts. Never from
        ``init_db`` itself, which also runs on every runtime start.

        For ids in the agent-reference columns that no longer name an agent
        (NULL and ``NON_AGENT_ACTOR_IDS`` excepted), the delete rules apply:
        open tasks such an id owns or is assigned are cancelled with
        ``ORPHAN_CANCEL_REASON``, unfinished meetings whose host is missing
        or NULL are ended with ``ORPHAN_MEETING_END_REASON`` as a delete ends
        them, then ``db.purge_orphan_agent_rows`` deletes the private rows
        and detaches the shared ones. Files are not touched:
        the startup ledger seeding already keeps any stray key from reissue.

        Logs one warning line with every count when anything changed, and
        nothing when nothing did.

        Returns:
            Rows changed per table or ``table.column``, plus
            ``tasks_cancelled`` and ``meetings_ended``. All zeros on a second
            run.
        """
        task_ids = db.list_open_task_ids_owned_by_missing_agents(
            terminal_statuses=TERMINAL_TASK_STATUSES,
            non_agent_ids=NON_AGENT_ACTOR_IDS,
        )
        cancelled, _posted_lines = cancel_tasks_as_operator(task_ids, reason=ORPHAN_CANCEL_REASON)
        ended = end_meetings_without_host(reason=ORPHAN_MEETING_END_REASON)
        counts = {
            "tasks_cancelled": len(cancelled),
            "meetings_ended": len(ended),
            **db.purge_orphan_agent_rows(NON_AGENT_ACTOR_IDS),
        }
        if any(counts.values()):
            logger.warning(
                "Removed rows left by deleted agents: %s",
                ", ".join(f"{key}={value}" for key, value in counts.items()),
            )
        return counts

    @staticmethod
    def _open_task_ids(agent_id: str) -> list[str]:
        """Return ids of non-terminal tasks the agent owns or is assigned, oldest first."""
        ids: list[str] = []
        for task in [*db.list_tasks(owner_id=agent_id), *db.list_tasks(assigned_to=agent_id)]:
            if task.id in ids or is_terminal_task_status(task.status):
                continue
            ids.append(task.id)
        return ids


def _remove_owned_path(path: Path) -> None:
    """Remove one owned path. Missing is fine; any other failure names the path.

    A symlink is unlinked, never followed, so nothing outside the agent's
    roots can be removed through it.
    """
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    except OSError as exc:
        raise OSError(exc.errno, f"Could not remove {path} of a deleted agent: {exc}") from exc


agent_repository = AgentRepository()
