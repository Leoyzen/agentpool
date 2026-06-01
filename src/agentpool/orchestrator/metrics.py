"""Metrics and observability for the SessionPool orchestration layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentpool.log import get_logger


if TYPE_CHECKING:
    from agentpool.orchestrator.core import SessionPool


logger = get_logger(__name__)


@dataclass
class SessionPoolMetrics:
    """Metrics snapshot for SessionPool observability.

    Attributes:
        active_sessions: Number of currently active sessions.
        active_turns: Number of sessions with a turn currently in progress.
        auto_resume_count: Total number of auto-resume occurrences recorded.
        event_bus_queue_depth: Mapping of session IDs to subscriber counts.
        session_lifetime_seconds: Average lifetime of closed sessions.
        turn_latency_ms: Average turn latency in milliseconds.
    """

    active_sessions: int
    active_turns: int
    auto_resume_count: int
    event_bus_queue_depth: dict[str, int]
    session_lifetime_seconds: float
    turn_latency_ms: float


class MetricsCollector:
    """Collects metrics from a SessionPool instance.

    Provides lock-protected access to session state and turn timings
    to produce a SessionPoolMetrics snapshot.
    """

    def __init__(self, session_pool: SessionPool) -> None:
        """Initialize the metrics collector.

        Args:
            session_pool: The session pool to collect metrics from.
        """
        self.session_pool = session_pool
        self._auto_resume_counter: int = 0

    def record_auto_resume(self) -> None:
        """Record an auto-resume occurrence.

        Called by TurnRunner when an auto-resume iteration is triggered.
        """
        self._auto_resume_counter += 1

    async def get_metrics(self) -> SessionPoolMetrics:
        """Collect a metrics snapshot from the session pool.

        Returns:
            A SessionPoolMetrics instance with current values.
        """
        async with self.session_pool.sessions._lock:
            sessions = dict(self.session_pool.sessions._sessions)

        closed_sessions = [s for s in sessions.values() if s.closed_at is not None]
        if closed_sessions:
            total_lifetime = 0.0
            for s in closed_sessions:
                assert s.closed_at is not None
                total_lifetime += s.closed_at - s.created_at
            avg_session_lifetime = total_lifetime / len(closed_sessions)
        else:
            avg_session_lifetime = 0.0

        turn_timings = self.session_pool.turns._turn_timings
        if turn_timings:
            avg_turn_latency_ms = sum(
                (end - start) * 1000 for start, end in turn_timings
            ) / len(turn_timings)
        else:
            avg_turn_latency_ms = 0.0

        subscriber_counts = await self.session_pool.event_bus.get_subscriber_counts()
        return SessionPoolMetrics(
            active_sessions=len(sessions),
            active_turns=sum(1 for s in sessions.values() if s.turn_lock.locked()),
            auto_resume_count=self._auto_resume_counter,
            event_bus_queue_depth=subscriber_counts,
            session_lifetime_seconds=avg_session_lifetime,
            turn_latency_ms=avg_turn_latency_ms,
        )
