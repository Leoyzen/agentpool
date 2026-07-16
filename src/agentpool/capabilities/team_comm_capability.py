"""TeamCommCapability — skeleton capability for dynamic team communication.

This capability provides the protocol instructions and (in future tasks T7/T8)
team communication tools (send_message, task_create, read_blackboard, etc.)
to agents that are members of or leads of a dynamic team.

At the skeleton stage (T6), ``get_tools()`` returns an empty list — no
actual tool functions are registered yet. The ``get_instructions()``
method renders the ``protocol_template`` from :class:`TeamModeConfig`
using session metadata (team_name, team_role, team_member_name).

Per-session instantiation:
    The factory creates a shared instance with ``session_metadata=None``
    during ``_compile_agent_capabilities()``. When a session with a
    ``team_id`` in its metadata is created, ``create_session_agent()``
    replaces the shared instance with a per-session instance carrying
    the actual session metadata. This two-phase approach ensures that
    the capability is registered at compile time but only produces
    meaningful instructions when actual team session context exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from agentpool.capabilities.function_toolset import FunctionToolsetCapability


if TYPE_CHECKING:
    from collections.abc import Sequence

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
    ) -> None:
        """Initialize the team communication capability.

        Args:
            config: The resolved team mode configuration (global + agent overlay).
            agent_name: Name of the agent this capability belongs to.
            session_metadata: Optional per-session metadata containing
                ``team_name``, ``team_role``, ``team_member_name``, etc.
                When ``None`` or empty, ``get_instructions()`` returns ``None``.
        """
        super().__init__(name="team_comm")
        self._config = config
        self._agent_name = agent_name
        self._session_metadata: dict[str, Any] = session_metadata or {}

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
        return self._config.protocol_template.format(
            team_name=self._session_metadata.get("team_name", "unknown"),
            role=self._session_metadata.get("team_role", "unknown"),
            member_name=self._session_metadata.get(
                "team_member_name",
                self._agent_name,
            ),
        )

    @override
    async def get_tools(self) -> Sequence[Tool[Any]]:
        """Return the list of team communication tools.

        Returns an empty list when ``config.enabled`` is ``False``.
        At the skeleton stage (T6), no tools are registered yet —
        T7 and T8 will add ``send_message``, ``task_create``, etc.
        """
        if not self._config.enabled:
            return []
        return self._tools
