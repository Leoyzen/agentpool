"""Lifecycle package: types, Protocols, and (later) default implementations.

The lifecycle subsystem provides the six dimensions of the RunLoop:
TriggerSource, Journal, SnapshotStore, CommChannel, EventTransport,
and the RunLoop itself.

This module exports the foundational types and Protocols. Default
implementations (ImmediateTrigger, MemoryJournal, etc.) will be added
in subsequent tasks.
"""

from __future__ import annotations

from agentpool.lifecycle.protocols import (
    CommChannel,
    EventTransport,
    Journal,
    SnapshotStore,
    TriggerSource,
)
from agentpool.lifecycle.types import (
    EventEnvelope,
    Feedback,
    Prompt,
    ResumeResult,
    RunState,
    ToolExecutionRecord,
)

__all__ = [
    "CommChannel",
    "EventEnvelope",
    "EventTransport",
    "Feedback",
    "Journal",
    "Prompt",
    "ResumeResult",
    "RunState",
    "SnapshotStore",
    "ToolExecutionRecord",
    "TriggerSource",
]
