"""ExtensionRegistry — unified capability registry with 4-level scope storage.

Replaces fragmented infrastructure (SkillURIResolver._providers,
AggregatedResourceSource) with a single registry
that supports pool, session, agent, and turn-level capability scoping.

Scope hierarchy (outer → inner):
    POOL → SESSION → AGENT → TURN

Pool-level capabilities are visible to all sessions. Session-level
capabilities are visible only within their session. Agent-level
capabilities are visible only to the named agent. Turn-level capabilities
are visible only for the duration of one turn and are guarded by an
``asyncio.Lock`` for concurrent access.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum, auto
import logging
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai.capabilities import AbstractCapability

    from agentpool.capabilities.change_event import ChangeEvent
    from agentpool.capabilities.resource_protocols import (
        ChangeObservable,
        CommandResource,
        McpResource,
        SkillResource,
    )


logger = logging.getLogger(__name__)

DEFAULT_MAX_COMPOSITION_DEPTH = 3
"""Default maximum composition depth (root-inclusive)."""


class ScopeLevel(Enum):
    """Capability scope level.

    Attributes:
        POOL: Visible to all sessions, agents, and turns.
        SESSION: Visible only within a specific session.
        AGENT: Visible only to a specific named agent.
        TURN: Visible only for the duration of one turn.
    """

    POOL = auto()
    SESSION = auto()
    AGENT = auto()
    TURN = auto()


@dataclass(frozen=True, slots=True)
class Scope:
    """Immutable scope identifying where a capability is visible.

    Attributes:
        level: The scope level (POOL/SESSION/AGENT/TURN).
        session_id: Session identifier (required for SESSION/AGENT/TURN).
        agent_name: Agent name (required for AGENT/TURN).
        turn_id: Turn identifier (required for TURN).
    """

    level: ScopeLevel
    session_id: str = ""
    agent_name: str = ""
    turn_id: str = ""


class CircularCompositionError(Exception):
    """Raised when a capability composition cycle is detected.

    Args:
        message: Description of the cycle.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ExtensionRegistry:
    """Registry for capabilities with 4-level scope storage.

    Capabilities are registered at a specific scope level and are
    visible to all inner scopes. The registry provides typed query
    methods that filter by Resource Protocol type, URI resolution
    by scheme, and change stream merging.

    Turn-level registration is guarded by an ``asyncio.Lock`` to
    prevent concurrent modification. Pool, session, and agent-level
    dicts do not require locking (mutated only at startup/shutdown).

    Composition cycle detection and depth limiting are performed at
    ``add_child()`` registration time.
    """

    def __init__(
        self,
        max_composition_depth: int = DEFAULT_MAX_COMPOSITION_DEPTH,
    ) -> None:
        """Initialize the registry with empty scope storage.

        Args:
            max_composition_depth: Maximum composition depth (root-inclusive).
                When depth exceeds this limit, a warning is logged but
                registration is NOT blocked. Default: 3.
        """
        self._max_composition_depth = max_composition_depth

        # 4-level scope storage
        self._pool: list[AbstractCapability[Any]] = []
        self._session: dict[str, list[AbstractCapability[Any]]] = {}
        self._agent: dict[str, dict[str, list[AbstractCapability[Any]]]] = {}
        # agent: session_id → agent_name → caps
        self._turn: dict[str, dict[str, dict[str, list[AbstractCapability[Any]]]]] = {}
        # turn: session_id → agent_name → turn_id → caps

        # Lock for turn-level mutations
        self._turn_lock = asyncio.Lock()

        # Child tracking for cycle detection and depth limiting
        # Maps id(capability) → list of child capabilities
        self._children: dict[int, list[AbstractCapability[Any]]] = {}

        # Parent tracking for ancestor chain walking
        # Maps id(capability) → parent capability (or None for roots)
        self._parents: dict[int, AbstractCapability[Any] | None] = {}

        # Background tasks for change stream merging
        self._background_tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        capability: AbstractCapability[Any],
        scope: Scope,
    ) -> None:
        """Register a capability at the given scope.

        For turn-level scope, this method acquires the turn lock.
        For other scopes, registration is synchronous (no lock).

        Args:
            capability: The capability to register.
            scope: The scope at which to register it.
        """
        match scope.level:
            case ScopeLevel.POOL:
                self._pool.append(capability)
            case ScopeLevel.SESSION:
                self._session.setdefault(scope.session_id, []).append(capability)
            case ScopeLevel.AGENT:
                self._agent.setdefault(scope.session_id, {}).setdefault(
                    scope.agent_name, []
                ).append(capability)
            case ScopeLevel.TURN:
                # Turn-level registration requires the lock
                # But register() is sync — we need to use the async variant
                # for turn-level. Fall through to a sync approach that's safe
                # because the lock is only needed for concurrent async access.
                self._turn.setdefault(scope.session_id, {}).setdefault(
                    scope.agent_name, {}
                ).setdefault(scope.turn_id, []).append(capability)

    async def register_async(
        self,
        capability: AbstractCapability[Any],
        scope: Scope,
    ) -> None:
        """Register a capability at the given scope (async variant).

        For turn-level scope, this method acquires the turn lock to
        prevent concurrent modification. For other scopes, behaves
        identically to ``register()``.

        Args:
            capability: The capability to register.
            scope: The scope at which to register it.
        """
        if scope.level == ScopeLevel.TURN:
            async with self._turn_lock:
                self._turn.setdefault(scope.session_id, {}).setdefault(
                    scope.agent_name, {}
                ).setdefault(scope.turn_id, []).append(capability)
        else:
            self.register(capability, scope)

    def unregister(  # noqa: PLR0911
        self,
        capability: AbstractCapability[Any],
        scope: Scope,
    ) -> bool:
        """Unregister a capability from the given scope.

        For turn-level scope, use ``unregister_async()`` instead to
        ensure proper locking.

        Args:
            capability: The capability to unregister.
            scope: The scope from which to unregister it.

        Returns:
            True if the capability was found and removed, False otherwise.
        """
        match scope.level:
            case ScopeLevel.POOL:
                if capability in self._pool:
                    self._pool.remove(capability)
                    return True
                return False
            case ScopeLevel.SESSION:
                caps = self._session.get(scope.session_id, [])
                if capability in caps:
                    caps.remove(capability)
                    return True
                return False
            case ScopeLevel.AGENT:
                agent_map = self._agent.get(scope.session_id, {})
                caps = agent_map.get(scope.agent_name, [])
                if capability in caps:
                    caps.remove(capability)
                    return True
                return False
            case ScopeLevel.TURN:
                session_map = self._turn.get(scope.session_id, {})
                agent_map = session_map.get(scope.agent_name, {})
                caps = agent_map.get(scope.turn_id, [])
                if capability in caps:
                    caps.remove(capability)
                    return True
                return False
        return False

    async def unregister_async(
        self,
        capability: AbstractCapability[Any],
        scope: Scope,
    ) -> bool:
        """Unregister a capability from the given scope (async variant).

        For turn-level scope, acquires the turn lock.

        Args:
            capability: The capability to unregister.
            scope: The scope from which to unregister it.

        Returns:
            True if the capability was found and removed, False otherwise.
        """
        if scope.level == ScopeLevel.TURN:
            async with self._turn_lock:
                session_map = self._turn.get(scope.session_id, {})
                agent_map = session_map.get(scope.agent_name, {})
                caps = agent_map.get(scope.turn_id, [])
                if capability in caps:
                    caps.remove(capability)
                    return True
                return False
        return self.unregister(capability, scope)

    def clear_turn(self, session_id: str, agent_name: str, turn_id: str) -> None:
        """Clear all turn-level capabilities for a specific turn.

        Args:
            session_id: Session identifier.
            agent_name: Agent name.
            turn_id: Turn identifier.
        """
        session_map = self._turn.get(session_id, {})
        agent_map = session_map.get(agent_name, {})
        agent_map.pop(turn_id, None)
        if not agent_map:
            session_map.pop(agent_name, None)
        if not session_map:
            self._turn.pop(session_id, None)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_visible_capabilities(
        self,
        scope: Scope,
    ) -> list[AbstractCapability[Any]]:
        """Get all capabilities visible at the given scope.

        Walks the scope hierarchy from pool → session → agent → turn,
        collecting all capabilities at each level.

        Args:
            scope: The scope to query.

        Returns:
            List of visible capabilities (pool + matching session +
            matching agent + matching turn).
        """
        result: list[AbstractCapability[Any]] = list(self._pool)

        if scope.level.value >= ScopeLevel.SESSION.value:
            result.extend(self._session.get(scope.session_id, []))

        if scope.level.value >= ScopeLevel.AGENT.value:
            agent_map = self._agent.get(scope.session_id, {})
            result.extend(agent_map.get(scope.agent_name, []))

        if scope.level.value >= ScopeLevel.TURN.value:
            session_map = self._turn.get(scope.session_id, {})
            agent_map = session_map.get(scope.agent_name, {})
            result.extend(agent_map.get(scope.turn_id, []))

        return result

    def get_skill_resources(
        self,
        scope: Scope,
    ) -> list[SkillResource]:
        """Get visible capabilities implementing ``SkillResource``.

        Args:
            scope: The scope to query.

        Returns:
            List of capabilities implementing ``SkillResource``.
        """
        from agentpool.capabilities.resource_protocols import SkillResource

        return [
            cap for cap in self.get_visible_capabilities(scope) if isinstance(cap, SkillResource)
        ]

    def get_mcp_resources(
        self,
        scope: Scope,
    ) -> list[McpResource]:
        """Get visible capabilities implementing ``McpResource``.

        Args:
            scope: The scope to query.

        Returns:
            List of capabilities implementing ``McpResource``.
        """
        from agentpool.capabilities.resource_protocols import McpResource

        return [cap for cap in self.get_visible_capabilities(scope) if isinstance(cap, McpResource)]

    def get_command_resources(
        self,
        scope: Scope,
    ) -> list[CommandResource]:
        """Get visible capabilities implementing ``CommandResource``.

        Args:
            scope: The scope to query.

        Returns:
            List of capabilities implementing ``CommandResource``.
        """
        from agentpool.capabilities.resource_protocols import CommandResource

        return [
            cap for cap in self.get_visible_capabilities(scope) if isinstance(cap, CommandResource)
        ]

    def get_observable_capabilities(
        self,
        scope: Scope,
    ) -> list[ChangeObservable]:
        """Get visible capabilities implementing ``ChangeObservable``.

        Args:
            scope: The scope to query.

        Returns:
            List of capabilities implementing ``ChangeObservable``.
        """
        from agentpool.capabilities.resource_protocols import ChangeObservable

        return [
            cap for cap in self.get_visible_capabilities(scope) if isinstance(cap, ChangeObservable)
        ]

    # ------------------------------------------------------------------
    # URI Resolution
    # ------------------------------------------------------------------

    async def resolve_uri(
        self,
        uri: str,
        scope: Scope,
    ) -> str | bytes | None:
        """Resolve a URI by routing based on scheme.

        ``skill://`` URIs are resolved by querying
        ``get_skill_resources(scope)`` and calling ``read_skill()``.
        ``mcp://`` URIs are resolved by querying
        ``get_mcp_resources(scope)`` and calling ``read_resource()``.
        Unknown schemes return ``None``.

        Uses ``skill_exists()`` / ``resource_exists()`` for a
        cheap-check-first pattern before reading content.

        Args:
            uri: The URI to resolve.
            scope: The scope to query for capabilities.

        Returns:
            The resolved content as string or bytes, or ``None`` if
            the URI cannot be resolved.
        """
        if uri.startswith("skill://"):
            # Flat URI (D9): parse via ResolvedSkillURI for consistency.
            from agentpool.skills.uri_resolver import ResolvedSkillURI

            resolved = ResolvedSkillURI.parse(uri)
            skill_name = resolved.skill_name

            for cap in self.get_skill_resources(scope):
                try:
                    if await cap.skill_exists(skill_name):
                        content = await cap.read_skill(skill_name)
                        if content is not None:
                            return content
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Failed to resolve skill URI %r via %s",
                        uri,
                        type(cap).__name__,
                        exc_info=True,
                    )
            return None

        if uri.startswith("mcp://"):
            for mcp_cap in self.get_mcp_resources(scope):
                try:
                    if await mcp_cap.resource_exists(uri):
                        mcp_content: str | bytes | None = await mcp_cap.read_resource(uri)
                        if mcp_content is not None:
                            return mcp_content
                except Exception:  # noqa: BLE001  # noqa: BLE001
                    logger.warning(
                        "Failed to resolve MCP URI %r via %s",
                        uri,
                        type(mcp_cap).__name__,
                        exc_info=True,
                    )
            return None

        # Unknown scheme
        return None

    # ------------------------------------------------------------------
    # Change Stream Merging
    # ------------------------------------------------------------------

    def merge_change_streams(
        self,
        scope: Scope,
    ) -> AsyncIterator[ChangeEvent] | None:
        """Merge ``on_change()`` streams from all visible observables.

        Uses a sentinel-based merge pattern: each source stream is
        consumed in a separate task. When a source completes (returns
        ``None`` or raises), a sentinel is pushed. The merge completes
        when all sources have sent sentinels.

        Exceptions in individual streams are logged via
        ``logger.warning()`` and not propagated.

        Args:
            scope: The scope to query for observables.

        Returns:
            An ``AsyncIterator[ChangeEvent]`` if any observables are
            visible, ``None`` if no observables are found.
        """
        observables = self.get_observable_capabilities(scope)
        if not observables:
            return None

        # Collect active streams
        streams: list[AsyncIterator[ChangeEvent]] = []
        for cap in observables:
            stream = cap.on_change()
            if stream is not None:
                streams.append(stream)

        if not streams:
            return None

        return self._merge_streams(streams)

    async def _merge_streams(
        self,
        streams: list[AsyncIterator[ChangeEvent]],
    ) -> AsyncIterator[ChangeEvent]:
        """Merge multiple change event streams into one.

        Args:
            streams: List of async iterators to merge.

        Yields:
            ``ChangeEvent`` instances from all streams.
        """
        queue: asyncio.Queue[ChangeEvent | None] = asyncio.Queue()
        tasks: list[asyncio.Task[None]] = []
        num_streams = len(streams)

        async def _consume(stream: AsyncIterator[ChangeEvent]) -> None:
            try:
                async for event in stream:
                    await queue.put(event)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Change stream consumer encountered an error",
                    exc_info=True,
                )
            finally:
                await queue.put(None)

        tasks = [asyncio.create_task(_consume(s)) for s in streams]
        for task in tasks:
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        finished_count = 0
        try:
            while finished_count < num_streams:
                event = await queue.get()
                if event is None:
                    finished_count += 1
                    continue
                yield event
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Composition: Cycle Detection & Depth Limit
    # ------------------------------------------------------------------

    def add_child(
        self,
        parent: AbstractCapability[Any],
        child: AbstractCapability[Any],
    ) -> None:
        """Add a child capability to a parent, with cycle detection.

        Checks the ancestor chain of ``parent`` for ``child``. If
        ``child`` already appears in its own ancestor chain, a
        ``CircularCompositionError`` is raised.

        Also checks composition depth. If depth exceeds
        ``max_composition_depth``, a warning is logged but registration
        is NOT blocked.

        Args:
            parent: The parent capability.
            child: The child capability to add.

        Raises:
            CircularCompositionError: If adding ``child`` would create
                a cycle in the composition graph.
        """
        # Cycle detection: walk ancestor chain of parent
        current: AbstractCapability[Any] | None = parent
        visited: set[int] = set()
        depth = 1  # root-inclusive

        while current is not None:
            current_id = id(current)
            if current_id in visited:
                # Cycle in existing chain (shouldn't happen, but guard)
                parent_name = type(parent).__name__
                msg = f"Circular composition detected: cycle in ancestor chain of {parent_name}"
                raise CircularCompositionError(msg)
            visited.add(current_id)

            if current_id == id(child):
                msg = (
                    f"Circular composition detected: "
                    f"{type(child).__name__} already appears in the "
                    f"ancestor chain of {type(parent).__name__}"
                )
                raise CircularCompositionError(msg)

            depth += 1
            current = self._parents.get(current_id)

        # Depth limit check (warning, not block)
        if depth > self._max_composition_depth:
            logger.warning(
                "Composition depth %d exceeds limit %d for %s → %s",
                depth,
                self._max_composition_depth,
                type(parent).__name__,
                type(child).__name__,
            )

        # Record the relationship
        self._children.setdefault(id(parent), []).append(child)
        self._parents[id(child)] = parent

    def get_children(
        self,
        capability: AbstractCapability[Any],
    ) -> list[AbstractCapability[Any]]:
        """Get the children of a capability.

        Args:
            capability: The capability to query.

        Returns:
            List of child capabilities, or empty list if none.
        """
        return list(self._children.get(id(capability), []))

    def get_depth(
        self,
        capability: AbstractCapability[Any],
    ) -> int:
        """Get the composition depth of a capability.

        Root capabilities have depth 1 (root-inclusive).

        Args:
            capability: The capability to query.

        Returns:
            The depth (1 for roots, 2 for children, etc.).
        """
        depth = 1
        current: AbstractCapability[Any] | None = self._parents.get(id(capability))
        visited: set[int] = {id(capability)}
        while current is not None:
            current_id = id(current)
            if current_id in visited:
                break
            visited.add(current_id)
            depth += 1
            current = self._parents.get(current_id)
        return depth
