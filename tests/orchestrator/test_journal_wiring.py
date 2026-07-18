"""Regression test: RunHandle._journal must be the same instance as
CommChannel._journal to ensure crash recovery sees all events.

When ``RunHandle`` is constructed via the ``SessionPool`` or
``SessionController`` paths, a ``ProtocolChannel`` is created with a
fresh ``MemoryJournal``. If ``RunHandle._journal`` is a *different*
instance, ``journal.resume()`` reads from an empty journal while
``publish()`` writes to the CommChannel's journal — making crash
recovery silently ineffective for protocol server sessions.

These tests verify the three construction paths:

1. **DirectChannel default path** — ``__post_init__`` creates
   ``DirectChannel(self._journal)`` so the same instance is shared.
2. **ProtocolChannel explicit path** — caller passes both
   ``_comm_channel`` and ``_journal`` (the corrected construction
   sites in ``session_pool_runs.py`` and ``session_controller_runs.py``).
3. **ProtocolChannel reuse path** — caller passes only
   ``_comm_channel``; ``__post_init__`` reuses the CommChannel's
   journal via the ``journal`` property (defensive guard).
"""

from __future__ import annotations

from typing import Any

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.lifecycle import DirectChannel, MemoryJournal, ProtocolChannel
from agentpool.orchestrator.event_bus import EventBus
from agentpool.orchestrator.run import RunHandle
from agentpool.orchestrator.session_controller import SessionState


pytestmark = pytest.mark.unit


def _make_minimal_run_handle(**overrides: Any) -> RunHandle:
    """Create a RunHandle with minimal required fields for testing.

    Args:
        **overrides: Extra keyword arguments forwarded to ``RunHandle``.

    Returns:
        A ``RunHandle`` instance with ``__post_init__`` already applied.
    """
    run_ctx = AgentRunContext(session_id="test", event_bus=EventBus(), deps=None)
    return RunHandle(
        run_id="test-run",
        session_id="test-session",
        agent_type="native",
        agent=None,
        event_bus=EventBus(),
        session=SessionState(session_id="test", agent_name="test"),
        run_ctx=run_ctx,
        **overrides,
    )


def test_direct_channel_journal_shared_with_run_handle() -> None:
    """When _comm_channel is None, RunHandle creates DirectChannel(self._journal) — same instance."""
    handle = _make_minimal_run_handle()
    assert handle._journal is not None
    assert handle._comm_channel is not None
    assert isinstance(handle._comm_channel, DirectChannel)
    assert handle._journal is handle._comm_channel.journal


def test_protocol_channel_journal_shared_with_run_handle() -> None:
    """When _comm_channel is provided with a journal, RunHandle._journal must be the same instance."""
    journal = MemoryJournal()
    event_bus = EventBus()
    comm_channel = ProtocolChannel(
        journal=journal,
        event_bus=event_bus,
        session_id="test",
    )
    handle = _make_minimal_run_handle(
        _comm_channel=comm_channel,
        _journal=journal,
    )
    assert handle._journal is journal
    assert isinstance(handle._comm_channel, ProtocolChannel)
    assert handle._comm_channel.journal is journal
    assert handle._journal is handle._comm_channel.journal


def test_protocol_channel_journal_reused_when_journal_not_passed() -> None:
    """When _comm_channel is provided but _journal is None, __post_init__ reuses CommChannel's journal.

    This is the defensive guard that prevents future construction sites
    from reintroducing the bug: even if a caller forgets to pass
    ``_journal``, ``__post_init__`` reuses the CommChannel's journal
    instead of creating a fresh empty one.
    """
    journal = MemoryJournal()
    event_bus = EventBus()
    comm_channel = ProtocolChannel(
        journal=journal,
        event_bus=event_bus,
        session_id="test",
    )
    handle = _make_minimal_run_handle(_comm_channel=comm_channel)
    # __post_init__ should have reused the CommChannel's journal.
    assert handle._journal is journal
    assert isinstance(handle._comm_channel, ProtocolChannel)
    assert handle._journal is handle._comm_channel.journal
