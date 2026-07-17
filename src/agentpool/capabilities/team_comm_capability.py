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

Per-session instantiation:
    The factory creates a shared instance with ``session_metadata=None``
    during ``_compile_agent_capabilities()``. When a session with a
    ``team_id`` in its metadata is created, ``create_session_agent()``
    replaces the shared instance with a per-session instance carrying
    the actual session metadata.
"""

from __future__ import annotations

import datetime
import json
import tempfile
from typing import TYPE_CHECKING, Any, cast, override
import uuid

from pydantic_ai.tools import (
    RunContext,  # noqa: TC002  # needed at runtime for PydanticAI type resolution
)

from agentpool.capabilities.function_toolset import FunctionToolsetCapability


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.capabilities.agent_context import AgentContext
    from agentpool.capabilities.file_team_state import FileTeamState
    from agentpool.tools.base import Tool
    from agentpool_config.team_mode import TeamModeConfig


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
            # Register lead-only tools
            self.register_tool(self.team_create)
            self.register_tool(self.team_delete)
            self.register_tool(self.delete_blackboard)
            self.register_tool(self.shutdown_request)

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
            for member_name in members:
                target_sid = team_state.get_member_session_id(team_id, member_name)
                if target_sid is None:
                    continue
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
            import contextlib

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

        return f"Team '{name}' created with {len(members)} members. team_id={team_id}"

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
        if session_pool is not None:
            for member_info in members.values():
                sid: str = member_info.get("session_id", "")
                if sid:
                    await session_pool.close_session(sid)

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

    # ------------------------------------------------------------------
    # AbstractCapability overrides
    # ------------------------------------------------------------------

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
        base = self._config.protocol_template.format(
            team_name=self._session_metadata.get("team_name", "unknown"),
            role=self._session_metadata.get("team_role", "unknown"),
            member_name=self._session_metadata.get(
                "team_member_name",
                self._agent_name,
            ),
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
