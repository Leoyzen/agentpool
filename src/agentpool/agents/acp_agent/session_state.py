"""ACP Agent - Session state tracking."""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import TYPE_CHECKING

from agentpool.log import get_logger


if TYPE_CHECKING:
    from acp.schema import (
        AvailableCommandsUpdate,
        SessionConfigOption,
        SessionModelState,
        SessionModeState,
        SessionUpdate,
    )

logger = get_logger(__name__)

PROTOCOL_VERSION = 1


@dataclass
class ACPState:
    """Tracks state of an ACP session.

    Preserves model/mode/config/commands state for the UI layer.
    Stream data is pushed directly to an async queue (not stored here).
    """

    session_id: str
    """The session ID from the ACP server."""

    current_model_id: str | None = None
    """Current model ID from session state."""

    models: SessionModelState | None = None
    """Full model state including available models."""

    modes: SessionModeState | None = None
    """Full mode state including available modes."""

    current_mode_id: str | None = None
    """Current mode ID."""

    config_options: list[SessionConfigOption] = dataclass_field(default_factory=list)
    """Unified session config options (replaces modes/models in newer ACP versions)."""

    available_commands: AvailableCommandsUpdate | None = None
    """Available commands from the agent."""

    is_loading: bool = False
    """Flag indicating session is being loaded (collecting updates for replay)."""

    _load_updates: list[SessionUpdate] = dataclass_field(default_factory=list)
    """Separate list for collecting updates during load (not consumed by streaming)."""

    def clear(self) -> None:
        """Clear state for a new prompt turn."""
        # Note: Don't clear session_id, current_model_id, models, config_options -
        # those persist across turns

    def start_load(self) -> None:
        """Start collecting updates for session load."""
        self.is_loading = True
        self._load_updates.clear()

    def finish_load(self) -> list[SessionUpdate]:
        """Finish loading and return collected updates."""
        self.is_loading = False
        updates = list(self._load_updates)
        self._load_updates.clear()
        return updates
