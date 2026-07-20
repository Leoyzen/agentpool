"""TeamCommCapability — capability for dynamic team communication.

This capability provides the protocol instructions and team communication
tools (send_message, task_create, read_blackboard, etc.) to agents that
are members of or leads of a dynamic team.

Universal tools (all members can use):
    - send_message: Send a message to a teammate's inbox.
    - task_create: Create a task on the shared task board.
    - task_list: List all tasks on the shared task board.
    - task_update: Update a task's status or owner.
    - read_blackboard: Read a key from the shared blackboard.
    - write_blackboard: Write a key to the shared blackboard.
    - list_blackboard: List all keys on the shared blackboard.
    - team_status: Get the current status of the team.

Lead-only tools (only agents with ``team_role == "lead"``):
    - team_create: Create a new team with eligible members.
    - team_delete: Delete the current team and close all member sessions.
    - delete_blackboard: Delete a key from the shared blackboard.
    - shutdown_request: Shut down a specific team member.
    - team_add_member: Add a new member to an existing team.
    - team_remove_member: Remove a member from the team.

    Lead-only tools are registered for all agents but filtered out for
    non-lead members by ``prepare_tools()`` before the model receives the
    tool list.  Runtime permission checks in each tool body remain as a
    safety net.

Per-session instantiation:
    The factory creates a shared instance with ``session_metadata=None``
    during ``_compile_agent_capabilities()``. When a session with a
    ``team_id`` in its metadata is created, ``create_session_agent()``
    replaces the shared instance with a per-session instance carrying
    the actual session metadata.

Role-aware tool schema:
    ``prepare_tools()`` modifies tool definitions based on
    ``team_role`` from session metadata:

    - **Non-lead members**: Lead-only tools are removed entirely.
      ``send_message`` has its ``to`` parameter description updated to
      omit the broadcast (``"*"``) mention, and a ``pattern`` constraint
      is added to reject ``"*"`` at the schema level.

    - **Lead agents**: All tool definitions are returned unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import tempfile
from typing import TYPE_CHECKING, Any, cast, override
import uuid

from pydantic_ai.tools import (
    RunContext,  # noqa: TC002  # needed at runtime for PydanticAI type resolution
    ToolDefinition,  # noqa: TC002  # needed at runtime for PydanticAI type resolution
)

from agentpool.capabilities.function_toolset import FunctionToolsetCapability
from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.capabilities.agent_context import AgentContext
    from agentpool.capabilities.file_team_state import FileTeamState
    from agentpool.tools.base import Tool
    from agentpool_config.team_mode import TeamModeConfig


logger = get_logger(__name__)

# Strong references to cleanup tasks so asyncio does not garbage-collect them
# while they are awaiting ``RunHandle.complete_event``.
_cleanup_tasks: set[asyncio.Task[Any]] = set()


class TeamCommCapability(FunctionToolsetCapability[Any]):
    """Capability providing team communication protocol instructions and tools.

    Inherits from :class:`FunctionToolsetCapability` and overrides
    ``get_instructions()`` and ``get_tools()`` to respect the
    :class:`TeamModeConfig` enabled flag and session metadata availability.

    Attributes:
        _config: The resolved team mode configuration.
        _agent_name: Name of the agent this capability is attached to.
        _session_metadata: Per-session metadata (team_name, team_role, etc.).
    """

    # Auto-cleanup tuning (override in tests).
    _idle_timeout: float = 300.0
    _poll_interval: float = 30.0

    def __init__(
        self,
        config: TeamModeConfig,
        agent_name: str,
        session_metadata: dict[str, Any] | None = None,
        agent_descriptions: dict[str, str] | None = None,
    ) -> None:
        """Initialize the team communication capability.

        Args:
            config: The resolved team mode configuration (global + agent overlay).
            agent_name: Name of the agent this capability belongs to.
            session_metadata: Optional per-session metadata containing
                ``team_name``, ``team_role``, ``team_member_name``, etc.
                When ``None`` or empty, ``get_instructions()`` returns ``None``.
            agent_descriptions: Optional mapping of agent name to short
                description for eligible agents. Used in ``get_instructions()``
                so the LLM knows what each agent does.
        """
        super().__init__(name="team_comm")
        self._config = config
        self._agent_name = agent_name
        self._session_metadata: dict[str, Any] = session_metadata or {}
        self._agent_descriptions: dict[str, str] = agent_descriptions or {}
        # Register universal tools (all members can use)
        if config.enabled:
            self.register_tool(self.send_message)
            self.register_tool(self.task_create)
            self.register_tool(self.task_list)
            self.register_tool(self.task_update)
            self.register_tool(self.read_blackboard)
            self.register_tool(self.write_blackboard)
            self.register_tool(self.list_blackboard)
            self.register_tool(self.team_status)
            # Register lead-only tools — filtered out for non-lead members
            # by prepare_tools() before the model receives the tool list.
            self.register_tool(self.team_create)
            self.register_tool(self.team_delete)
            self.register_tool(self.delete_blackboard)
            self.register_tool(self.shutdown_request)
            self.register_tool(self.team_add_member)
            self.register_tool(self.team_remove_member)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_agent_context(self, ctx: RunContext[Any]) -> AgentContext:
        """Extract AgentContext from a pydantic-ai RunContext.

        In production, PydanticAI wraps our AgentContext inside
        ``agents.context.AgentContext.data``. This method unwraps it.

        Args:
            ctx: The RunContext passed to a tool function.

        Returns:
            The AgentContext from ``ctx.deps`` (or ``ctx.deps.data``).

        Raises:
            RuntimeError: If ``ctx.deps`` is None or AgentContext is not found.
        """
        from agentpool.capabilities.agent_context import AgentContext

        deps = ctx.deps
        if deps is None:
            msg = "TeamCommCapability requires AgentContext as deps. Got: None"
            raise RuntimeError(msg)
        # In production, deps is agents.context.AgentContext (PydanticAI runtime
        # context). Our capabilities.agent_context.AgentContext is stored at
        # deps.data, set by NativeTurn (turn.py: agent_deps.data = run_ctx.deps).
        from agentpool.agents.context import AgentContext as RuntimeAgentContext

        if isinstance(deps, RuntimeAgentContext):
            inner = deps.data
            if inner is None:
                msg = "TeamCommCapability requires AgentContext at deps.data. Got: None"
                raise RuntimeError(msg)
            return cast(AgentContext, inner)
        # In tests, deps may be directly our AgentContext or a MagicMock(spec=AgentContext).
        if isinstance(deps, AgentContext):
            return deps
        return cast(AgentContext, deps)

    def _get_team_state(self, agent_ctx: AgentContext) -> FileTeamState | None:
        """Create a FileTeamState for the current team, or None if not in a team.

        Args:
            agent_ctx: The per-turn agent context.

        Returns:
            A FileTeamState rooted at the configured base_dir, or None
            if no ``team_id`` is present in session metadata.
        """
        from agentpool.capabilities.file_team_state import FileTeamState

        team_id: str | None = agent_ctx.session.metadata.get("team_id")
        if team_id is None:
            return None
        base_dir = (
            agent_ctx.team_mode_config.effective_base_dir
            if agent_ctx.team_mode_config is not None
            else tempfile.gettempdir()
        )
        return FileTeamState(base_dir)

    def _get_team_id(self, agent_ctx: AgentContext) -> str | None:
        """Return the team_id from session metadata, or None."""
        team_id: str | None = agent_ctx.session.metadata.get("team_id")
        return team_id

    # ------------------------------------------------------------------
    # Universal tools
    # ------------------------------------------------------------------

    async def send_message(  # noqa: PLR0911, PLR0915
        self,
        ctx: RunContext[Any],
        to: str,
        body: str,
        urgent: bool = False,
        message_type: str = "",
    ) -> str:
        """Send a message to a teammate's inbox.

        Args:
            ctx: RunContext with AgentContext deps.
            to: Recipient member name. ``"*"`` broadcasts to all members
                (lead-only — returns error for non-lead agents).
            body: Message body text.
            urgent: If True, deliver via steer (mid-turn injection);
                otherwise queue for next turn.
            message_type: Optional message type tag. If the type is in
                ``config.auto_urgent``, ``urgent`` is forced to ``True``.

        Returns:
            Success or error message string.
        """
        # Message size enforcement.
        body_bytes = len(body.encode())
        if body_bytes > self._config.message_max_bytes:
            return (
                f"Message exceeds max size ({body_bytes} > {self._config.message_max_bytes} bytes)"
            )

        # Auto-urgent: force urgent=True for configured message types.
        if message_type and message_type in self._config.auto_urgent:
            urgent = True

        # Broadcast: lead-only.
        if to == "*":
            agent_ctx = self._resolve_agent_context(ctx)
            role: str = agent_ctx.session.metadata.get("team_role", "")
            if role != "lead":
                return "Broadcast is lead-only"

            team_state = self._get_team_state(agent_ctx)
            if team_state is None:
                return "Not in a team session"

            team_id: str = agent_ctx.session.metadata["team_id"]
            session_pool = agent_ctx.host.session_pool
            if session_pool is None:
                return "SessionPool not available"

            from agentpool.capabilities.file_team_state import FileTeamState

            state_path = team_state._state_path(team_id)
            if not state_path.exists():
                return "Team state not found"
            state: dict[str, Any] = FileTeamState._read_json(state_path)
            members: dict[str, dict[str, str]] = state.get("members", {})

            from agentpool.lifecycle.types import DeliveryMode

            mode = DeliveryMode.STEER if urgent else DeliveryMode.QUEUE
            delivered = 0
            lead_sid = agent_ctx.session.session_id
            for member_name in members:
                target_sid = team_state.get_member_session_id(team_id, member_name)
                if target_sid is None or target_sid == lead_sid:
                    continue  # Skip self (lead broadcasting to itself).
                result = await session_pool.send_message(target_sid, body, mode=mode)
                if result is not None:
                    delivered += 1
                team_state.write_message(
                    team_id,
                    member_name,
                    {"from": self._agent_name, "body": body, "urgent": urgent},
                )
            return f"Broadcast sent to {delivered} members"

        agent_ctx = self._resolve_agent_context(ctx)
        team_state = self._get_team_state(agent_ctx)
        if team_state is None:
            return "Not in a team session"

        team_id = agent_ctx.session.metadata["team_id"]
        session_pool = agent_ctx.host.session_pool
        if session_pool is None:
            return "SessionPool not available"

        # Bounds: max_member_turns and inbox_max_bytes checks.
        from agentpool.capabilities.file_team_state import FileTeamState

        state_path = team_state._state_path(team_id)
        if state_path.exists():
            current_state: dict[str, Any] = FileTeamState._read_json(state_path)
            members_state: dict[str, dict[str, Any]] = current_state.get("members", {})
            member_info: dict[str, Any] = members_state.get(to, {})
            turn_count: int = member_info.get("turn_count", 0)
            if turn_count >= self._config.bounds.max_member_turns:
                return (
                    f"Member '{to}' has exceeded max turns "
                    f"({turn_count} >= {self._config.bounds.max_member_turns})"
                )

            existing_messages = team_state.read_messages(team_id, to)
            inbox_size = sum(len(json.dumps(m).encode()) for m in existing_messages)
            body_bytes_len = len(body.encode())
            if inbox_size + body_bytes_len > self._config.inbox_max_bytes:
                return (
                    f"Inbox exceeds max size ({inbox_size + body_bytes_len} > "
                    f"{self._config.inbox_max_bytes} bytes)"
                )

            member_info["turn_count"] = turn_count + 1
            members_state[to] = member_info
            current_state["members"] = members_state
            FileTeamState._atomic_write(state_path, current_state)

        target_sid = team_state.get_member_session_id(team_id, to)
        if target_sid is None:
            return f"Member '{to}' not found or no session registered"

        from agentpool.lifecycle.types import DeliveryMode

        mode = DeliveryMode.STEER if urgent else DeliveryMode.QUEUE
        result = await session_pool.send_message(target_sid, body, mode=mode)
        if result is None:
            return f"Failed to deliver message to '{to}'"

        # Persist to inbox for audit trail.
        team_state.write_message(
            team_id,
            to,
            {"from": self._agent_name, "body": body, "urgent": urgent},
        )
        return f"Message sent to {to}"

    async def task_create(
        self,
        ctx: RunContext[Any],
        subject: str,
        description: str = "",
        blocked_by: list[str] | None = None,
    ) -> str:
        """Create a task on the shared task board.

        Args:
            ctx: RunContext with AgentContext deps.
            subject: Short task title.
            description: Optional longer description.
            blocked_by: Optional list of task_ids this task depends on.

        Returns:
            Success message with task_id, or error string.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        team_id = self._get_team_id(agent_ctx)
        if team_id is None:
            return "Not in a team session"

        team_state = self._get_team_state(agent_ctx)
        if team_state is None:
            return "Not in a team session"

        task_id = team_state.create_task(
            team_id,
            {
                "subject": subject,
                "description": description,
                "blocked_by": blocked_by or [],
            },
        )
        return f"Task created: {task_id}"

    async def task_list(self, ctx: RunContext[Any]) -> str:
        """List all tasks on the shared task board.

        Args:
            ctx: RunContext with AgentContext deps.

        Returns:
            JSON array of tasks (pretty-printed), or error string.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        team_id = self._get_team_id(agent_ctx)
        if team_id is None:
            return "Not in a team session"

        team_state = self._get_team_state(agent_ctx)
        if team_state is None:
            return "Not in a team session"

        tasks = team_state.list_tasks(team_id)
        return json.dumps(tasks, indent=2, default=str)

    async def task_update(
        self,
        ctx: RunContext[Any],
        task_id: str,
        status: str = "",
        owner: str = "",
    ) -> str:
        """Update a task's status or owner on the shared task board.

        Args:
            ctx: RunContext with AgentContext deps.
            task_id: ID of the task to update.
            status: New status (e.g. "in_progress", "completed"). Empty = no change.
            owner: New owner name. Empty = no change.

        Returns:
            Updated task as JSON, or error string.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        team_id = self._get_team_id(agent_ctx)
        if team_id is None:
            return "Not in a team session"

        team_state = self._get_team_state(agent_ctx)
        if team_state is None:
            return "Not in a team session"

        updates: dict[str, Any] = {}
        if status:
            updates["status"] = status
        if owner:
            updates["owner"] = owner
        if not updates:
            return "No updates specified"

        updated = team_state.update_task(team_id, task_id, updates)
        return json.dumps(updated, indent=2, default=str)

    async def read_blackboard(self, ctx: RunContext[Any], key: str) -> str:
        """Read a key from the shared blackboard.

        Args:
            ctx: RunContext with AgentContext deps.
            key: Blackboard key to read.

        Returns:
            JSON value + metadata, or "Key not found" / error string.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        team_id = self._get_team_id(agent_ctx)
        if team_id is None:
            return "Not in a team session"

        team_state = self._get_team_state(agent_ctx)
        if team_state is None:
            return "Not in a team session"

        result = team_state.read_blackboard(team_id, key)
        if result is None:
            return "Key not found"
        return json.dumps(result, indent=2, default=str)

    async def write_blackboard(
        self,
        ctx: RunContext[Any],
        key: str,
        value: str,
        expected_version: int | None = None,
    ) -> str:
        """Write a key to the shared blackboard with optimistic locking.

        Args:
            ctx: RunContext with AgentContext deps.
            key: Blackboard key to write.
            value: Value to store.
            expected_version: Expected current version for optimistic locking.
                If None, no version check is performed.

        Returns:
            "Written, version=N" on success, or "Conflict: current version is N".
        """
        agent_ctx = self._resolve_agent_context(ctx)
        team_id = self._get_team_id(agent_ctx)
        if team_id is None:
            return "Not in a team session"

        team_state = self._get_team_state(agent_ctx)
        if team_state is None:
            return "Not in a team session"

        result = team_state.write_blackboard(
            team_id,
            key,
            {"text": value},
            expected_version=expected_version,
            written_by=self._agent_name,
        )

        # Bounds: max_size_mb check on the resulting blackboard file.
        if result.startswith("Written"):
            key_path = team_state._validate_key(key, team_state._blackboard_dir(team_id))
            file_size = key_path.stat().st_size
            max_size = self._config.blackboard.max_size_mb * 1024 * 1024
            if file_size > max_size:
                return (
                    f"Blackboard write exceeds max size "
                    f"({file_size / 1024 / 1024:.1f}MB > "
                    f"{self._config.blackboard.max_size_mb}MB)"
                )

        return result

    async def list_blackboard(self, ctx: RunContext[Any]) -> str:
        """List all keys on the shared blackboard.

        Args:
            ctx: RunContext with AgentContext deps.

        Returns:
            JSON array of key names, or error string.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        team_id = self._get_team_id(agent_ctx)
        if team_id is None:
            return "Not in a team session"

        team_state = self._get_team_state(agent_ctx)
        if team_state is None:
            return "Not in a team session"

        keys = team_state.list_blackboard(team_id)
        return json.dumps(keys, indent=2)

    async def team_status(self, ctx: RunContext[Any]) -> str:
        """Get the current status of the team.

        Args:
            ctx: RunContext with AgentContext deps.

        Returns:
            Formatted status string with team name, members, and status.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        team_id = self._get_team_id(agent_ctx)
        if team_id is None:
            return "Not in a team session"

        team_state = self._get_team_state(agent_ctx)
        if team_state is None:
            return "Not in a team session"

        from agentpool.capabilities.file_team_state import FileTeamState

        state_path = team_state._state_path(team_id)
        if not state_path.exists():
            return "Team state not found"

        state: dict[str, Any] = FileTeamState._read_json(state_path)
        team_name: str = state.get("team_name", "unknown")
        status: str = state.get("status", "unknown")
        members: dict[str, dict[str, str]] = state.get("members", {})

        member_lines: list[str] = []
        for name, info in members.items():
            sid: str = info.get("session_id", "")
            agent_name: str = info.get("agent", name)
            session_display = sid if sid else "unregistered"
            member_lines.append(f"  - {name} (agent={agent_name}, session={session_display})")

        lines = [
            f"Team: {team_name}",
            f"Status: {status}",
            f"Members ({len(members)}):",
            *member_lines,
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Lead-only tools
    # ------------------------------------------------------------------

    async def team_create(  # noqa: PLR0911
        self,
        ctx: RunContext[Any],
        name: str,
        members: list[dict[str, str]],
    ) -> str:
        """Create a new team with eligible members (lead-only).

        Pass ``members`` as a list of dicts with ``agent`` (registered agent
        name) and ``name`` (display name) keys. Only agents listed in
        ``member_eligible`` can be used. If ``defaults`` is configured and
        ``members`` is empty, default members from config are used.

        Args:
            ctx: RunContext with AgentContext deps.
            name: Human-readable team name.
            members: List of member dicts, each with ``agent`` and ``name``
                keys.

        Returns:
            Success message with team_id, or error string.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        role: str = agent_ctx.session.metadata.get("team_role", "")
        if role != "lead":
            return "Only lead can use team_create"

        # Config defaults: when LLM passes empty members, use defaults config.
        if not members and self._config.defaults is not None:
            members = [{"name": m.name, "agent": m.agent} for m in self._config.defaults.members]

        # Eligibility checks.
        for member in members:
            agent_name: str = member.get("agent", "")
            if not agent_ctx.agent_registry.exists(agent_name):
                return f"Agent '{agent_name}' not found in registry"
            if agent_name not in self._config.member_eligible:
                return f"Agent '{agent_name}' is not eligible for team membership"

        # Bounds: max_members check.
        if len(members) > self._config.bounds.max_members:
            return f"Team exceeds max_members ({len(members)} > {self._config.bounds.max_members})"

        # Generate team_id and create state.
        team_id = str(uuid.uuid4())
        lead_session_id: str = agent_ctx.session.session_id

        from agentpool.capabilities.file_team_state import FileTeamState

        base_dir = (
            agent_ctx.team_mode_config.effective_base_dir
            if agent_ctx.team_mode_config is not None
            else tempfile.gettempdir()
        )
        team_state = FileTeamState(base_dir)
        team_state.init(
            team_id,
            name,
            [{"name": m["name"], "agent": m["agent"]} for m in members],
        )

        # Register the lead as a member so other members can send_message
        # to the lead by name.  The lead's member name comes from session
        # metadata (set by the factory), falling back to the agent name.
        lead_member_name = agent_ctx.session.metadata.get(
            "team_member_name",
            self._agent_name,
        )
        team_state.register_member(team_id, lead_member_name, lead_session_id)

        # Record started_at timestamp for wall-clock enforcement.
        state = team_state._read_json(team_state._state_path(team_id))
        state["started_at"] = datetime.datetime.now(datetime.UTC).isoformat()
        team_state._atomic_write(team_state._state_path(team_id), state)

        session_pool = agent_ctx.host.session_pool
        if session_pool is None:
            return "SessionPool not available"

        from agentpool.lifecycle.types import DeliveryMode

        created_sessions: list[str] = []
        try:
            for member in members:
                member_session_id = await agent_ctx.delegation.create_child_session(
                    member["agent"],
                    parent_session_id=lead_session_id,
                    team_id=team_id,
                    team_role="member",
                    team_member_name=member["name"],
                    description=f"Team member: {member['name']}",
                )
                created_sessions.append(member_session_id)
                team_state.register_member(
                    team_id,
                    member["name"],
                    member_session_id,
                )
                await session_pool.send_message(
                    member_session_id,
                    self._config.protocol_template.format(
                        team_name=name,
                        role="member",
                        member_name=member["name"],
                    ),
                    mode=DeliveryMode.QUEUE,
                )
        except Exception as exc:  # noqa: BLE001
            for sid in created_sessions:
                with contextlib.suppress(Exception):
                    await session_pool.close_session(sid)
            with contextlib.suppress(Exception):
                team_state.cleanup(team_id)
            return f"Failed to create team: {exc}"

        # Write team_id back to session metadata so subsequent tool calls
        # can access the team state without requiring a new session.
        agent_ctx.session.metadata["team_id"] = team_id
        agent_ctx.session.metadata["team_name"] = name
        # Store member session IDs so the auto-cleanup callback (and
        # team_delete) can close them without re-reading team state.
        agent_ctx.session.metadata["team_member_sessions"] = list(created_sessions)

        # Schedule auto-cleanup: when the lead's RunHandle terminates
        # (complete_event fires), close all member sessions to prevent
        # leaks.  This covers the scenario where the lead's run finishes
        # but ``close_session(lead)`` is not called (e.g. protocol server
        # keeps the lead session alive for follow-ups).
        self._schedule_member_cleanup(agent_ctx, lead_session_id, list(created_sessions))

        team_dir = team_state._team_dir(team_id)
        logger.info(
            "Team created — state at %s",
            str(team_dir),
            team_id=team_id,
            team_name=name,
            member_count=len(members),
        )

        return f"Team '{name}' created with {len(members)} members. team_id={team_id}"

    @staticmethod
    def _schedule_member_cleanup(
        agent_ctx: AgentContext,
        lead_session_id: str,
        member_session_ids: list[str],
    ) -> None:
        """Schedule a background task to close member sessions when the lead goes idle.

        Polls ``session.last_active_at`` every 30 seconds.  When the lead
        has been inactive for longer than ``idle_timeout`` (default 300s),
        closes every member session whose ID is still recorded in
        ``session.metadata["team_member_sessions"]`` (``team_delete``
        clears this list to signal manual cleanup was already performed).

        This approach correctly handles protocol-server sessions (e.g.
        OpenCode) where the lead's RunLoop stays alive between turns —
        the cleanup fires based on wall-clock inactivity, not on
        ``complete_event`` which may never fire.

        Args:
            agent_ctx: The lead agent's per-turn context.
            lead_session_id: The lead session ID.
            member_session_ids: Member session IDs to close on idle.
        """
        session_pool = agent_ctx.host.session_pool
        if session_pool is None:
            return

        import time

        # Configurable via class attributes so tests can override.
        idle_timeout = TeamCommCapability._idle_timeout
        poll_interval = TeamCommCapability._poll_interval

        async def _cleanup_when_idle() -> None:
            """Poll lead activity; close members after idle timeout."""
            try:
                while True:
                    await asyncio.sleep(poll_interval)
                    # Check if team_delete already cleaned up.
                    current_session = session_pool.sessions.get_session(
                        lead_session_id,
                    )
                    if current_session is None:
                        return  # Lead session closed — cascade handled it.
                    remaining = current_session.metadata.get(
                        "team_member_sessions",
                    )
                    if not remaining:
                        return  # team_delete already closed members.

                    # Check idle time via last_active_at.
                    now = time.monotonic()
                    idle_seconds = now - current_session.last_active_at
                    if idle_seconds < idle_timeout:
                        continue  # Lead still active, keep waiting.

                    # Lead has been idle beyond threshold — close members.
                    logger.info(
                        "Lead idle for %.0fs, auto-closing member sessions",
                        idle_seconds,
                        lead_session_id=lead_session_id,
                    )
                    for msid in member_session_ids:
                        try:
                            await session_pool.close_session(msid)
                        except Exception:
                            logger.exception(
                                "Failed to auto-close member session",
                                member_session_id=msid,
                                lead_session_id=lead_session_id,
                            )
                    # Clear list so cascade close is a no-op.
                    current_session.metadata["team_member_sessions"] = []
                    return  # Cleanup done, exit loop.
            except asyncio.CancelledError:
                pass  # Pool shutdown — exit gracefully.

        task = asyncio.create_task(_cleanup_when_idle())
        _cleanup_tasks.add(task)

        def _on_done(t: asyncio.Task[Any]) -> None:
            _cleanup_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.error(
                    "Member session cleanup task failed: %s",
                    t.exception(),
                    lead_session_id=lead_session_id,
                )

        task.add_done_callback(_on_done)

    async def team_delete(self, ctx: RunContext[Any]) -> str:
        """Delete the current team and close all member sessions (lead-only).

        Args:
            ctx: RunContext with AgentContext deps.

        Returns:
            ``"Team deleted"`` on success, or error string.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        role: str = agent_ctx.session.metadata.get("team_role", "")
        if role != "lead":
            return "Only lead can use team_delete"

        team_id = self._get_team_id(agent_ctx)
        if team_id is None:
            return "Not in a team session"

        team_state = self._get_team_state(agent_ctx)
        if team_state is None:
            return "Not in a team session"

        from agentpool.capabilities.file_team_state import FileTeamState

        state_path = team_state._state_path(team_id)
        if not state_path.exists():
            return "Team state not found"
        state: dict[str, Any] = FileTeamState._read_json(state_path)
        members: dict[str, dict[str, str]] = state.get("members", {})

        session_pool = agent_ctx.host.session_pool
        lead_session_id = agent_ctx.session.session_id
        if session_pool is not None:
            for member_info in members.values():
                sid: str = member_info.get("session_id", "")
                if sid and sid != lead_session_id:
                    await session_pool.close_session(sid)

        # Clear member session IDs from metadata so the auto-cleanup
        # callback (scheduled in team_create) knows manual cleanup was
        # already performed and skips double-closing.
        agent_ctx.session.metadata["team_member_sessions"] = []

        team_state.cleanup(team_id)
        return "Team deleted"

    async def delete_blackboard(self, ctx: RunContext[Any], key: str) -> str:
        """Delete a key from the shared blackboard (lead-only).

        Args:
            ctx: RunContext with AgentContext deps.
            key: Blackboard key to delete.

        Returns:
            ``"Blackboard key '{key}' deleted"`` on success, or error string.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        role: str = agent_ctx.session.metadata.get("team_role", "")
        if role != "lead":
            return "Only lead can use delete_blackboard"

        team_id = self._get_team_id(agent_ctx)
        if team_id is None:
            return "Not in a team session"

        team_state = self._get_team_state(agent_ctx)
        if team_state is None:
            return "Not in a team session"

        team_state.delete_blackboard(team_id, key)
        return f"Blackboard key '{key}' deleted"

    async def shutdown_request(self, ctx: RunContext[Any], member_name: str) -> str:
        """Shut down a specific team member (lead-only).

        Args:
            ctx: RunContext with AgentContext deps.
            member_name: Name of the member to shut down.

        Returns:
            ``"Shutdown completed for {member_name}"`` on success, or error.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        role: str = agent_ctx.session.metadata.get("team_role", "")
        if role != "lead":
            return "Only lead can use shutdown_request"

        team_id = self._get_team_id(agent_ctx)
        if team_id is None:
            return "Not in a team session"

        team_state = self._get_team_state(agent_ctx)
        if team_state is None:
            return "Not in a team session"

        member_sid = team_state.get_member_session_id(team_id, member_name)
        if member_sid is None:
            return f"Member '{member_name}' not found"

        session_pool = agent_ctx.host.session_pool
        if session_pool is None:
            return "SessionPool not available"

        await session_pool.close_session(member_sid)
        return f"Shutdown completed for {member_name}"

    async def team_add_member(  # noqa: PLR0911
        self,
        ctx: RunContext[Any],
        name: str,
        agent: str,
        prompt: str = "",
        lifecycle: str = "persistent",
        notify: str | None = None,
    ) -> str:
        """Add a new member to an existing team (lead-only).

        Args:
            ctx: RunContext with AgentContext deps.
            name: Display name for the new member.
            agent: Registered agent name to use as the member.
            prompt: Optional initial prompt to send the member. If empty,
                the protocol template is used.
            lifecycle: ``"persistent"`` (default) or ``"ephemeral"``.
                Ephemeral members are auto-closed when their run completes.
            notify: Optional message to broadcast to existing members
                (excluding lead and the new member).

        Returns:
            Success message or error string.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        role: str = agent_ctx.session.metadata.get("team_role", "")
        if role != "lead":
            return "Only lead can use team_add_member"

        team_id = self._get_team_id(agent_ctx)
        if team_id is None:
            return "Not in a team session"

        # Check agent exists in registry.
        if not agent_ctx.agent_registry.exists(agent):
            return f"Agent '{agent}' not found in registry"

        # Check agent is eligible.
        if agent not in self._config.member_eligible:
            return f"Agent '{agent}' is not eligible"

        team_state = self._get_team_state(agent_ctx)
        if team_state is None:
            return "Not in a team session"

        from agentpool.capabilities.file_team_state import FileTeamState

        # Check name not already in team state members.
        state_path = team_state._state_path(team_id)
        if not state_path.exists():
            return "Team state not found"
        state: dict[str, Any] = FileTeamState._read_json(state_path)
        members: dict[str, dict[str, Any]] = state.get("members", {})
        if name in members:
            return f"Member '{name}' already exists"

        # Bounds: max_members check (exclude lead from count).
        lead_member_name = agent_ctx.session.metadata.get(
            "team_member_name",
            self._agent_name,
        )
        non_lead_count = sum(1 for mname in members if mname != lead_member_name)
        if non_lead_count >= self._config.bounds.max_members:
            return (
                f"Team exceeds max_members "
                f"({non_lead_count + 1} > {self._config.bounds.max_members})"
            )

        session_pool = agent_ctx.host.session_pool
        if session_pool is None:
            return "SessionPool not available"

        lead_session_id: str = agent_ctx.session.session_id

        # Create child session for the new member.
        try:
            member_session_id = await agent_ctx.delegation.create_child_session(
                agent,
                parent_session_id=lead_session_id,
                team_id=team_id,
                team_role="member",
                team_member_name=name,
                description=f"Team member: {name}",
            )
        except Exception as exc:  # noqa: BLE001
            return f"Failed to create member session: {exc}"

        # Register member in team state.
        team_state.register_member(team_id, name, member_session_id)

        # Send initial prompt to member.
        from agentpool.lifecycle.types import DeliveryMode

        team_name: str = state.get("team_name", "unknown")
        initial_prompt = prompt or self._config.protocol_template.format(
            team_name=team_name,
            role="member",
            member_name=name,
        )
        await session_pool.send_message(
            member_session_id,
            initial_prompt,
            mode=DeliveryMode.QUEUE,
        )

        # Ephemeral lifecycle: schedule auto-close when run completes.
        if lifecycle == "ephemeral":
            base_dir = (
                agent_ctx.team_mode_config.effective_base_dir
                if agent_ctx.team_mode_config is not None
                else tempfile.gettempdir()
            )
            self._schedule_ephemeral_cleanup(
                session_pool,
                member_session_id,
                team_id,
                name,
                base_dir,
            )

        # Notify existing members (excluding lead and the new member).
        if notify is not None and notify:
            # Re-read state to get the updated members dict.
            updated_state: dict[str, Any] = FileTeamState._read_json(
                team_state._state_path(team_id),
            )
            updated_members: dict[str, dict[str, Any]] = updated_state.get(
                "members",
                {},
            )
            for existing_name, existing_info in updated_members.items():
                if existing_name in (lead_member_name, name):
                    continue
                existing_sid: str = existing_info.get("session_id", "")
                if not existing_sid:
                    continue
                await session_pool.send_message(
                    existing_sid,
                    notify,
                    mode=DeliveryMode.QUEUE,
                )

        # Write to blackboard.
        team_state.write_blackboard(
            team_id,
            f"member_update/{name}",
            {"action": "added", "agent": agent, "lifecycle": lifecycle},
            written_by=self._agent_name,
        )

        # Append member_session_id to session metadata.
        team_member_sessions: list[str] = agent_ctx.session.metadata.get(
            "team_member_sessions",
            [],
        )
        team_member_sessions.append(member_session_id)
        agent_ctx.session.metadata["team_member_sessions"] = team_member_sessions

        return f"Member '{name}' added to team (lifecycle={lifecycle})"

    async def team_remove_member(
        self,
        ctx: RunContext[Any],
        member_name: str,
    ) -> str:
        """Remove a member from the team (lead-only).

        Args:
            ctx: RunContext with AgentContext deps.
            member_name: Name of the member to remove.

        Returns:
            Success message or error string.
        """
        agent_ctx = self._resolve_agent_context(ctx)
        role: str = agent_ctx.session.metadata.get("team_role", "")
        if role != "lead":
            return "Only lead can use team_remove_member"

        team_id = self._get_team_id(agent_ctx)
        if team_id is None:
            return "Not in a team session"

        # Cannot remove yourself.
        lead_member_name = agent_ctx.session.metadata.get(
            "team_member_name",
            self._agent_name,
        )
        if member_name == lead_member_name:
            return "Cannot remove yourself"

        team_state = self._get_team_state(agent_ctx)
        if team_state is None:
            return "Not in a team session"

        from agentpool.capabilities.file_team_state import FileTeamState

        member_sid = team_state.get_member_session_id(team_id, member_name)
        if member_sid is None:
            return f"Member '{member_name}' not found"

        session_pool = agent_ctx.host.session_pool
        if session_pool is not None:
            await session_pool.close_session(member_sid)

        # Remove from team state: read, delete member, write back.
        state_path = team_state._state_path(team_id)
        if state_path.exists():
            state: dict[str, Any] = FileTeamState._read_json(state_path)
            members_dict: dict[str, dict[str, Any]] = state.get("members", {})
            members_dict.pop(member_name, None)
            state["members"] = members_dict
            FileTeamState._atomic_write(state_path, state)

        # Remove from session metadata team_member_sessions.
        team_member_sessions: list[str] = agent_ctx.session.metadata.get(
            "team_member_sessions",
            [],
        )
        if member_sid in team_member_sessions:
            team_member_sessions.remove(member_sid)
            agent_ctx.session.metadata["team_member_sessions"] = team_member_sessions

        # Write to blackboard.
        team_state.write_blackboard(
            team_id,
            f"member_update/{member_name}",
            {"action": "removed"},
            written_by=self._agent_name,
        )

        return f"Member '{member_name}' removed from team"

    @staticmethod
    def _schedule_ephemeral_cleanup(
        session_pool: Any,
        member_session_id: str,
        team_id: str,
        member_name: str,
        base_dir: str,
    ) -> None:
        """Poll member run state; close session when run completes.

        Args:
            session_pool: The SessionPool managing the member session.
            member_session_id: Session ID of the ephemeral member.
            team_id: Team ID for state cleanup.
            member_name: Member name for state cleanup.
            base_dir: Base directory for FileTeamState.
        """
        from agentpool.capabilities.file_team_state import FileTeamState

        async def _poll_and_close() -> None:
            try:
                while True:
                    await asyncio.sleep(5.0)
                    session = session_pool.sessions.get_session(member_session_id)
                    if session is None:
                        return  # Already closed
                    if session.current_run_id is None:
                        # Run completed — close and remove from team state.
                        await session_pool.close_session(member_session_id)
                        team_state = FileTeamState(base_dir)
                        state_path = team_state._state_path(team_id)
                        if state_path.exists():
                            state = team_state._read_json(state_path)
                            members = state.get("members", {})
                            members.pop(member_name, None)
                            state["members"] = members
                            team_state._atomic_write(state_path, state)
                        return
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(_poll_and_close())
        _cleanup_tasks.add(task)

        def _on_done(t: asyncio.Task[Any]) -> None:
            _cleanup_tasks.discard(t)

        task.add_done_callback(_on_done)

    # ------------------------------------------------------------------
    # AbstractCapability overrides
    # ------------------------------------------------------------------

    # Tool names that only lead agents may use.  Non-lead members never
    # see these tools — ``prepare_tools`` filters them out before the
    # model receives the tool list, so the LLM cannot attempt to call
    # them.  The runtime permission checks in each tool body remain as a
    # safety net.
    _LEAD_ONLY_TOOLS: frozenset[str] = frozenset(
        {
            "team_create",
            "team_delete",
            "delete_blackboard",
            "shutdown_request",
            "team_add_member",
            "team_remove_member",
        },
    )

    @override
    async def prepare_tools(
        self,
        ctx: RunContext[Any],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        """Filter and modify tool definitions based on the agent's team role.

        For non-lead members:
            - Lead-only tools (``team_create``, ``team_delete``,
              ``delete_blackboard``, ``shutdown_request``,
              ``team_add_member``, ``team_remove_member``) are removed
              entirely so the LLM never sees them.
            - ``send_message`` has its ``to`` parameter description
              updated to remove the broadcast (``"*"``) mention, and a
              ``pattern`` constraint is added to reject ``"*"`` at the
              schema level.

        For lead agents, all tool definitions are returned unchanged.

        Args:
            ctx: The PydanticAI run context (unused — role is read from
                ``self._session_metadata``).
            tool_defs: The full list of tool definitions for this step.

        Returns:
            Filtered/modified tool definitions.
        """
        # No session metadata = compile-time shared instance; no role
        # filtering to apply.
        if not self._session_metadata:
            return tool_defs

        role: str = self._session_metadata.get("team_role", "")
        if role == "lead":
            return tool_defs

        result: list[ToolDefinition] = []
        for td in tool_defs:
            if td.name in self._LEAD_ONLY_TOOLS:
                continue
            if td.name == "send_message":
                self._strip_broadcast_from_send_message(td)
            result.append(td)
        return result

    @staticmethod
    def _strip_broadcast_from_send_message(tool_def: ToolDefinition) -> None:
        """Remove broadcast (``to="*"``) from the send_message tool schema.

        Mutates ``tool_def`` in place:
            - Updates the ``to`` parameter description to omit the
              broadcast mention.
            - Adds a ``pattern`` constraint that rejects ``"*"``.

        Args:
            tool_def: The ``send_message`` ToolDefinition to modify.
        """
        schema = tool_def.parameters_json_schema
        props = schema.get("properties", {})
        to_prop = props.get("to")
        if to_prop is not None and isinstance(to_prop, dict):
            to_prop["description"] = "Recipient member name."
            to_prop["pattern"] = r"^[^*]+$"

    @override
    def get_instructions(self) -> str | None:
        """Render the team protocol template using session metadata.

        Returns ``None`` when:
            - ``config.enabled`` is ``False``, OR
            - ``session_metadata`` is empty/``None``

        When both conditions are met, renders ``config.protocol_template``
        via ``str.format()`` with ``team_name``, ``role``, and ``member_name``
        extracted from session metadata (with sensible defaults).
        """
        if not self._config.enabled or not self._session_metadata:
            return None
        role: str = self._session_metadata.get("team_role", "unknown")
        base = self._config.protocol_template.format(
            team_name=self._session_metadata.get("team_name", "unknown"),
            role=role,
            member_name=self._session_metadata.get(
                "team_member_name",
                self._agent_name,
            ),
        )

        # Role-specific capabilities section.
        if role == "lead":
            base += (
                "\n\n## Your Capabilities (Lead)\n\n"
                "- You can broadcast to all members via `send_message` with "
                '`to="*"`.\n'
                "- You can create and delete teams, delete blackboard keys, "
                "and shut down members.\n"
            )
        else:
            base += (
                "\n\n## Your Capabilities (Member)\n\n"
                "- Use `send_message` to send messages to individual members "
                "by name.\n"
                '- Broadcast (`to="*"`) is not available to you — send '
                "individual messages to each member instead.\n"
            )

        # Append eligible agent names + descriptions so the LLM knows
        # which agents can be used as team members in team_create.
        eligible = self._config.member_eligible
        if eligible:
            base += (
                "\n\n## Eligible Agents\n\n"
                "The following agents can be used as team members in `team_create`:\n"
            )
            for name in eligible:
                desc = self._agent_descriptions.get(name)
                if desc:
                    base += f"- `{name}`: {desc}\n"
                else:
                    base += f"- `{name}`\n"
        return base

    @override
    async def get_tools(self) -> Sequence[Tool[Any]]:
        """Return the list of team communication tools.

        Returns an empty list when ``config.enabled`` is ``False``.
        """
        if not self._config.enabled:
            return []
        return self._tools
