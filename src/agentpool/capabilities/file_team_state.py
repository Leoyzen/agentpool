"""File-based team state persistence for dynamic team mode.

Provides :class:`FileTeamState`, a synchronous file-I/O backend that stores
team metadata, member inboxes, task boards, and a versioned blackboard on
the local filesystem. All writes are atomic (tmp + ``os.replace``) and
blackboard writes are protected by :class:`filelock.FileLock` with
optimistic version locking.

Directory layout::

    {base_dir}/teams/{team_id}/
        state.json
        inboxes/{member_name}/
        tasks/
        blackboard/
        blackboard/.locks/
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
from pathlib import Path
import re
import shutil
from typing import Any
import uuid

from filelock import FileLock


__all__ = ["FileTeamState", "start_team_cleanup_task"]

_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_/]+$")


class FileTeamState:
    """Synchronous file-based store for team state.

    All methods perform blocking file I/O — do not call from async
    hot paths without offloading to a thread executor.
    """

    def __init__(self, base_dir: str) -> None:
        """Store the base directory for team state files.

        Args:
            base_dir: Root directory under which ``teams/`` is created.
        """
        self._base_dir = Path(base_dir)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _teams_dir(self) -> Path:
        return self._base_dir / "teams"

    def _team_dir(self, team_id: str) -> Path:
        return self._teams_dir() / team_id

    def _state_path(self, team_id: str) -> Path:
        return self._team_dir(team_id) / "state.json"

    def _inbox_dir(self, team_id: str, member_name: str) -> Path:
        return self._team_dir(team_id) / "inboxes" / member_name

    def _tasks_dir(self, team_id: str) -> Path:
        return self._team_dir(team_id) / "tasks"

    def _blackboard_dir(self, team_id: str) -> Path:
        return self._team_dir(team_id) / "blackboard"

    def _locks_dir(self, team_id: str) -> Path:
        return self._blackboard_dir(team_id) / ".locks"

    # ------------------------------------------------------------------
    # Atomic write helper
    # ------------------------------------------------------------------

    @staticmethod
    def _atomic_write(path: Path, data: dict[str, Any]) -> None:
        """Write *data* as JSON to *path* atomically.

        Writes to a sibling temporary file first, then ``os.replace``.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        """Read and parse a JSON file."""
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    # ------------------------------------------------------------------
    # Team lifecycle
    # ------------------------------------------------------------------

    def init(
        self,
        team_id: str,
        team_name: str,
        members: list[dict[str, str]],
    ) -> None:
        """Create the team directory structure and initial state.json.

        Args:
            team_id: Unique team identifier (used as directory name).
            team_name: Human-readable team name.
            members: List of member dicts, each with at least ``name``.
        """
        team_dir = self._team_dir(team_id)
        team_dir.mkdir(parents=True, exist_ok=True)
        self._inbox_dir(team_id, "_").parent.mkdir(parents=True, exist_ok=True)
        self._tasks_dir(team_id).mkdir(parents=True, exist_ok=True)
        self._blackboard_dir(team_id).mkdir(parents=True, exist_ok=True)
        self._locks_dir(team_id).mkdir(parents=True, exist_ok=True)

        members_map: dict[str, dict[str, str]] = {}
        for member in members:
            name = member["name"]
            members_map[name] = {
                "agent": member.get("agent", name),
                "session_id": "",
            }

        state: dict[str, Any] = {
            "team_name": team_name,
            "members": members_map,
            "status": "active",
            "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "ended_at": None,
        }
        self._atomic_write(self._state_path(team_id), state)

    def register_member(
        self,
        team_id: str,
        member_name: str,
        session_id: str,
        *,
        agent: str | None = None,
    ) -> None:
        """Write a member's session_id into state.json.

        Args:
            team_id: Team to update.
            member_name: Member whose session to record.
            session_id: Session identifier to persist.
            agent: Agent type (e.g. "historian").  If provided, stored
                in the member record.  If not provided, defaults to
                ``member_name`` for backward compatibility.
        """
        state = self._read_json(self._state_path(team_id))
        members: dict[str, dict[str, str]] = state["members"]
        if member_name not in members:
            members[member_name] = {
                "agent": agent or member_name,
                "session_id": "",
            }
        members[member_name]["session_id"] = session_id
        if agent is not None:
            members[member_name]["agent"] = agent
        self._atomic_write(self._state_path(team_id), state)

    def get_member_session_id(self, team_id: str, member_name: str) -> str | None:
        """Return the session_id for a member, or ``None`` if not registered.

        Args:
            team_id: Team to query.
            member_name: Member to look up.
        """
        state = self._read_json(self._state_path(team_id))
        members: dict[str, dict[str, str]] = state["members"]
        member = members.get(member_name)
        if member is None:
            return None
        sid: str = member.get("session_id", "")
        return sid if sid else None

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def write_message(
        self,
        team_id: str,
        member_name: str,
        message: dict[str, Any],
    ) -> None:
        """Atomically write a message to a member's inbox.

        Args:
            team_id: Team whose inbox to write to.
            member_name: Recipient member name.
            message: Message payload dict.
        """
        inbox = self._inbox_dir(team_id, member_name)
        inbox.mkdir(parents=True, exist_ok=True)
        msg_id = str(uuid.uuid4())
        path = inbox / f"{msg_id}.json"
        self._atomic_write(path, message)

    def read_messages(self, team_id: str, member_name: str) -> list[dict[str, Any]]:
        """Return all messages in a member's inbox, sorted by timestamp.

        Args:
            team_id: Team whose inbox to read.
            member_name: Member whose messages to retrieve.
        """
        inbox = self._inbox_dir(team_id, member_name)
        if not inbox.exists():
            return []
        messages = [self._read_json(f) for f in inbox.glob("*.json")]
        messages.sort(key=lambda m: m.get("timestamp", ""))
        return messages

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def create_task(self, team_id: str, task: dict[str, Any]) -> str:
        """Create a new task file and return its task_id.

        Args:
            team_id: Team to add the task to.
            task: Task payload dict.
        """
        tasks_dir = self._tasks_dir(team_id)
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        task_data = {**task, "task_id": task_id}
        if "status" not in task_data:
            task_data["status"] = "pending"
        if "blocked_by" not in task_data:
            task_data["blocked_by"] = []
        self._atomic_write(tasks_dir / f"{task_id}.json", task_data)
        return task_id

    def list_tasks(self, team_id: str) -> list[dict[str, Any]]:
        """Return all tasks with computed ``is_unblocked`` field.

        A task is unblocked when all ``blocked_by`` tasks have
        ``status == "completed"``. Failed dependencies do NOT unblock.

        Args:
            team_id: Team whose tasks to list.
        """
        tasks_dir = self._tasks_dir(team_id)
        if not tasks_dir.exists():
            return []
        tasks: list[dict[str, Any]] = []
        task_by_id: dict[str, dict[str, Any]] = {}
        for f in tasks_dir.glob("*.json"):
            t = self._read_json(f)
            tasks.append(t)
            tid: str = t.get("task_id", f.stem)
            task_by_id[tid] = t

        for t in tasks:
            blocked_by: list[str] = t.get("blocked_by", [])
            if not blocked_by:
                t["is_unblocked"] = True
                continue
            deps = [task_by_id.get(dep) for dep in blocked_by]
            t["is_unblocked"] = all(
                dep is not None and dep.get("status") == "completed" for dep in deps
            )
        return tasks

    def update_task(
        self,
        team_id: str,
        task_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge *updates* into an existing task and return the result.

        Args:
            team_id: Team containing the task.
            task_id: Task to update.
            updates: Fields to merge into the task.
        """
        path = self._tasks_dir(team_id) / f"{task_id}.json"
        task = self._read_json(path)
        task.update(updates)
        self._atomic_write(path, task)
        return task

    # ------------------------------------------------------------------
    # Blackboard
    # ------------------------------------------------------------------

    def _validate_key(self, key: str, blackboard_dir: Path) -> Path:
        """Validate a blackboard key and return the safe file path.

        Args:
            key: Blackboard key to validate.
            blackboard_dir: Resolved blackboard directory.

        Raises:
            ValueError: If the key contains invalid characters or
                attempts path traversal.
        """
        if not _KEY_PATTERN.match(key):
            msg = f"Invalid blackboard key: {key!r}"
            raise ValueError(msg)
        key_path = (blackboard_dir / f"{key}.json").resolve()
        bb_resolved = blackboard_dir.resolve()
        try:
            key_path.relative_to(bb_resolved)
        except ValueError:
            msg = f"Path traversal detected in blackboard key: {key!r}"
            raise ValueError(msg) from None
        return key_path

    def read_blackboard(self, team_id: str, key: str) -> dict[str, Any] | None:
        """Return the blackboard value + metadata for *key*, or ``None``.

        Args:
            team_id: Team whose blackboard to read.
            key: Blackboard key.
        """
        bb_dir = self._blackboard_dir(team_id)
        key_path = self._validate_key(key, bb_dir)
        if not key_path.exists():
            return None
        return self._read_json(key_path)

    def write_blackboard(
        self,
        team_id: str,
        key: str,
        value: dict[str, Any],
        expected_version: int | None = None,
        written_by: str = "unknown",
        mode: str = "overwrite",
    ) -> str:
        """Write a value to the blackboard with optimistic locking.

        Args:
            team_id: Team whose blackboard to write to.
            key: Blackboard key.
            value: Value payload to store.
            expected_version: Expected current version for optimistic
                locking.  If ``None``, no version check is performed.
            written_by: Name of the writer.
            mode: ``"overwrite"`` (default) replaces the value entirely;
                ``"append"`` concatenates to the existing ``text`` field.

        Returns:
            ``"Written, version=N"`` on success, or
            ``"Conflict: current version is N"`` on version mismatch.
        """
        bb_dir = self._blackboard_dir(team_id)
        self._locks_dir(team_id).mkdir(parents=True, exist_ok=True)
        key_path = self._validate_key(key, bb_dir)
        lock_path = self._locks_dir(team_id) / f"{key}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(lock_path))
        with lock:
            current: dict[str, Any] | None = None
            current_version = 0
            if key_path.exists():
                current = self._read_json(key_path)
                current_version = current.get("version", 0)

            if expected_version is not None and expected_version != current_version:
                return f"Conflict: current version is {current_version}"

            new_version = current_version + 1

            if mode == "append" and current is not None:
                old_text = current.get("value", {}).get("text", "")
                new_text = value.get("text", "")
                if old_text:
                    merged_value: dict[str, Any] = {"text": old_text + "\n" + new_text}
                else:
                    merged_value = {"text": new_text}
            else:
                merged_value = value

            entry: dict[str, Any] = {
                "value": merged_value,
                "version": new_version,
                "written_by": written_by,
                "written_at": datetime.datetime.now(datetime.UTC).isoformat(),
            }
            self._atomic_write(key_path, entry)
            return f"Written, version={new_version}"

    def list_blackboard(self, team_id: str) -> list[str]:
        """Return all blackboard keys (without the ``.json`` suffix).

        Args:
            team_id: Team whose blackboard to list.
        """
        bb_dir = self._blackboard_dir(team_id)
        if not bb_dir.exists():
            return []
        keys = [f.stem for f in bb_dir.glob("*.json")]
        return sorted(keys)

    def delete_blackboard(self, team_id: str, key: str) -> None:
        """Delete a blackboard key.

        Args:
            team_id: Team whose blackboard to modify.
            key: Blackboard key to delete.
        """
        bb_dir = self._blackboard_dir(team_id)
        key_path = self._validate_key(key, bb_dir)
        if key_path.exists():
            key_path.unlink()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self, team_id: str) -> None:
        """Remove the entire team directory.

        Args:
            team_id: Team to remove.
        """
        team_dir = self._team_dir(team_id)
        if team_dir.exists():
            shutil.rmtree(team_dir)

    @classmethod
    def cleanup_expired_teams(cls, base_dir: str, ttl_hours: int) -> int:
        """Remove expired team directories and mark orphaned teams.

        Scans ``{base_dir}/teams/`` for ``state.json`` files. Teams with
        ``status="deleted"`` and ``ended_at`` older than *ttl_hours* are
        removed entirely. Teams with ``status="active"`` whose
        ``created_at`` + *ttl_hours* < now and ``ended_at`` is ``None``
        are marked as ``status="orphaned"`` (best-effort write).

        Args:
            base_dir: Root directory containing ``teams/``.
            ttl_hours: Minimum age (in hours) before cleanup or orphaning.

        Returns:
            Number of team directories removed.
        """
        teams_root = Path(base_dir) / "teams"
        if not teams_root.exists():
            return 0
        now = datetime.datetime.now(datetime.UTC)
        removed = 0
        for entry in teams_root.iterdir():
            if not entry.is_dir():
                continue
            state_path = entry / "state.json"
            if not state_path.exists():
                continue
            state = cls._read_json(state_path)
            status: str = state.get("status", "")

            if status == "deleted":
                ended_at_raw: str | None = state.get("ended_at")
                if ended_at_raw is None:
                    continue
                try:
                    ended_at = datetime.datetime.fromisoformat(ended_at_raw)
                except ValueError:
                    continue
                if ended_at.tzinfo is None:
                    ended_at = ended_at.replace(tzinfo=datetime.UTC)
                age_hours = (now - ended_at).total_seconds() / 3600
                if age_hours >= ttl_hours:
                    shutil.rmtree(entry)
                    removed += 1

            elif status == "active":
                created_at_raw: str | None = state.get("created_at")
                if created_at_raw is None:
                    continue
                if state.get("ended_at") is not None:
                    continue
                try:
                    created_at = datetime.datetime.fromisoformat(created_at_raw)
                except ValueError:
                    continue
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=datetime.UTC)
                age_hours = (now - created_at).total_seconds() / 3600
                if age_hours >= ttl_hours:
                    state["status"] = "orphaned"
                    with contextlib.suppress(OSError):
                        cls._atomic_write(state_path, state)

        return removed


async def start_team_cleanup_task(
    base_dir: str,
    ttl_hours: int,
    interval_minutes: int = 10,
) -> asyncio.Task[None]:
    """Start a background task that periodically cleans up expired teams.

    Args:
        base_dir: Root directory containing ``teams/``.
        ttl_hours: Minimum age (in hours) before cleanup or orphaning.
        interval_minutes: Seconds between cleanup runs, expressed in minutes.

    Returns:
        The ``asyncio.Task`` for cancellation.
    """
    try:
        import logfire
    except ImportError:
        logfire = None  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    async def _cleanup_loop() -> None:
        if logfire is not None:
            span_ctx = logfire.span(
                "lifecycle.team_cleanup",
                base_dir=base_dir,
                ttl_hours=ttl_hours,
            )
        else:
            from contextlib import nullcontext

            span_ctx = nullcontext()
        with span_ctx:
            while True:
                removed = FileTeamState.cleanup_expired_teams(base_dir, ttl_hours)
                if removed > 0 and logfire is not None:
                    logfire.info(
                        "team_cleanup_removed",
                        removed=removed,
                        base_dir=base_dir,
                    )
                await asyncio.sleep(interval_minutes * 60)

    return asyncio.create_task(_cleanup_loop())
