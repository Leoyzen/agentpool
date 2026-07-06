"""SessionPool orchestration layer for agent session management.

Modules:
- event_bus: EventBus, EventEnvelope, drain_and_merge
- session_controller: SessionController, SessionState, exceptions
- session_pool: SessionPool
- run: RunHandle, RunStatus
- metrics: MetricsCollector, SessionPoolMetrics
- runtime_registry: RuntimeAgentRegistry
"""

from __future__ import annotations

from agentpool.orchestrator.event_bus import (
    DEFAULT_QUEUE_MAXSIZE,
    EventEnvelope,
    EventBus,
    drain_and_merge,
)
from agentpool.orchestrator.metrics import MetricsCollector, SessionPoolMetrics
from agentpool.orchestrator.run import RunHandle, RunStatus
from agentpool.orchestrator.runtime_registry import RuntimeAgentRegistry
from agentpool.orchestrator.session_controller import (
    DEFAULT_SESSION_TTL_SECONDS,
    CheckpointMismatchError,
    SessionBusyError,
    SessionController,
    SessionLifecyclePolicy,
    SessionNotFoundError,
    SessionState,
)
from agentpool.orchestrator.session_pool import (
    DEFAULT_MAX_AUTO_RESUME,
    SessionPool,
)

__all__ = [
    "DEFAULT_MAX_AUTO_RESUME",
    "DEFAULT_QUEUE_MAXSIZE",
    "DEFAULT_SESSION_TTL_SECONDS",
    "CheckpointMismatchError",
    "EventBus",
    "EventEnvelope",
    "MetricsCollector",
    "RunHandle",
    "RunStatus",
    "RuntimeAgentRegistry",
    "SessionBusyError",
    "SessionController",
    "SessionLifecyclePolicy",
    "SessionNotFoundError",
    "SessionPool",
    "SessionPoolMetrics",
    "SessionState",
    "drain_and_merge",
]
