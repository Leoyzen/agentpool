"""Session pool for high-level session management.

Extracted from orchestrator/core.py as part of the thin-wrapper refactor.
Combines session and turn management for protocol handlers.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any, Final
import uuid

import logfire

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    RunErrorEvent,
    SessionResumeEvent,
    StreamCompleteEvent,
)
from agentpool.lifecycle.types import DeliveryMode
from agentpool.log import get_logger
from agentpool.orchestrator.event_bus import EventBus
from agentpool.orchestrator.run import RunHandle
from agentpool.orchestrator.session_controller import (
    CheckpointMismatchError,
    SessionBusyError,
    SessionClosedError,
    SessionController,
    SessionNotFoundError,
    SessionState,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from pydantic_ai.messages import ModelMessage

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.native_agent import Agent
    from agentpool.agents.native_agent.checkpoint import CheckpointData
    from agentpool.delegation import AgentPool
    from agentpool.delegation.base_team import BaseTeam
    from agentpool.messaging import ChatMessage
    from agentpool.messaging.messagenode import MessageNode
    from agentpool.sessions.models import ElicitationResumePayload, PendingDeferredCall, SessionData
    from agentpool.sessions.store import SessionStore
    from agentpool_config.teams import TeamConfig


logger = get_logger(__name__)

DEFAULT_MAX_AUTO_RESUME: Final[int] = 10


def _build_team_from_config(
    name: str,
    team_config: TeamConfig,
    nodes: Sequence[MessageNode[Any, Any]],
) -> BaseTeam[Any, Any]:
    """Construct a ``BaseTeam`` from a ``TeamConfig`` and pre-resolved nodes.

    This replaces the deprecated ``TeamConfig.get_team()`` method, keeping
    team instantiation in the core layer instead of the config layer.
    """
    from agentpool.delegation.base_team import BaseTeam

    member_configs = team_config.get_member_configs()

    return BaseTeam(
        nodes,
        mode=team_config.mode,
        name=name,
        display_name=team_config.display_name,
        shared_prompt=team_config.shared_prompt,
        mcp_servers=team_config.get_mcp_servers(),
        member_prompt_templates=member_configs or None,
        member_timeout=team_config.member_timeout,
    )


class SessionPool:
    """High-level session pool combining session and turn management.

    This is the main interface used by protocol handlers.
    """

    def __init__(
        self,
        pool: AgentPool[Any],
        store: SessionStore | None = None,
        enable_auto_resume: bool = True,
        enable_event_bus: bool = True,
        max_auto_resume: int = DEFAULT_MAX_AUTO_RESUME,
        max_concurrent_runs: int | None = None,
        replay_buffer_size: int = 100,
    ) -> None:
        """Initialize the session pool.

        Args:
            pool: The agent pool to resolve agents from.
            store: Optional session store for persistence.
            enable_auto_resume: Whether to enable auto-resume loop.
            enable_event_bus: Whether to enable cross-turn event routing.
            max_auto_resume: Maximum auto-resume iterations.
            max_concurrent_runs: Maximum number of concurrent runs across all sessions.
            replay_buffer_size: Maximum number of events retained per session for replay.
        """
        self.pool = pool
        self.sessions = SessionController(
            pool,
            store=store,
            cleanup_callback=self.close_session,
            max_concurrent_runs=max_concurrent_runs,
        )
        self._event_bus = EventBus(
            session_controller=self.sessions,
            replay_buffer_size=replay_buffer_size,
        )
        self.sessions._event_bus = self._event_bus
        self._enable_auto_resume = enable_auto_resume
        self._enable_event_bus = enable_event_bus
        self._runs_lock: asyncio.Lock = asyncio.Lock()
        self._resume_locks: dict[str, asyncio.Lock] = {}
        self._resume_locks_lock = asyncio.Lock()
        self._message_cache: dict[str, list[ChatMessage[Any]]] = {}
        self._elicitation_registries: dict[str, Any] = {}

    async def start(self) -> None:
        """Start the session pool and background tasks."""
        await self.sessions.start_cleanup_task()
        logger.info("SessionPool started")

    async def shutdown(self) -> None:
        """Shutdown the session pool and cancel background tasks."""
        await self.sessions.stop_cleanup_task()
        active_sessions = list(self.sessions._sessions.keys())
        for session_id in active_sessions:
            try:
                await self.close_session(session_id)
            except Exception:
                logger.exception(
                    "Failed to close session during shutdown",
                    session_id=session_id,
                )
        logger.info("SessionPool shut down")

    @property
    def event_bus(self) -> EventBus:
        """Get the event bus for cross-turn event routing."""
        return self._event_bus

    async def create_session(
        self,
        session_id: str,
        agent_name: str | None = None,
        parent_session_id: str | None = None,
        lifecycle_policy: str | None = None,
        **metadata: Any,
    ) -> SessionState:
        """Create or get a session.

        Args:
            session_id: Unique identifier for the session.
            agent_name: Name of the agent to associate with the session.
            parent_session_id: Optional parent session ID for hierarchical sessions.
            lifecycle_policy: Optional lifecycle policy override.
            **metadata: Arbitrary metadata to attach to the session.

        Returns:
            The session state.
        """
        if parent_session_id is not None and self.sessions.store is not None:
            parent_data = await self.sessions.store.load(parent_session_id)
            if parent_data is not None:
                metadata.setdefault("project_id", parent_data.project_id)
                metadata.setdefault("cwd", parent_data.cwd)
        state, _was_created = await self.sessions.get_or_create_session(
            session_id, agent_name, parent_session_id, lifecycle_policy, **metadata
        )
        return state

    async def create_team_from_config(
        self,
        team_name: str,
        team_config: TeamConfig,
    ) -> BaseTeam[Any, Any]:
        """Create a team from config using session-level agent resolution.

        For each member in the team config, resolves the agent via
        :meth:`SessionController.get_or_create_session_agent`, then
        constructs a :class:`BaseTeam` with ``mode`` set to the
        configured execution mode.

        Member names are stored on the resulting team nodes; actual
        session agents are created per-execution by
        :meth:`BaseTeam._resolve_scoped_team_nodes`.

        Args:
            team_name: Name for the created team.
            team_config: Team configuration from the manifest.

        Returns:
            A ``BaseTeam`` instance with the configured execution mode.

        Raises:
            ValueError: If a member name is not found in the manifest
                agents or teams section.
        """
        from agentpool_config.context import ConfigContextManager

        member_names = [team_config.get_member_name(m) for m in team_config.members]

        nodes: list[MessageNode[Any, Any]] = []
        for member_name in member_names:
            cfg = self.pool.manifest.agents.get(member_name)
            if cfg is not None:
                # Create a stateless agent without entering its async context.
                # This avoids spawning MCP subprocesses for temporary template
                # agents — actual per-session agents are created later by
                # BaseTeam._resolve_scoped_team_nodes() during execution.
                if cfg.name is None:
                    cfg = cfg.model_copy(update={"name": member_name})
                with ConfigContextManager(self.pool._config_file_path):
                    agent: MessageNode[Any, Any] = cfg.get_agent(pool=self.pool)
                nodes.append(agent)
            elif member_name in self.pool.manifest.teams:
                nested_config = self.pool.manifest.teams[member_name]
                nested_team = await self.create_team_from_config(member_name, nested_config)
                nodes.append(nested_team)
            else:
                msg = f"Team member {member_name!r} not found in manifest agents or teams"
                raise ValueError(msg)

        return _build_team_from_config(team_name, team_config, nodes)

    async def _get_resume_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create per-session lock for resume serialization.

        Args:
            session_id: Session identifier.

        Returns:
            The per-session resume lock.
        """
        async with self._resume_locks_lock:
            lock = self._resume_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._resume_locks[session_id] = lock
            return lock

    @contextlib.asynccontextmanager
    async def _with_resume_lock(
        self, session_id: str, *, allow_active_run: bool = False
    ) -> AsyncIterator[SessionState | None]:
        """Acquire per-session resume lock with state validation.

        Ensures only one resume runs per session at a time and that
        the session is in a resumable state (no active run, persisted
        status is ``"checkpointed"``).

        Args:
            session_id: Session to lock.
            allow_active_run: When True, skip the ``current_run_id`` check.
                Used for in-process elicitation resume where the agent run
                is intentionally still alive.

        Yields:
            The live ``SessionState``, or ``None`` if no live session exists.

        Raises:
            SessionBusyError: If the session has an active run or its
                persisted status is not ``"checkpointed"``.
        """
        resume_lock = await self._get_resume_lock(session_id)
        async with resume_lock:
            session = self.sessions.get_session(session_id)
            if not allow_active_run and session is not None and session.current_run_id is not None:
                raise SessionBusyError(session_id, session.current_run_id)

            if self.sessions.store is not None:
                current_data = await self.sessions.store.load(session_id)
                # When allow_active_run is True (in-process elicitation
                # resume), the persisted status may still be "active"
                # because the elicitation bridge checkpoint saves
                # checkpoint data but doesn't update the session store
                # status. Allow "active" in that case.
                if (
                    current_data is not None
                    and current_data.status != "checkpointed"
                    and (not allow_active_run or current_data.status != "active")
                ):
                    raise SessionBusyError(session_id, current_data.status)

            yield session

    async def _load_checkpoint_data(self, session_id: str) -> CheckpointData:
        """Load checkpoint data for a session.

        Args:
            session_id: Session identifier.

        Returns:
            Checkpoint data.

        Raises:
            SessionNotFoundError: If no checkpoint exists for the session.
        """
        from agentpool.agents.native_agent.checkpoint import CheckpointManager

        storage = self.pool.storage
        if storage is None:
            raise SessionNotFoundError(session_id)

        checkpoint_mgr = CheckpointManager(storage)
        data = await checkpoint_mgr.load_checkpoint(session_id)
        if data is None:
            raise SessionNotFoundError(session_id)
        return data

    async def _reconstruct_native_agent(
        self,
        session_id: str,
        agent_name: str,
    ) -> Agent[Any, Any]:
        """Reconstruct a native agent from config for session resume.

        Args:
            session_id: Session identifier.
            agent_name: Name of the agent configuration to use.

        Returns:
            A reconstructed native agent instance.

        Raises:
            SessionNotFoundError: If the agent config is not found.
        """
        from agentpool.models.agents import NativeAgentConfig
        from agentpool_config.context import ConfigContextManager

        cfg = self.pool.manifest.agents.get(agent_name)
        if cfg is None:
            raise SessionNotFoundError(session_id)

        if not isinstance(cfg, NativeAgentConfig):
            raise SessionNotFoundError(session_id)

        if cfg.name is None:
            cfg = cfg.model_copy(update={"name": agent_name})

        session = self.sessions.get_session(session_id)
        input_provider = session.input_provider if session else None

        with ConfigContextManager(self.pool._config_file_path):
            agent: Agent[Any, Any] = cfg.get_agent(
                input_provider=input_provider,
                pool=self.pool,
            )

        # Add pool-level providers (non-MCP only).
        # MCP tools are handled via McpConfigSnapshot -> get_capabilities() ->
        # MCPToolset, not through agent._all_capabilities.
        if self.pool is not None:
            agent._external_capabilities.append(self.pool.skills_tools_provider)

        await agent.__aenter__()
        return agent

    async def _reconstruct_acp_agent(
        self,
        session_id: str,
        agent_name: str,
    ) -> BaseAgent[Any, Any]:
        """Reconstruct an ACP agent from config for session resume.

        Args:
            session_id: Session identifier.
            agent_name: Name of the agent configuration to use.

        Returns:
            A reconstructed ACP agent with reopened subprocess.

        Raises:
            SessionNotFoundError: If the agent config is not found.
        """
        from agentpool_config.context import ConfigContextManager

        cfg = self.pool.manifest.agents.get(agent_name)
        if cfg is None:
            raise SessionNotFoundError(session_id)

        if cfg.name is None:
            cfg = cfg.model_copy(update={"name": agent_name})

        session = self.sessions.get_session(session_id)
        input_provider = session.input_provider if session else None

        with ConfigContextManager(self.pool._config_file_path):
            agent: BaseAgent[Any, Any] = cfg.get_agent(
                input_provider=input_provider,
                pool=self.pool,
            )

        # Add pool-level providers (non-MCP only).
        # MCP tools are handled via McpConfigSnapshot -> get_capabilities() ->
        # MCPToolset, not through agent._all_capabilities.
        if self.pool is not None:
            agent._external_capabilities.append(self.pool.skills_tools_provider)

        await agent.__aenter__()
        return agent

    async def _resume_native_agent(  # noqa: PLR0915
        self,
        session_data: SessionData,
        checkpoint: CheckpointData,
        results: Any,
        elicitation_payloads: list[ElicitationResumePayload] | None = None,
    ) -> None:
        """Resume a native agent from checkpoint with deferred results.

        Routes resume through the SessionPool's normal turn management
        (``run_stream()`` → ``_run_stream_run_turn()`` →
        ``_create_run_handle()``) so the resumed turn has full RunHandle
        lifecycle support (journal, snapshot, event delivery, session
        coordination).

        The checkpoint's ``message_history`` is passed as a
        ``list[ModelMessage]`` (NOT wrapped in ``MessageHistory``) to
        initialize ``RunHandle._message_history``.

        Elicitation responses are delivered via TWO mechanisms:

        1. **DeferredToolResults** (primary): ``ToolReturnPart`` entries
           built from ``elicitation_payloads``, keyed by ``tool_call_id``.
           pydantic-ai matches these against the ``ModelResponse`` in
           ``message_history`` and uses the results directly, skipping
           tool execution. This is necessary because ``agentlet.iter()``
           starts from ``UserPromptNode`` and generates a NEW
           ``ModelResponse`` — it does NOT replay the old one.

        2. **cached_elicitation_responses** (fallback): Set on
           ``AgentRunContext`` for cases where the tool does re-execute
           and calls ``handle_elicitation()``.

        Args:
            session_data: Persisted session data.
            checkpoint: Checkpoint data with message_history and pending_calls.
            results: DeferredToolResults for resolving pending deferred calls.
            elicitation_payloads: Optional elicitation responses for crash
                recovery resume of elicitation deferred calls.

        Raises:
            SessionNotFoundError: If agent config is not found.
            RuntimeError: If agent.run() fails (pending_calls remain uncleared).
        """
        agent = await self._reconstruct_native_agent(
            session_data.session_id, session_data.agent_name
        )

        # Detect agent config drift between checkpoint and resume.
        # The hash check is advisory: if we can't compute the current hash
        # (e.g. agent has no tools attribute, or tools is a mock in tests),
        # we skip the comparison and proceed with resume.
        if session_data.agent_config_hash:
            try:
                from agentpool.agents.native_agent.checkpoint import (
                    compute_agent_config_hash,
                )

                agent_tools = await agent._get_all_tools()
                current_hash = compute_agent_config_hash(agent_tools)
                if current_hash != session_data.agent_config_hash:
                    logger.warning(
                        "Agent config hash mismatch — tools may have changed since checkpoint",
                        session_id=session_data.session_id,
                        stored_hash=session_data.agent_config_hash,
                        current_hash=current_hash,
                    )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "Could not compute agent config hash for drift check",
                    session_id=session_data.session_id,
                    exc_info=True,
                )

        # Build cached elicitation responses for crash recovery (fallback).
        cached_elicitation: dict[str, Any] = {}
        # Build DeferredToolResults from elicitation_payloads (primary mechanism).
        # pydantic-ai's agentlet.iter() starts from UserPromptNode, NOT from
        # ModelResponse in message_history. It generates a NEW ModelResponse
        # with a NEW tool_call_id, so cached_elicitation_responses (keyed by
        # the OLD tool_call_id) are never hit. By passing DeferredToolResults,
        # pydantic-ai matches them against the ModelResponse in message_history
        # and uses the results directly, skipping tool execution entirely.
        from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
        from pydantic_ai.tools import DeferredToolResults

        # Build a mapping from elicitation handle (PendingDeferredCall.tool_call_id)
        # to the actual ToolCallPart.tool_call_id in the ModelResponse.
        # This is needed because handle_elicitation() may use run_ctx.run_id as
        # the handle when agent_ctx.tool_call_id is not set (MCP tools without
        # AgentContext param). The DeferredToolResults must be keyed by the
        # ToolCallPart.tool_call_id (what pydantic-ai expects), not the
        # elicitation handle.
        pending_by_handle: dict[str, PendingDeferredCall] = {
            call.tool_call_id: call for call in checkpoint.pending_calls
        }
        # Find ToolCallPart.tool_call_id for each pending call.
        # Match by tool_name first; fall back to positional matching
        # (MCP tools without AgentContext param may have empty tool_name
        # in PendingDeferredCall, so name matching fails).
        tool_call_id_map: dict[str, str] = {}  # handle → ToolCallPart.tool_call_id
        last_model_response: ModelResponse | None = None
        for msg in reversed(checkpoint.message_history):
            if isinstance(msg, ModelResponse):
                last_model_response = msg
                break
        if last_model_response is not None:
            # Collect all ToolCallParts from the last ModelResponse
            tool_call_parts: list[ToolCallPart] = [
                part
                for part in last_model_response.parts
                if isinstance(part, ToolCallPart) and part.tool_call_id
            ]
            pending_calls_list = list(pending_by_handle.items())

            # First pass: match by tool_name
            matched_tc_indices: set[int] = set()
            for handle, pcall in pending_calls_list:
                if not pcall.tool_name:
                    continue
                for i, tc in enumerate(tool_call_parts):
                    if i in matched_tc_indices:
                        continue
                    if tc.tool_name == pcall.tool_name:
                        tool_call_id_map[handle] = tc.tool_call_id
                        matched_tc_indices.add(i)
                        break

            # Second pass: positional matching for unmatched handles
            unmatched_tc_indices = [
                i for i in range(len(tool_call_parts)) if i not in matched_tc_indices
            ]
            unmatched_handles = [h for h, _ in pending_calls_list if h not in tool_call_id_map]
            for handle, tc_idx in zip(unmatched_handles, unmatched_tc_indices, strict=False):
                tool_call_id_map[handle] = tool_call_parts[tc_idx].tool_call_id

        elicitation_tool_results: dict[str, Any] = {}
        if elicitation_payloads:
            from mcp.types import ElicitResult as MCPElicitResult

            for payload in elicitation_payloads:
                # Map elicitation handle to ToolCallPart.tool_call_id
                actual_tool_call_id = tool_call_id_map.get(
                    payload.deferred_handle, payload.deferred_handle
                )
                pcall_deferred: PendingDeferredCall | None = pending_by_handle.get(
                    payload.deferred_handle
                )
                tool_name = pcall_deferred.tool_name if pcall_deferred else ""
                match payload.action:
                    case "accept":
                        cached_elicitation[payload.deferred_handle] = MCPElicitResult(
                            action="accept",
                            content=payload.content,
                        )
                        elicitation_tool_results[actual_tool_call_id] = ToolReturnPart(
                            tool_name=tool_name,
                            content=payload.content or {},
                            tool_call_id=actual_tool_call_id,
                        )
                    case "decline":
                        cached_elicitation[payload.deferred_handle] = MCPElicitResult(
                            action="decline",
                        )
                        elicitation_tool_results[actual_tool_call_id] = ToolReturnPart(
                            tool_name=tool_name,
                            content="declined",
                            tool_call_id=actual_tool_call_id,
                        )
                    case "cancel":
                        cached_elicitation[payload.deferred_handle] = MCPElicitResult(
                            action="cancel",
                        )
                        elicitation_tool_results[actual_tool_call_id] = ToolReturnPart(
                            tool_name=tool_name,
                            content="cancelled",
                            tool_call_id=actual_tool_call_id,
                        )

        try:
            # Route through the pool's normal turn management so the
            # resumed turn has full RunHandle lifecycle (journal,
            # snapshot, event delivery, session coordination).
            # Pass message_history as list[ModelMessage] (NOT wrapped
            # in MessageHistory) — the pool path initializes
            # RunHandle._message_history directly.
            run_kwargs: dict[str, Any] = {
                "cached_elicitation_responses": cached_elicitation or None,
                "message_history": list(checkpoint.message_history),
            }
            # Build DeferredToolResults from elicitation_payloads.
            # This is the PRIMARY mechanism for crash recovery: pydantic-ai
            # matches these against the ModelResponse in message_history and
            # uses the results directly, skipping tool execution.
            # cached_elicitation_responses is kept as a fallback.
            if elicitation_tool_results:
                run_kwargs["deferred_tool_results"] = DeferredToolResults(
                    calls=elicitation_tool_results
                )
            elif getattr(results, "calls", None):
                # Non-elicitation deferred results from the caller
                run_kwargs["deferred_tool_results"] = results
            async for _ in self.run_stream(
                session_data.session_id,
                "",
                **run_kwargs,
            ):
                pass
        finally:
            await agent.__aexit__(None, None, None)

    async def _resume_acp_agent(
        self,
        session_data: SessionData,
        checkpoint: CheckpointData,
        results: Any,
    ) -> None:
        """Resume an ACP agent by reopening the subprocess and sending session/resume.

        Reopens the ACP subprocess and calls agent.run() to restart the
        session with restored state.

        Args:
            session_data: Persisted session data.
            checkpoint: Checkpoint data (used for metadata only; ACP agents
                manage their own message history).
            results: DeferredToolResults for resolving pending deferred calls.
        """
        agent = await self._reconstruct_acp_agent(session_data.session_id, session_data.agent_name)
        try:
            # ACP agents receive the resumed session context through run()
            run_fn: Any = agent.run
            await run_fn(
                message_history=list(checkpoint.message_history),
                deferred_tool_results=results,
            )
        finally:
            if hasattr(agent, "__aexit__"):
                await agent.__aexit__(None, None, None)

    async def _try_in_process_elicitation_resume(
        self,
        session_id: str,
        elicitation_payloads: list[ElicitationResumePayload],
    ) -> bool:
        """Attempt in-process elicitation resume by resolving futures.

        Checks if the agent run is still alive (in-process) and has an
        ``ElicitationFutureRegistry`` with pending futures for the given
        elicitation payloads. If so, resolves each future with the
        corresponding payload.

        Args:
            session_id: The session to check.
            elicitation_payloads: Elicitation responses to resolve.

        Returns:
            True if ALL payloads were resolved in-process, False otherwise.
        """
        run_handle = self._get_active_run_handle(session_id)
        if run_handle is None or run_handle.run_ctx is None:
            return False

        registry = run_handle.run_ctx.elicitation_registry
        if registry is None:
            return False

        all_resolved = True
        for payload in elicitation_payloads:
            if payload.deferred_handle not in registry:
                all_resolved = False
                continue
            registry.resolve(payload.deferred_handle, payload)

        if all_resolved:
            logger.debug(
                "In-process elicitation resume — all futures resolved",
                session_id=session_id,
                count=len(elicitation_payloads),
            )
        else:
            logger.debug(
                "In-process elicitation resume — some futures not found, "
                "falling back to crash recovery",
                session_id=session_id,
            )

        return all_resolved

    async def resume_session(  # noqa: PLR0915
        self,
        session_id: str,
        deferred_tool_results: Any,
        *,
        source: str = "resume_prompt",
        elicitation_payloads: list[ElicitationResumePayload] | None = None,
    ) -> None:
        """Resume a paused session with resolved deferred tool results.

        Loads the persisted SessionData, validates that deferred_tool_results
        cover all pending_deferred_calls (raising CheckpointMismatchError if not),
        and resumes execution via the appropriate path:

        - **In-process elicitation resume**: If ``elicitation_payloads`` are
          provided and the elicitation future still exists in the
          ``ElicitationFutureRegistry`` (agent run is still alive), the future
          is resolved directly.
        - **Crash recovery elicitation resume**: If ``elicitation_payloads``
          are provided but the future does NOT exist, the elicitation responses
          are pre-populated into ``AgentRunContext.cached_elicitation_responses``
          and the agent run is re-executed.
        - **Non-elicitation resume**: Native agent: load checkpoint →
          reconstruct agent → ``agent.run_stream()`` with deferred results.

        Args:
            session_id: Session to resume.
            deferred_tool_results: Results for pending deferred tool calls.
            source: Identifier for the entity triggering the resume.
            elicitation_payloads: Optional elicitation responses for resuming
                deferred elicitation calls.

        Raises:
            SessionNotFoundError: If the session does not exist in storage.
            SessionBusyError: If the session has an active run.
            CheckpointMismatchError: If results don't cover all pending calls.
        """
        store = self.sessions.store
        if store is None:
            raise SessionNotFoundError(session_id)

        # Load persisted session data
        data = await store.load(session_id)
        if data is None:
            raise SessionNotFoundError(session_id)

        # Separate elicitation and non-elicitation pending call IDs.
        # Must be done before the SessionBusyError check so we can detect
        # in-process elicitation resume (where the agent run is still alive).
        elicitation_call_ids: set[str] = {
            call.tool_call_id
            for call in data.pending_deferred_calls
            if call.deferred_kind == "elicitation"
        }
        non_elicitation_pending_ids: set[str] = {
            call.tool_call_id for call in data.pending_deferred_calls
        } - elicitation_call_ids

        # Fast-path: check for active run in live sessions (before lock).
        # EXCEPTION: in-process elicitation resume — the agent run is
        # intentionally still alive, paused on elicitation futures.
        session = self.sessions.get_session(session_id)
        has_in_process_elicitation = False
        if session is not None and session.current_run_id is not None:
            if elicitation_payloads is not None:
                run_handle = self._get_active_run_handle(session_id)
                if (
                    run_handle is not None
                    and run_handle.run_ctx is not None
                    and run_handle.run_ctx.elicitation_registry is not None
                    and len(run_handle.run_ctx.elicitation_registry) > 0
                    and all(
                        p.deferred_handle in run_handle.run_ctx.elicitation_registry
                        for p in elicitation_payloads
                    )
                ):
                    has_in_process_elicitation = True

            if not has_in_process_elicitation:
                raise SessionBusyError(session_id, session.current_run_id)

        provided_call_ids: set[str] = set(getattr(deferred_tool_results, "calls", {}).keys())

        missing = non_elicitation_pending_ids - provided_call_ids
        extra = provided_call_ids - non_elicitation_pending_ids - elicitation_call_ids
        if missing or extra:
            raise CheckpointMismatchError(
                session_id=session_id,
                expected=non_elicitation_pending_ids,
                provided=provided_call_ids,
                missing=missing,
                extra=extra,
            )

        # Validate elicitation_payloads cover all elicitation deferred calls.
        if elicitation_call_ids:
            provided_elicitation_ids: set[str] = {
                p.deferred_handle for p in (elicitation_payloads or [])
            }
            missing_elicitation = elicitation_call_ids - provided_elicitation_ids
            if missing_elicitation:
                raise CheckpointMismatchError(
                    session_id=session_id,
                    expected=elicitation_call_ids,
                    provided=provided_elicitation_ids,
                    missing=missing_elicitation,
                    extra=set(),
                )

        # Determine agent type
        agent_type = data.metadata.get("agent_type", "native")

        # Per-session resume lock with state validation.
        # For in-process elicitation resume, allow the active run to persist.
        async with self._with_resume_lock(
            session_id, allow_active_run=has_in_process_elicitation
        ) as session:
            try:
                # In-process elicitation resume: resolve futures so the
                # suspended agent run can continue naturally.
                if elicitation_payloads:
                    resolved = await self._try_in_process_elicitation_resume(
                        session_id, elicitation_payloads
                    )
                    if resolved:
                        # All futures resolved — agent run will continue.
                        data = data.model_copy(
                            update={
                                "status": "active",
                                "pending_deferred_calls": [],
                            }
                        )
                        data.touch()
                        await store.save(data)

                        if session is not None:
                            session.last_active_at = time.monotonic()

                        total_resolved = len(elicitation_call_ids)
                        await self.event_bus.publish(
                            session_id,
                            SessionResumeEvent(
                                session_id=session_id,
                                resolved_call_count=total_resolved,
                                source=source,
                            ),
                        )

                        logger.info(
                            "In-process elicitation resume — futures resolved",
                            session_id=session_id,
                            count=total_resolved,
                        )
                        return

                    # In-process resolution failed (race condition).
                    # If the run is still active, we cannot start crash
                    # recovery — that would create a concurrent run.
                    if session is not None and session.current_run_id is not None:
                        raise SessionBusyError(session_id, session.current_run_id)  # noqa: TRY301

                # Load checkpoint data
                checkpoint = await self._load_checkpoint_data(session_id)

                # Mark session as resuming
                data = data.model_copy(update={"status": "resuming"})
                await store.save(data)

                # Route to appropriate resume path
                if agent_type == "acp":
                    await self._resume_acp_agent(data, checkpoint, deferred_tool_results)
                else:
                    await self._resume_native_agent(
                        data,
                        checkpoint,
                        deferred_tool_results,
                        elicitation_payloads=elicitation_payloads,
                    )

                # Clear pending_deferred_calls ONLY after agent.run() succeeds
                data = data.model_copy(
                    update={
                        "status": "active",
                        "pending_deferred_calls": [],
                    }
                )
                data.touch()
                await store.save(data)

                # Update live session if one exists
                if session is not None:
                    session.last_active_at = time.monotonic()

                # Emit SessionResumeEvent
                total_resolved = len(non_elicitation_pending_ids) + len(elicitation_call_ids)
                await self.event_bus.publish(
                    session_id,
                    SessionResumeEvent(
                        session_id=session_id,
                        resolved_call_count=total_resolved,
                        source=source,
                    ),
                )

                logger.info(
                    "Session resumed successfully",
                    session_id=session_id,
                    agent_type=agent_type,
                    resolved_calls=total_resolved,
                )

            except Exception:
                # On failure, keep status as checkpointed and do NOT clear pending calls
                data = data.model_copy(update={"status": "checkpointed"})
                data.touch()
                await store.save(data)
                raise

    async def close_session(self, session_id: str) -> None:
        """Close a session.

        Waits for any active run to complete before proceeding.
        Order: wait for run, reject elicitation futures, session cleanup,
        event bus, then turn state.

        Args:
            session_id: The session to close.
        """
        session = self.sessions.get_session(session_id)
        run_handle: RunHandle | None = None
        if session is not None:
            async with session._request_lock:
                session.closing = True
                run_id = session.current_run_id
                if run_id is not None:
                    run_handle = self.sessions._runs.get(run_id)

            if run_handle is not None:
                # Signal the RunHandle to stop its idle/wake loop so that
                # start()'s finally block can set complete_event promptly.
                run_handle.close()
                # Unblock any background-task wait loop inside the run so
                # complete_event can be set promptly instead of waiting.
                if run_handle.run_ctx is not None:
                    run_handle.run_ctx.cancelled = True
                    # Snapshot values before setting to avoid dict mutation race.
                    for ev in list(run_handle.run_ctx.child_done_events.values()):
                        ev.set()
                    run_handle.run_ctx.child_done_events.clear()
                try:
                    await asyncio.wait_for(run_handle.complete_event.wait(), timeout=2.0)
                except TimeoutError:
                    self.cancel_run(run_handle.run_id)
                    await asyncio.sleep(0.1)

        # Reject all pending elicitation futures so blocked MCP tool calls
        # unblock immediately instead of hanging on a closed session.
        if run_handle is not None and run_handle.run_ctx is not None:
            registry = run_handle.run_ctx.elicitation_registry
            if registry is not None:
                registry.reject_all(SessionClosedError(session_id))

        try:
            await self.sessions.close_session(session_id)
        except Exception:
            logger.exception(
                "Failed to close session in controller",
                session_id=session_id,
            )
        finally:
            # EventBus and message cache cleanup may be interrupted by
            # CancelledError from garbage-collected async generator cleanup.
            # Suppress these spurious cancellations so shutdown proceeds.
            try:
                await self.event_bus.close_session(session_id)
            except asyncio.CancelledError:
                logger.warning(
                    "EventBus close_session interrupted by spurious cancellation",
                    session_id=session_id,
                )
            except Exception:
                logger.exception(
                    "Failed to close event bus session",
                    session_id=session_id,
                )

            self._message_cache.pop(session_id, None)

    async def _await_inflight_checkpoints(self) -> None:
        """Wait for any in-flight checkpoint operations to complete.

        During normal operation, checkpoint-on-close happens synchronously
        inside :meth:`close_session`, so there are no in-flight operations
        to await. This method is a future-proof hook for graceful teardown:
        if the checkpoint mechanism ever becomes asynchronous (e.g.,
        background flush), this method ensures the shutdown waits for
        completion.

        Called from :meth:`AgentPool.__aexit__` during pool shutdown.
        """
        # Currently no-op: all checkpoint operations complete synchronously
        # within SessionController.close_session() under its lock.
        logger.debug("No in-flight checkpoint operations to await")

    # ------------------------------------------------------------------
    # RunHandle delegation helpers
    # ------------------------------------------------------------------

    def _get_active_run_handle(self, session_id: str) -> RunHandle | None:
        """Get the active RunHandle for a session, if any.

        Returns:
            The RunHandle, or None if no active run exists.
        """
        session = self.sessions.get_session(session_id)
        if session is None or session.current_run_id is None:
            return None
        return self.sessions._runs.get(session.current_run_id)

    def _create_run_handle(
        self,
        session: SessionState,
        agent: BaseAgent[Any, Any],
        session_id: str,
        *,
        cached_elicitation_responses: dict[str, Any] | None = None,
        deferred_tool_results: Any = None,
        message_history: list[ModelMessage] | None = None,
    ) -> RunHandle:
        """Create and register a RunHandle without a background task.

        Unlike :meth:`SessionController._start_run_handle`, this does
        NOT create an asyncio task to consume ``start()``. The caller
        is responsible for draining ``start()``.

        Creates the RunHandle with ProtocolTrigger and ProtocolChannel
        lifecycle dimensions for protocol server integration, matching
        the pattern in :meth:`SessionController._start_run_handle`.

        Also wires ``_host_context`` and ``_agent_registry`` so that
        ``get_agentlet()`` can create a ``CheckpointManager`` and
        ``SubagentCapability`` can resolve agents — matching the
        infrastructure provided by ``_start_run_handle()``.

        Resume parameters (``cached_elicitation_responses``,
        ``deferred_tool_results``, ``message_history``) are only set
        by ``resume_session()``. Normal turns pass ``None`` for all
        three — runtime behavior is unchanged.

        Args:
            session: The session state.
            agent: The agent instance (native or ACP).
            session_id: The session identifier.
            cached_elicitation_responses: Pre-populated elicitation
                responses for crash recovery resume.
            deferred_tool_results: Deferred tool results for resolving
                pending deferred calls during resume.
            message_history: Message history from checkpoint to
                initialize ``RunHandle._message_history``.

        Returns:
            The newly created and registered RunHandle.

        Raises:
            SessionBusyError: If the session already has an active run
                that is not ``DONE``.
        """
        from agentpool.lifecycle import (
            MemoryJournal,
            ProtocolChannel,
            ProtocolTrigger,
        )
        from agentpool.lifecycle.types import RunState

        # Staleness check: prevent silently overwriting an active run.
        if session.current_run_id is not None and session.current_run_id in self.sessions._runs:
            existing = self.sessions._runs[session.current_run_id]
            if existing._run_state != RunState.DONE:
                raise SessionBusyError(session_id, session.current_run_id)

        event_bus = self.event_bus
        run_ctx = AgentRunContext(session_id=session_id, event_bus=event_bus)
        if cached_elicitation_responses is not None:
            run_ctx.cached_elicitation_responses = cached_elicitation_responses

        trigger = ProtocolTrigger()
        comm_channel: ProtocolChannel | None = None
        if event_bus is not None:
            journal = MemoryJournal()
            comm_channel = ProtocolChannel(
                journal=journal,
                event_bus=event_bus,
                session_id=session_id,
            )

        # Wire _host_context and _agent_registry, matching the pattern
        # in SessionController._start_run_handle() (session_controller.py:950-972).
        from agentpool.host.registry import AgentRegistry

        host_ctx = self.pool.get_context()
        agent_registry = AgentRegistry(
            dict.fromkeys(self.pool.manifest.agents),  # type: ignore[arg-type]
        )

        run_handle = RunHandle(
            run_id=uuid.uuid4().hex,
            session_id=session_id,
            agent_type=agent.AGENT_TYPE,
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
            _trigger_source=trigger,
            _comm_channel=comm_channel,
            _host_context=host_ctx,
            _agent_registry=agent_registry,
        )
        if message_history is not None:
            # Handle both list[ModelMessage] and MessageHistory objects.
            # Some callers (e.g. subagent_tools.py) pass MessageHistory()
            # which is not directly iterable as list[ModelMessage].
            from agentpool.messaging.message_history import MessageHistory

            if isinstance(message_history, MessageHistory):
                model_msgs: list[ModelMessage] = []
                for chat_msg in message_history.get_history():
                    model_msgs.extend(chat_msg.messages)
                run_handle._message_history = model_msgs
            else:
                run_handle._message_history = list(message_history)
        if deferred_tool_results is not None:
            run_handle._resume_deferred_tool_results = deferred_tool_results
        self.sessions._runs[run_handle.run_id] = run_handle
        session.current_run_id = run_handle.run_id
        return run_handle

    async def _process_prompt_run_turn(
        self,
        session_id: str,
        *prompts: Any,
        **kwargs: Any,
    ) -> None:
        """Handle process_prompt via the RunHandle path.

        If no active run exists, creates a RunHandle and drains
        ``start()`` to completion. If a run is active, steers the
        message into it.
        """
        session, _ = await self.sessions.get_or_create_session(session_id)
        if session.is_closing:
            return
        # Extract input_provider from kwargs and set on session BEFORE
        # get_or_create_session_agent() so the agent is created with the
        # correct input_provider and the session state is consistent.
        input_provider = kwargs.pop("input_provider", None)
        if input_provider is not None:
            session.input_provider = input_provider
        agent = await self.sessions.get_or_create_session_agent(
            session_id, input_provider=input_provider
        )
        if agent is None:
            return
        content = " ".join(str(p) for p in prompts) if prompts else ""

        run_id = session.current_run_id
        if run_id is not None:
            run_handle = self.sessions._runs.get(run_id)
            if run_handle is not None:
                run_handle.steer(content)
            return

        run_handle = self._create_run_handle(session, agent, session_id)
        gen = run_handle.start(content)
        try:
            async for _event in gen:
                if isinstance(_event, StreamCompleteEvent | RunErrorEvent):
                    break
        finally:
            await gen.aclose()
            session.current_run_id = None
            self.sessions._runs.pop(run_handle.run_id, None)

    # ------------------------------------------------------------------
    # SessionPool public methods
    # ------------------------------------------------------------------

    async def process_prompt(
        self,
        session_id: str,
        *prompts: Any,
        **kwargs: Any,
    ) -> None:
        """Process a prompt through the RunHandle lifecycle.

        Main entry point for protocol handlers.
        Events are delivered exclusively via EventBus.

        Args:
            session_id: The session to process the prompt for.
            *prompts: Prompts to process.
            **kwargs: Additional arguments passed to the agent.
        """
        await self._process_prompt_run_turn(session_id, *prompts, **kwargs)

    async def receive_request(
        self,
        session_id: str,
        content: str | list[Any],
        *,
        priority: str = "when_idle",
        message_id: str | None = None,
        **kwargs: Any,
    ) -> str | None:
        """Route an incoming request for a session (fire-and-forget).

        .. deprecated::
            Use :meth:`send_message` with ``DeliveryMode`` instead.
            This method maps ``priority`` to ``DeliveryMode`` and
            delegates to :meth:`send_message`.

        Creates a background task that processes the prompt through
        the RunHandle lifecycle. Protocol handlers should subscribe to the
        EventBus *before* calling this method so no events are dropped.

        Idle sessions create a RunHandle, busy sessions call
        ``RunHandle.steer()`` or ``RunHandle.followup()``.

        Args:
            session_id: Target session.
            content: Message / prompt content (text or structured content
                blocks). List content is passed through without
                stringification.
            priority: ``"when_idle"`` to queue, ``"asap"`` to inject into
                active turn. Unknown values default to ``"when_idle"``.
            message_id: Optional message ID. Auto-generated as UUID4 if
                not provided.
            **kwargs: Additional arguments passed to the turn runner.

        Returns:
            The ``message_id`` string on success, ``None`` on failure.
        """
        import warnings

        warnings.warn(
            "SessionPool.receive_request() is deprecated. "
            "Use send_message() with DeliveryMode instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        # Map priority string to DeliveryMode enum.
        if priority == "asap":
            mode = DeliveryMode.STEER
        elif priority == "when_idle":
            mode = DeliveryMode.QUEUE
        else:
            warnings.warn(
                f"Unknown priority {priority!r}; defaulting to QUEUE. "
                "Use send_message() with DeliveryMode for type safety.",
                DeprecationWarning,
                stacklevel=2,
            )
            mode = DeliveryMode.QUEUE
        return await self.send_message(
            session_id,
            content,
            mode=mode,
            message_id=message_id,
        )

    async def send_message(
        self,
        session_id: str,
        content: str | list[Any],
        *,
        mode: DeliveryMode = DeliveryMode.QUEUE,
        message_id: str | None = None,
    ) -> str | None:
        """Send a message to a session using the typed ``DeliveryMode`` enum.

        Maps ``DeliveryMode.STEER`` → ``priority="asap"`` (inject into
        active turn) and ``DeliveryMode.QUEUE`` → ``priority="when_idle"``
        (queue for next turn), then delegates to
        :meth:`SessionController.receive_request`.

        Args:
            session_id: Target session.
            content: Message / prompt content (text or structured content
                blocks). List content is stored as ``Feedback.content_blocks``
                without stringification.
            mode: Delivery mode — ``STEER`` for mid-turn injection,
                ``QUEUE`` for next-turn queue (default).
            message_id: Optional message ID. Auto-generated as UUID4 if
                not provided. Returned on success.

        Returns:
            The ``message_id`` string on success (both new runs and
            steer/followup), ``None`` on failure.
        """
        priority = "asap" if mode is DeliveryMode.STEER else "when_idle"
        # Delegate directly to _route_message() to avoid the deprecated
        # receive_request() path. This requires session+agent resolution,
        # which mirrors what SessionController.receive_request() does.
        session = self.sessions.get_session(session_id)
        if session is None:
            return None
        session.last_active_at = time.monotonic()
        agent = await self.sessions.get_or_create_session_agent(session_id)
        if agent is None:
            return None
        return await self.sessions._route_message(
            session,
            agent,
            session_id,
            content,
            priority=priority,
            message_id=message_id,
        )

    async def run_agent(
        self,
        agent: str,
        prompt: str,
        parent_session_id: str | None = None,
        **metadata: Any,
    ) -> str:
        """Run an agent to completion and return the result text.

        Creates a temporary session, sends the prompt via
        :meth:`send_message` with ``DeliveryMode.QUEUE``, subscribes to
        the EventBus to capture the ``StreamCompleteEvent``, waits for
        run completion, then closes the session. The session is always
        cleaned up via ``try/finally`` even on error.

        Args:
            agent: Name of the agent to run.
            prompt: Input prompt for the agent.
            parent_session_id: Optional parent session for hierarchical
                sessions (e.g. subagent delegation).
            **metadata: Arbitrary metadata attached to the session.

        Returns:
            The final assistant response text.

        Raises:
            SessionNotFoundError: If the session cannot be created.
            asyncio.TimeoutError: If the run does not complete within
                the default timeout.
            Exception: Any error from the agent run is re-raised after
                session cleanup.
        """
        import uuid

        from agentpool.agents.events import RunErrorEvent

        session_id = str(uuid.uuid4())
        await self.create_session(
            session_id,
            agent_name=agent,
            parent_session_id=parent_session_id,
            **metadata,
        )

        # Subscribe to EventBus BEFORE sending the message to avoid
        # missing the StreamCompleteEvent in a race.
        bus_queue = await self.event_bus.subscribe(session_id, scope="session")
        result_text: str = ""
        try:
            msg_id = await self.send_message(
                session_id,
                prompt,
                mode=DeliveryMode.QUEUE,
            )
            if msg_id is None:
                msg = f"Failed to send message to session {session_id}"
                raise RuntimeError(msg)

            # Drain events from the EventBus until we capture the
            # StreamCompleteEvent or RunErrorEvent.
            while True:
                try:
                    envelope = await asyncio.wait_for(bus_queue.get(), timeout=120.0)
                except TimeoutError:
                    logger.warning(
                        "Agent execution timed out after 120 seconds in run_agent",
                        session_id=session_id,
                    )
                    msg = f"Agent execution timed out after 120 seconds for session {session_id}"
                    raise TimeoutError(msg) from None
                event = envelope.event
                if isinstance(event, StreamCompleteEvent):
                    content = event.message.content
                    result_text = content if isinstance(content, str) else str(content)
                    break
                if isinstance(event, RunErrorEvent):
                    raise RuntimeError(event.message)  # noqa: TRY004

            # Ensure the run has fully completed before closing.
            await self.wait_for_completion(session_id, timeout=10.0)
        finally:
            try:
                await self.event_bus.unsubscribe(session_id, bus_queue)
            except Exception:
                logger.exception("Failed to unsubscribe from EventBus", session_id=session_id)
            try:
                await self.close_session(session_id)
            except Exception:
                logger.exception("Failed to close session", session_id=session_id)

        return result_text

    async def wait_for_completion(
        self,
        session_id: str,
        timeout: float | None = None,
    ) -> str:
        """Wait for the active run on a session to complete.

        Args:
            session_id: The session to wait for.
            timeout: Maximum seconds to wait. ``None`` waits indefinitely.

        Returns:
            The ``session_id`` on completion.

        Raises:
            SessionNotFoundError: If the session does not exist.
            asyncio.TimeoutError: If the run does not complete within
                ``timeout`` seconds.
        """
        return await self.sessions.wait_for_completion(session_id, timeout=timeout)

    def revoke_message(self, session_id: str, message_id: str) -> bool:
        """Revoke a pending steer or followup message by ID.

        Args:
            session_id: The session containing the message.
            message_id: The ID of the message to revoke.

        Returns:
            ``True`` if revoked, ``False`` if already delivered or not found.
        """
        return self.sessions.revoke_inject(session_id, message_id)

    @property
    def active_runs(self) -> list[RunHandle]:
        """Get all currently active (running) RunHandles."""
        return [rh for rh in self.sessions._runs.values() if rh.is_running]

    def get_run(self, run_id: str) -> RunHandle | None:
        """Get a RunHandle by ID.

        Args:
            run_id: The run ID to look up.

        Returns:
            The RunHandle, or None if not found.
        """
        return self.sessions._runs.get(run_id)

    def cancel_run(self, run_id: str) -> None:
        """Cancel a run by ID.

        Args:
            run_id: The run ID to cancel.

        Raises:
            ValueError: If no active run with the given ID exists.
        """
        run_handle = self.sessions._runs.get(run_id)
        if run_handle is None:
            raise ValueError("No active run found with ID: " + run_id)
        run_handle.cancel()

    async def run_stream(
        self,
        session_id: str,
        *prompts: Any,
        scope: str = "session",
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Process prompts and yield events.

        Convenience method for tests and standalone clients that want
        an async iterator over session events. Yields events directly
        from ``RunHandle.start()`` when no active run exists. If a run
        is already active, steers the message and falls back to EventBus.

        Args:
            session_id: The session to process the prompt for.
            *prompts: Prompts to process.
            scope: Subscription scope - "session" (exact match),
                "descendants" (self + children), or "subtree" (self + parent + siblings).
            **kwargs: Additional arguments passed to the turn runner
                (e.g. ``input_provider``).

        Yields:
            Events published to the EventBus for this session.
        """
        async for event in self._run_stream_run_turn(session_id, *prompts, scope=scope, **kwargs):
            yield event

    async def _run_stream_run_turn(  # noqa: PLR0915
        self,
        session_id: str,
        *prompts: Any,
        scope: str = "session",
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Handle run_stream via the RunHandle path.

        If no active run exists, creates a RunHandle and yields events
        directly from ``start()``. If a run is active, steers the
        message and yields from the EventBus subscription.

        Resume parameters (``cached_elicitation_responses``,
        ``deferred_tool_results``, ``message_history``) are extracted
        from ``**kwargs`` and forwarded to ``_create_run_handle()``.
        Only set by ``resume_session()``; normal turns pass ``None``.
        """
        session, _ = await self.sessions.get_or_create_session(session_id)
        if session.is_closing:
            return
        # Extract input_provider from kwargs and set on session BEFORE
        # get_or_create_session_agent() so the agent is created with the
        # correct input_provider and the session state is consistent.
        input_provider = kwargs.pop("input_provider", None)
        if input_provider is not None:
            session.input_provider = input_provider
        # Extract resume parameters — only set by resume_session().
        cached_elicitation_responses: dict[str, Any] | None = kwargs.pop(
            "cached_elicitation_responses", None
        )
        deferred_tool_results: Any = kwargs.pop("deferred_tool_results", None)
        message_history: list[ModelMessage] | None = kwargs.pop("message_history", None)
        agent = await self.sessions.get_or_create_session_agent(
            session_id, input_provider=input_provider
        )
        if agent is None:
            return
        content = " ".join(str(p) for p in prompts) if prompts else ""

        run_id = session.current_run_id
        if run_id is not None:
            # Active run — steer and use EventBus
            run_handle = self.sessions._runs.get(run_id)
            if run_handle is not None:
                run_handle.steer(content)
            queue = await self.event_bus.subscribe(session_id, scope=scope)
            try:
                while True:
                    try:
                        event = await queue.get()
                    except asyncio.QueueShutDown:
                        break
                    yield event.event
                    raw_event = getattr(event, "event", event)
                    if isinstance(raw_event, StreamCompleteEvent | RunErrorEvent):
                        break
            finally:
                await self.event_bus.unsubscribe(session_id, queue)
            return

        # No active run — create RunHandle and yield from start().
        # Acquire _request_lock to prevent race with concurrent
        # receive_request() → _start_run_handle() on the same session.
        # Without this, both paths could see current_run_id is None and
        # create overlapping RunHandles.
        async with session._request_lock:
            # Re-check current_run_id inside the lock — another caller
            # may have created a run while we waited.
            if session.current_run_id is not None:
                # Active run appeared — steer and use EventBus
                run_handle = self.sessions._runs.get(session.current_run_id)
                if run_handle is not None:
                    run_handle.steer(content)
                queue = await self.event_bus.subscribe(session_id, scope=scope)
                try:
                    while True:
                        try:
                            event = await queue.get()
                        except asyncio.QueueShutDown:
                            break
                        yield event.event
                        raw_event = getattr(event, "event", event)
                        if isinstance(raw_event, StreamCompleteEvent | RunErrorEvent):
                            break
                finally:
                    await self.event_bus.unsubscribe(session_id, queue)
                return

            # Also subscribe to EventBus so that events published by tools
            # during turn execution (e.g. SpawnSessionStart from task() →
            # create_child_session()) are delivered to the consumer, not
            # just events yielded directly by start().
            run_handle = self._create_run_handle(
                session,
                agent,
                session_id,
                cached_elicitation_responses=cached_elicitation_responses,
                deferred_tool_results=deferred_tool_results,
                message_history=message_history,
            )
            self.event_bus.clear_replay_buffer(session_id)
            bus_queue = await self.event_bus.subscribe(session_id, scope=scope)
            gen = run_handle.start(content)
        # Lock released — the run is now registered and can be steered
        # by concurrent receive_request() calls.
        try:
            async for evt in gen:
                # Drain any tool-published events from EventBus before
                # yielding the start() event. This ensures SpawnSessionStart
                # and similar events appear before the StreamCompleteEvent.
                with contextlib.suppress(asyncio.QueueEmpty):
                    while True:
                        envelope = bus_queue.get_nowait()
                        yield envelope.event
                yield evt
                if isinstance(evt, StreamCompleteEvent | RunErrorEvent):
                    break
        finally:
            await gen.aclose()
            await self.event_bus.unsubscribe(session_id, bus_queue)
            session.current_run_id = None
            self.sessions._runs.pop(run_handle.run_id, None)

    async def inject_prompt(self, session_id: str, message: str, **kwargs: Any) -> str | None:
        """Inject a message into a session.

        If the session has an active run, injects immediately via
        ``RunHandle.steer()``. Otherwise, returns None.

        Does NOT acquire session.turn_lock.

        Args:
            session_id: The session to inject into.
            message: The message to inject.
            **kwargs: Additional arguments passed to the agent run.

        Returns:
            message_id if injected into active turn, None otherwise.
        """
        run_handle = self._get_active_run_handle(session_id)
        if run_handle is not None:
            return run_handle.steer(message)
        return None

    async def queue_prompt(self, session_id: str, *prompts: Any, **kwargs: Any) -> str | None:
        """Queue prompts for a session.

        Similar to inject_prompt but for full prompts.
        Does NOT acquire session.turn_lock.

        Args:
            session_id: The session to queue prompts for.
            *prompts: Prompts to queue.
            **kwargs: Additional arguments passed to the agent run.

        Returns:
            message_id if queued into active turn, None otherwise.
        """
        run_handle = self._get_active_run_handle(session_id)
        if run_handle is not None:
            message = prompts[0] if prompts else ""
            return run_handle.followup(str(message))
        return None

    @logfire.instrument("session.steer {session_id}")
    async def steer(self, session_id: str, message: str, **kwargs: Any) -> str | None:
        """Inject a steer message with agent-type-aware routing.

        Delegates to ``RunHandle.steer()`` when an active run exists.

        Args:
            session_id: Target session.
            message: The steer message to deliver.
            **kwargs: Additional arguments (ignored).

        Returns:
            message_id if delivered into active turn, None otherwise.
        """
        run_handle = self._get_active_run_handle(session_id)
        if run_handle is not None:
            return run_handle.steer(message)
        return None

    @logfire.instrument("session.followup {session_id}")
    async def followup(self, session_id: str, message: str, **kwargs: Any) -> str | None:
        """Queue a follow-up message with agent-type-aware routing.

        Delegates to ``RunHandle.followup()`` when an active run exists.

        Args:
            session_id: Target session.
            message: The follow-up message to deliver.
            **kwargs: Additional arguments (ignored).

        Returns:
            message_id if delivered into active turn, None otherwise.
        """
        run_handle = self._get_active_run_handle(session_id)
        if run_handle is not None:
            return run_handle.followup(message)
        return None

    async def get_messages(
        self,
        session_id: str,
    ) -> list[ChatMessage[Any]]:
        """Get message history for a session.

        Results are cached per session_id (full message list) to avoid
        repeated storage queries. Cache is invalidated by append_message,
        truncate_messages, and copy_messages.

        Args:
            session_id: The session to retrieve messages for.

        Returns:
            List of messages ordered by timestamp (oldest first).

        Raises:
            KeyError: If the session does not exist.
        """
        session = self.sessions.get_session(session_id)
        if session is None:
            raise KeyError(session_id)

        if session_id in self._message_cache:
            return list(self._message_cache[session_id])

        storage = self.pool.storage
        if storage is not None:
            messages = await storage.get_session_messages(session_id)
            self._message_cache[session_id] = list(messages)
            return messages

        return []

    async def append_message(
        self,
        session_id: str,
        message: ChatMessage[Any],
    ) -> str:
        """Append a message to a session's history.

        Args:
            session_id: The session to append to.
            message: The message to append.

        Returns:
            The ID of the appended message.

        Raises:
            KeyError: If the session does not exist.
        """
        session = self.sessions.get_session(session_id)
        if session is None:
            raise KeyError(session_id)

        storage = self.pool.storage
        if storage is not None:
            await storage.log_message(message=message)

        self._message_cache.pop(session_id, None)
        return message.message_id

    async def copy_messages(
        self,
        source_session_id: str,
        target_session_id: str,
        *,
        up_to_message_id: str | None = None,
    ) -> str | None:
        """Copy messages from one session to another.

        Used by share_session (copy all) and revert_session (copy up to
        a specific message).

        Args:
            source_session_id: Session to copy from.
            target_session_id: Session to copy to.
            up_to_message_id: If set, only copy messages up to and
                including this message ID. If None, copy all messages.

        Returns:
            The ID of the fork point message (last copied message),
            or None if no messages were copied.

        Raises:
            KeyError: If either session does not exist.
        """
        if self.sessions.get_session(source_session_id) is None:
            raise KeyError(source_session_id)
        if self.sessions.get_session(target_session_id) is None:
            raise KeyError(target_session_id)

        storage = self.pool.storage
        if storage is not None:
            result = await storage.fork_conversation(
                source_session_id=source_session_id,
                new_session_id=target_session_id,
                fork_from_message_id=up_to_message_id,
            )
            self._message_cache.pop(target_session_id, None)
            return result

        return None

    async def truncate_messages(
        self,
        session_id: str,
        up_to_message_id: str,
    ) -> int:
        """Truncate messages after a specific message ID.

        Used by revert_session to remove messages after the revert point.

        Args:
            session_id: The session to truncate.
            up_to_message_id: Keep messages up to and including this ID,
                remove everything after.

        Returns:
            Number of messages removed.

        Raises:
            KeyError: If the session does not exist.
        """
        session = self.sessions.get_session(session_id)
        if session is None:
            raise KeyError(session_id)

        storage = self.pool.storage
        if storage is not None:
            removed = await storage.truncate_messages(session_id, up_to_message_id)
            self._message_cache.pop(session_id, None)
            return removed

        return 0
