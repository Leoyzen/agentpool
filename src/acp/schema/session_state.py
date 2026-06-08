"""Session state schema definitions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field

from acp.schema.base import AnnotatedObject


# Type aliases for config option identifiers
SessionConfigId = str
"""Unique identifier for a configuration option."""

SessionConfigValueId = str
"""Unique identifier for a possible value within a configuration option."""

SessionConfigGroupId = str
"""Unique identifier for a group of values within a configuration option."""


SessionConfigOptionCategory = Literal["mode", "model", "thought_level", "other"]
"""**UNSTABLE**: This capability is not part of the spec yet.

Semantic category for a session configuration option.

This is intended to help Clients distinguish broadly common selectors (e.g. model selector vs
session mode selector vs thought/reasoning level) for UX purposes (keyboard shortcuts, icons,
placement). It MUST NOT be required for correctness. Clients MUST handle missing or unknown
categories gracefully (treat as `other`).

Values:
    - "mode": Session mode selector
    - "model": Model selector
    - "thought_level": Thought/reasoning level selector
    - "other": Unknown / uncategorized selector
"""


class ModelInfo(AnnotatedObject):
    """**UNSTABLE**: This capability is not part of the spec yet.

    Information about a selectable model.
    """

    description: str | None = None
    """Optional description of the model."""

    model_id: str
    """Unique identifier for the model."""

    name: str
    """Human-readable name of the model."""


class SessionModelState(AnnotatedObject):
    """**UNSTABLE**: This capability is not part of the spec yet.

    The set of models and the one currently active.
    """

    available_models: Sequence[ModelInfo]
    """The set of models that the Agent can use."""

    current_model_id: str
    """The current model the Agent is using."""


class SessionMode(AnnotatedObject):
    """A mode the agent can operate in.

    See protocol docs: [Session Modes](https://agentclientprotocol.com/protocol/session-modes)
    """

    description: str | None = None
    """Optional description of the mode."""

    id: str
    """Unique identifier for the mode."""

    name: str
    """Human-readable name of the mode."""


class SessionModeState(AnnotatedObject):
    """The set of modes and the one currently active."""

    available_modes: Sequence[SessionMode]
    """The set of modes that the Agent can operate in."""

    current_mode_id: str
    """The current mode the Agent is in."""


class SessionInfo(AnnotatedObject):
    """**UNSTABLE**: This capability is not part of the spec yet.

    Information about a session returned by session/list.
    """

    cwd: str
    """The working directory for this session. Must be an absolute path."""

    session_id: str
    """Unique identifier for the session."""

    title: str | None = None
    """Human-readable title for the session."""

    updated_at: str | None = None
    """ISO 8601 timestamp of last activity."""

    meta: dict[str, Any] | None = None
    """Arbitrary session metadata."""

    parent_session_id: str | None = None
    """ID of the parent session if this session was spawned as a subagent."""

    child_session_ids: Sequence[str] | None = None
    """IDs of child sessions spawned from this session."""

    depth: int | None = Field(default=None, ge=0)
    """Nesting depth in the session hierarchy (0 for root sessions)."""


class SubagentCapabilities(AnnotatedObject):
    """Capabilities of an available subagent."""

    streaming: bool | None = False
    """Whether the subagent supports streaming updates."""

    tools: bool | None = False
    """Whether the subagent can use tools."""

    delegation: bool | None = False
    """Whether the subagent can delegate to other subagents."""

    prompt_delegation: bool | None = False
    """Whether the subagent supports prompt delegation (Phase 2)."""

    background: bool | None = False
    """Whether the subagent supports background execution (Phase 2)."""


class SubagentInfo(AnnotatedObject):
    """Information about an available subagent for delegation.

    Advertised during session lifecycle so clients know which subagents
    can be invoked.
    """

    subagent_id: str
    """Unique identifier for the subagent."""

    name: str
    """Human-readable name of the subagent."""

    description: str | None = None
    """Optional description of the subagent."""

    capabilities: SubagentCapabilities | None = None
    """Capabilities of the subagent."""


class SessionConfigSelectOption(AnnotatedObject):
    """A possible value for a configuration selector."""

    value: SessionConfigValueId
    """Unique identifier for this option value."""

    name: str
    """Human-readable label for this option value."""

    description: str | None = None
    """Optional description for this option value."""


class SessionConfigSelectGroup(AnnotatedObject):
    """A group of possible values for a configuration selector."""

    group: SessionConfigGroupId
    """Unique identifier for this group."""

    name: str
    """Human-readable label for this group."""

    options: Sequence[SessionConfigSelectOption]
    """The set of option values in this group."""


SessionConfigSelectOptions = (
    Sequence[SessionConfigSelectOption] | Sequence[SessionConfigSelectGroup]
)
"""The possible values for a configuration selector, optionally organized into groups."""


class SessionConfigSelect(AnnotatedObject):
    """A single-value selector (dropdown) session configuration option payload."""

    current_value: SessionConfigValueId
    """The currently selected value."""

    options: SessionConfigSelectOptions
    """The set of selectable options."""


class SessionConfigKind(AnnotatedObject):
    """Type-specific session configuration option payload."""

    type: Literal["select"] = Field(default="select", init=False)
    """Discriminator for the config option type."""

    # Flattened SessionConfigSelect fields
    current_value: SessionConfigValueId
    """The currently selected value."""

    options: SessionConfigSelectOptions
    """The set of selectable options."""


class SessionConfigOption(AnnotatedObject):
    """A session configuration option selector and its current state."""

    id: SessionConfigId
    """Unique identifier for the configuration option."""

    name: str
    """Human-readable label for the option."""

    description: str | None = None
    """Optional description for the Client to display to the user."""

    category: SessionConfigOptionCategory | None = None
    """Optional semantic category for this option (UX only)."""

    type: Literal["select"] = Field(default="select", init=False)
    """Discriminator for the config option type (flattened from kind)."""

    current_value: SessionConfigValueId
    """The currently selected value (flattened from kind.select)."""

    options: SessionConfigSelectOptions
    """The set of selectable options (flattened from kind.select)."""
