"""Factory function for creating lifecycle dimensions from config.

Maps a ``LifecycleConfig`` to concrete implementations of the five
lifecycle dimensions (TriggerSource, Journal, SnapshotStore,
CommChannel, EventTransport). Returns default in-memory implementations
when the config is ``None`` or all-defaults.

Usage::

    from agentpool.lifecycle.factory import create_dimensions

    trigger, journal, snapshot, comm, transport = create_dimensions(
        lifecycle_config,
        session_id="my_session",
    )
    run_handle = RunHandle(
        run_id="run1",
        session_id="my_session",
        agent_type="native",
        _trigger_source=trigger,
        _journal=journal,
        _snapshot_store=snapshot,
        _comm_channel=comm,
        _event_transport=transport,
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentpool.lifecycle.comm_channel import DirectChannel
from agentpool.lifecycle.event_transport import InProcessTransport
from agentpool.lifecycle.journal import DurableJournal, MemoryJournal
from agentpool.lifecycle.snapshot_store import (
    DurableSnapshotStore,
    MemorySnapshotStore,
)


if TYPE_CHECKING:
    from agentpool.lifecycle.protocols import (
        CommChannel,
        EventTransport,
        Journal,
        SnapshotStore,
        TriggerSource,
    )
    from agentpool_config.lifecycle import LifecycleConfig


def create_dimensions(
    lifecycle_config: LifecycleConfig | None,
    session_id: str,
) -> tuple[
    TriggerSource | None,
    Journal | None,
    SnapshotStore | None,
    CommChannel | None,
    EventTransport | None,
]:
    """Create lifecycle dimensions from a LifecycleConfig.

    Maps the config's fields to concrete implementations:

    - **journal**: ``"memory"`` → ``MemoryJournal``;
      ``"durable"`` → ``DurableJournal`` with a SQLite DB path
      derived from ``session_id``.
    - **snapshot**: ``"memory"`` → ``MemorySnapshotStore``;
      ``"durable"`` → ``DurableSnapshotStore`` with a SQLite DB
      path derived from ``session_id``.
    - **comm_channel**: Always ``DirectChannel(journal)``.
    - **event_transport**: Always ``InProcessTransport()``.
    - **trigger_source**: Always ``None`` — the caller (RunHandle)
      creates an ``ImmediateTrigger`` with the actual prompt in
      ``__post_init__`` when ``_trigger_source`` is ``None``.

    When ``lifecycle_config`` is ``None`` or all fields are default
    (``"memory"``, ``"memory"``, ``"mark_interrupted"``), all
    values are ``None`` so that ``RunHandle.__post_init__`` creates
    in-memory defaults.

    Args:
        lifecycle_config: The lifecycle configuration, or ``None``
            for all-defaults.
        session_id: Session identifier used to derive durable DB
            file paths (e.g. ``"lifecycle_{session_id}.db"``).

    Returns:
        Tuple of ``(trigger_source, journal, snapshot_store,
        comm_channel, event_transport)``. Any element may be
        ``None`` to signal that ``RunHandle.__post_init__`` should
        create the default.
    """
    # When config is None or all-defaults, return None for all
    # dimensions. RunHandle.__post_init__ will create defaults.
    if lifecycle_config is None or lifecycle_config.is_all_defaults():
        return (None, None, None, None, None)

    # Create Journal based on config.
    if lifecycle_config.journal == "durable":
        journal: Journal = DurableJournal(f"sqlite:///lifecycle_{session_id}.db")
    else:
        journal = MemoryJournal()

    # Create SnapshotStore based on config.
    if lifecycle_config.snapshot == "durable":
        snapshot_store: SnapshotStore = DurableSnapshotStore(
            f"lifecycle_{session_id}_snapshot.db",
        )
    else:
        snapshot_store = MemorySnapshotStore()

    # CommChannel always wraps the journal.
    comm_channel: CommChannel = DirectChannel(journal)

    # EventTransport is always in-process for M2.
    event_transport: EventTransport = InProcessTransport()

    # TriggerSource is always None — RunHandle.__post_init__ creates
    # an ImmediateTrigger("") when _trigger_source is None. The actual
    # prompt is passed to start() as initial_prompt.
    trigger_source: TriggerSource | None = None

    return (trigger_source, journal, snapshot_store, comm_channel, event_transport)


__all__ = ["create_dimensions"]
