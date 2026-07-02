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

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    RunErrorEvent,
    SessionResumeEvent,
    StreamCompleteEvent,
)
from agentpool.log import get_logger
from agentpool.orchestrator.event_bus import EventBus
from agentpool.orchestrator.run import RunHandle, RunStatus
from agentpool.orchestrator.session_controller import (
    CheckpointMismatchError,
    SessionBusyError,
    SessionController,
    SessionNotFoundError,
    SessionState,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.native_agent import Agent
    from agentpool.agents.native_agent.checkpoint import CheckpointData
    from agentpool.delegation import AgentPool
    from agentpool.delegation.team import Team
    from agentpool.delegation.teamrun import TeamRun
    from agentpool.messaging import ChatMessage
    from agentpool.messaging.messagenode import MessageNode
    from agentpool.sessions.models import SessionData
    from agentpool.sessions.store import SessionStore
    from agentpool_config.teams import TeamConfig


logger = get_logger(__name__)

DEFAULT_MAX_AUTO_RESUME: Final[int] = 10


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
    ) -> Team[Any] | TeamRun[Any, Any]:
        """Create a team from config using session-level agent resolution.

        For each member in the team config, resolves the agent via
        :meth:`SessionController.get_or_create_session_agent`, then
        constructs a :class:`Team` (parallel) or :class:`TeamRun`
        (sequential) using :meth:`TeamConfig.get_team`.

        Member names are stored on the resulting team nodes; actual
        session agents are created per-execution by
        :meth:`Team._resolve_scoped_team_nodes`.

        Args:
            team_name: Name for the created team.
            team_config: Team configuration from the manifest.

        Returns:
            A ``Team`` (parallel) or ``TeamRun`` (sequential) instance.

        Raises:
            ValueError: If a member name is not found in the manifest
                agents or teams sections.
        """
        from agentpool.utils.identifiers import generate_session_id

        member_names = [team_config.get_member_name(m) for m in team_config.members]

        nodes: list[MessageNode[Any, Any]] = []
        for member_name in member_names:
            cfg = self.pool.manifest.agents.get(member_name)
            if cfg is not None:
                member_session_id = generate_session_id()
                agent = await self.sessions.get_or_create_session_agent(
                    member_session_id,
                    agent_name=member_name,
                )
                nodes.append(agent)
            elif member_name in self.pool.manifest.teams:
                nested_config = self.pool.manifest.teams[member_name]
                nested_team = await self.create_team_from_config(member_name, nested_config)
                nodes.append(nested_team)
            else:
                msg = f"Team member {member_name!r} not found in manifest agents or teams"
                raise ValueError(msg)

        return team_config.get_team(nodes, team_name)

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
    async def _with_resume_lock(self, session_id: str) -> AsyncIterator[SessionState | None]:
        """Acquire per-session resume lock with state validation.

        Ensures only one resume runs per session at a time and that
        the session is in a resumable state (no active run, persisted
        status is ``"checkpointed"``).

        Args:
            session_id: Session to lock.

        Yields:
            The live ``SessionState``, or ``None`` if no live session exists.

        Raises:
            SessionBusyError: If the session has an active run or its
                persisted status is not ``"checkpointed"``.
        """
        resume_lock = await self._get_resume_lock(session_id)
        async with resume_lock:
            session = self.sessions.get_session(session_id)
            if session is not None and session.current_run_id is not None:
                raise SessionBusyError(session_id, session.current_run_id)

            if self.sessions.store is not None:
                current_data = await self.sessions.store.load(session_id)
                if current_data is not None and current_data.status != "checkpointed":
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
        # MCP tools are handled via McpConfigSnapshot → as_capability() →
        # MCPToolset, not through agent.tools.providers.
        if self.pool is not None:
            if self.pool.skills_instruction_provider:
                agent.tools.add_provider(self.pool.skills_instruction_provider)
            agent.tools.add_provider(self.pool.skills_tools_provider)

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
        # MCP tools are handled via McpConfigSnapshot → as_capability() →
        # MCPToolset, not through agent.tools.providers.
        if self.pool is not None:
            if self.pool.skills_instruction_provider:
                agent.tools.add_provider(self.pool.skills_instruction_provider)
            agent.tools.add_provider(self.pool.skills_tools_provider)

        await agent.__aenter__()
        return agent

    async def _resume_native_agent(
        self,
        session_data: SessionData,
        checkpoint: CheckpointData,
        results: Any,
    ) -> None:
        """Resume a native agent from checkpoint with deferred results.

        Loads message_history from checkpoint, reconstructs the agent from its
        original config, and calls agent.run() with the restored history and
        deferred results.

        Args:
            session_data: Persisted session data.
            checkpoint: Checkpoint data with message_history and pending_calls.
            results: DeferredToolResults for resolving pending deferred calls.

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

                agent_tools = await agent.tools.get_tools()
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

        try:
            message_history: list[Any] = list(checkpoint.message_history)
            # deferred_tool_results is forwarded to pydantic-ai Agent.run()
            # which accepts it natively; cast to Any since BaseAgent.run()
            # doesn't declare this kwarg in its signature.
            run_fn: Any = agent.run
            await run_fn(
                message_history=message_history,
                deferred_tool_results=results,
            )
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

    async def resume_session(
        self,
        session_id: str,
        deferred_tool_results: Any,
        *,
        source: str = "resume_prompt",
    ) -> None:
        """Resume a paused session with resolved deferred tool results.

        Loads the persisted SessionData, validates that deferred_tool_results
        cover all pending_deferred_calls (raising CheckpointMismatchError if not),
        and resumes execution via the appropriate path:
        - Native agent: load checkpoint → reconstruct agent from config →
          agent.run(message_history=restored, deferred_tool_results=results)
        - ACP agent: load session data → reopen subprocess →
          agent.run(message_history=restored, deferred_tool_results=results)

        Per-session resume_lock ensures only one resume at a time.
        Emits SessionResumeEvent on success.

        Args:
            session_id: Session to resume.
            deferred_tool_results: Results for pending deferred tool calls
                (DeferredToolResults-compatible object with .calls dict).
            source: Identifier for the entity triggering the resume.

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

        # Fast-path: check for active run in live sessions (before lock).
        # The authoritative check is inside _with_resume_lock, but this
        # early check avoids unnecessary store operations for busy sessions.
        session = self.sessions.get_session(session_id)
        if session is not None and session.current_run_id is not None:
            raise SessionBusyError(session_id, session.current_run_id)

        # Validate deferred_tool_results cover all pending_deferred_calls
        pending_call_ids: set[str] = {call.tool_call_id for call in data.pending_deferred_calls}
        provided_call_ids: set[str] = set(getattr(deferred_tool_results, "calls", {}).keys())

        missing = pending_call_ids - provided_call_ids
        extra = provided_call_ids - pending_call_ids
        if missing or extra:
            raise CheckpointMismatchError(
                session_id=session_id,
                expected=pending_call_ids,
                provided=provided_call_ids,
                missing=missing,
                extra=extra,
            )

        # Determine agent type
        agent_type = data.metadata.get("agent_type", "native")

        # Per-session resume lock with state validation (Decision 8, Task 19)
        async with self._with_resume_lock(session_id) as session:
            try:
                # Load checkpoint data
                checkpoint = await self._load_checkpoint_data(session_id)

                # Mark session as resuming
                data = data.model_copy(update={"status": "resuming"})
                await store.save(data)

                # Route to appropriate resume path
                if agent_type == "acp":
                    await self._resume_acp_agent(data, checkpoint, deferred_tool_results)
                else:
                    await self._resume_native_agent(data, checkpoint, deferred_tool_results)

                # Clear pending_deferred_calls ONLY after agent.run() succeeds (Decision 8)
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
                await self.event_bus.publish(
                    session_id,
                    SessionResumeEvent(
                        session_id=session_id,
                        resolved_call_count=len(pending_call_ids),
                        source=source,
                    ),
                )

                logger.info(
                    "Session resumed successfully",
                    session_id=session_id,
                    agent_type=agent_type,
                    resolved_calls=len(pending_call_ids),
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
        Order: wait for run, session cleanup, event bus, then turn state.

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
                # Without this, a handle stuck in _idle_event.wait() will
                # never exit, causing close_session to hang until timeout.
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

        await self.sessions.close_session(session_id)
        # EventBus and message cache cleanup may be interrupted by
        # CancelledError from garbage-collected async generator cleanup
        # (e.g., when a consumer broke from run_stream without closing
        # the generator). Suppress these spurious cancellations so
        # shutdown proceeds.
        try:
            await self.event_bus.close_session(session_id)
        except asyncio.CancelledError:
            logger.warning(
                "EventBus close_session interrupted by spurious cancellation",
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
    ) -> RunHandle:
        """Create and register a RunHandle without a background task.

        Unlike :meth:`SessionController._start_run_handle`, this does
        NOT create an asyncio task to consume ``start()``. The caller
        is responsible for draining ``start()``.

        Returns:
            The newly created and registered RunHandle.
        """
        event_bus = self.event_bus
        run_ctx = AgentRunContext(session_id=session_id, event_bus=event_bus)
        run_handle = RunHandle(
            run_id=uuid.uuid4().hex,
            session_id=session_id,
            agent_type=agent.AGENT_TYPE,
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )
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
        content: Any,
        priority: str = "when_idle",
        **kwargs: Any,
    ) -> RunHandle | None:
        """Route an incoming request for a session (fire-and-forget).

        Creates a background task that processes the prompt through
        the RunHandle lifecycle. Protocol handlers should subscribe to the
        EventBus *before* calling this method so no events are dropped.

        Idle sessions create a RunHandle, busy sessions call
        ``RunHandle.steer()`` or ``RunHandle.followup()``.

        Args:
            session_id: Target session.
            content: Message / prompt content.
            priority: "when_idle" to queue, "asap" to inject into active turn.
            **kwargs: Additional arguments passed to the turn runner.

        Returns:
            The RunHandle if a new run was started, otherwise None.
        """
        return await self.sessions.receive_request(session_id, content, priority=priority, **kwargs)

    @property
    def active_runs(self) -> list[RunHandle]:
        """Get all currently active (running) RunHandles."""
        return [rh for rh in self.sessions._runs.values() if rh.status == RunStatus.running]

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
        *prompts: str,
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

    async def _run_stream_run_turn(
        self,
        session_id: str,
        *prompts: str,
        scope: str = "session",
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Handle run_stream via the RunHandle path.

        If no active run exists, creates a RunHandle and yields events
        directly from ``start()``. If a run is active, steers the
        message and yields from the EventBus subscription.
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
        # Also subscribe to EventBus so that events published by tools
        # during turn execution (e.g. SpawnSessionStart from task() →
        # create_child_session()) are delivered to the consumer, not
        # just events yielded directly by start().
        run_handle = self._create_run_handle(session, agent, session_id)
        self.event_bus.clear_replay_buffer(session_id)
        bus_queue = await self.event_bus.subscribe(session_id, scope=scope)
        gen = run_handle.start(content)
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

    async def inject_prompt(self, session_id: str, message: str, **kwargs: Any) -> bool:
        """Inject a message into a session.

        If the session has an active run, injects immediately via
        ``RunHandle.steer()``. Otherwise, returns False.

        Does NOT acquire session.turn_lock.

        Args:
            session_id: The session to inject into.
            message: The message to inject.
            **kwargs: Additional arguments passed to the agent run.

        Returns:
            True if injected into active turn, False if queued.
        """
        run_handle = self._get_active_run_handle(session_id)
        if run_handle is not None:
            return run_handle.steer(message)
        return False

    async def queue_prompt(self, session_id: str, *prompts: Any, **kwargs: Any) -> bool:
        """Queue prompts for a session.

        Similar to inject_prompt but for full prompts.
        Does NOT acquire session.turn_lock.

        Args:
            session_id: The session to queue prompts for.
            *prompts: Prompts to queue.
            **kwargs: Additional arguments passed to the agent run.

        Returns:
            True if queued into active turn, False if stored for later.
        """
        run_handle = self._get_active_run_handle(session_id)
        if run_handle is not None:
            message = prompts[0] if prompts else ""
            return run_handle.followup(str(message))
        return False

    async def steer(self, session_id: str, message: str, **kwargs: Any) -> bool:
        """Inject a steer message with agent-type-aware routing.

        Delegates to ``RunHandle.steer()`` when an active run exists.

        Args:
            session_id: Target session.
            message: The steer message to deliver.
            **kwargs: Additional arguments (ignored).

        Returns:
            True if delivered into active turn, False if queued for idle.
        """
        run_handle = self._get_active_run_handle(session_id)
        if run_handle is not None:
            return run_handle.steer(message)
        return False

    async def followup(self, session_id: str, message: str, **kwargs: Any) -> bool:
        """Queue a follow-up message with agent-type-aware routing.

        Delegates to ``RunHandle.followup()`` when an active run exists.

        Args:
            session_id: Target session.
            message: The follow-up message to deliver.
            **kwargs: Additional arguments (ignored).

        Returns:
            True if delivered into active turn, False if queued for idle.
        """
        run_handle = self._get_active_run_handle(session_id)
        if run_handle is not None:
            return run_handle.followup(message)
        return False

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
