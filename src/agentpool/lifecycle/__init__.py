"""Lifecycle package: types, Protocols, and default implementations.

The lifecycle subsystem provides the six dimensions of the RunLoop:
TriggerSource, Journal, SnapshotStore, CommChannel, EventTransport,
and the RunLoop itself.

This module exports the foundational types, Protocols, TriggerSource
implementations, and SnapshotStore implementations. Additional default
implementations (MemoryJournal, CommChannel, etc.) will be added in
subsequent tasks.
"""

from __future__ import annotations

from agentpool.lifecycle.comm_channel import DirectChannel, ProtocolChannel
from agentpool.lifecycle.event_transport import InProcessTransport
from agentpool.lifecycle.factory import create_dimensions
from agentpool.lifecycle.journal import DurableJournal, MemoryJournal
from agentpool.lifecycle.protocols import (
    CommChannel,
    EventTransport,
    Journal,
    SnapshotStore,
    TriggerSource,
)
from agentpool.lifecycle.snapshot_store import (
    DurableSnapshotStore,
    MemorySnapshotStore,
)
from agentpool.lifecycle.triggers import (
    ChannelTrigger,
    ImmediateTrigger,
    ProtocolTrigger,
    ScheduledTrigger,
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
    "ChannelTrigger",
    "CommChannel",
    "DirectChannel",
    "DurableJournal",
    "DurableSnapshotStore",
    "EventEnvelope",
    "EventTransport",
    "Feedback",
    "ImmediateTrigger",
    "InProcessTransport",
    "Journal",
    "MemoryJournal",
    "MemorySnapshotStore",
    "Prompt",
    "ProtocolChannel",
    "ProtocolTrigger",
    "ResumeResult",
    "RunState",
    "ScheduledTrigger",
    "SnapshotStore",
    "ToolExecutionRecord",
    "TriggerSource",
    "create_dimensions",
]
