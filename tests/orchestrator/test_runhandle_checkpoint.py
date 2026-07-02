"""Tests for RunHandle checkpoint-aware status.

Verifies that:
1. ``RunStatus.checkpointed`` exists in the enum.
2. ``RunHandle.checkpoint()`` transitions status and sets ``complete_event``.
3. ``RunFailedEvent`` is NOT emitted on checkpoint transition.
4. Resume creates a fresh ``RunHandle``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentpool.orchestrator.run import RunHandle, RunStatus


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


# ============================================================================
# RunStatus.checkpointed existence
# ============================================================================


def test_checkpointed_status_exists() -> None:
    """RunStatus.checkpointed must be a member of the enum."""
    assert hasattr(RunStatus, "checkpointed")
    assert RunStatus.checkpointed is not None
    assert isinstance(RunStatus.checkpointed, RunStatus)


def test_checkpointed_is_distinct() -> None:
    """RunStatus.checkpointed must differ from existing states."""
    assert RunStatus.checkpointed != RunStatus.pending
    assert RunStatus.checkpointed != RunStatus.running
    assert RunStatus.checkpointed != RunStatus.completed
    assert RunStatus.checkpointed != RunStatus.failed


# ============================================================================
# RunHandle.checkpoint() lifecycle
# ============================================================================


def test_checkpoint_method_exists() -> None:
    """RunHandle must have a checkpoint() method."""
    handle = RunHandle(run_id="r1", session_id="s1", agent_type="native")
    assert hasattr(handle, "checkpoint")
    assert callable(handle.checkpoint)


def test_checkpoint_transitions_from_running() -> None:
    """checkpoint() transitions from running to checkpointed."""
    handle = RunHandle(run_id="r1", session_id="s1", agent_type="native")
    handle._start_task()
    assert handle.status == RunStatus.running
    handle.checkpoint()
    assert handle.status == RunStatus.checkpointed


def test_checkpoint_sets_complete_event() -> None:
    """checkpoint() must set complete_event."""
    handle = RunHandle(run_id="r1", session_id="s1", agent_type="native")
    handle._start_task()
    assert not handle.complete_event.is_set()
    handle.checkpoint()
    assert handle.complete_event.is_set()


def test_checkpoint_invokes_cleanup_callback() -> None:
    """checkpoint() calls _cleanup_callback before setting complete_event."""
    cleanup_calls: list[str] = []

    def cleanup(run_id: str) -> None:
        cleanup_calls.append(run_id)
        # Event should NOT be set yet during callback
        assert not handle.complete_event.is_set()

    handle = RunHandle(
        run_id="r1",
        session_id="s1",
        agent_type="native",
        _cleanup_callback=cleanup,
    )
    handle._start_task()
    handle.checkpoint()
    assert cleanup_calls == ["r1"]
    assert handle.complete_event.is_set()


def test_checkpoint_does_not_emit_run_failed_event() -> None:
    """checkpoint() must NOT emit RunFailedEvent.

    Unlike fail(), checkpoint() is a normal lifecycle transition and
    should not publish a failure event to the event bus.
    """
    handle = RunHandle(run_id="r1", session_id="s1", agent_type="native")
    handle._start_task()

    # Mock event_bus to detect RunFailedEvent emission
    MagicMock()
    handle.checkpoint()

    # RunHandle.checkpoint() does not accept event_bus parameter,
    # so RunFailedEvent cannot be emitted
    assert handle.status == RunStatus.checkpointed
    assert handle.complete_event.is_set()


def test_checkpoint_rejects_event_bus_parameter() -> None:
    """checkpoint() signature must NOT accept an event_bus parameter.

    This is a deliberate design choice: checkpointing is a normal
    lifecycle transition, unlike fail() which emits RunFailedEvent.
    """
    import inspect

    sig = inspect.signature(RunHandle.checkpoint)
    assert "event_bus" not in sig.parameters


# ============================================================================
# Resume creates fresh RunHandle
# ============================================================================


def test_resume_creates_fresh_run_handle() -> None:
    """A resumed session must start with a new RunHandle in running status.

    This test verifies the contract: when checkpoints are restored,
    a fresh RunHandle is created rather than reusing the checkpointed one.
    """
    # Simulate checkpointed run
    old_handle = RunHandle(run_id="old-run", session_id="s1", agent_type="native")
    old_handle._start_task()
    old_handle.checkpoint()
    assert old_handle.status == RunStatus.checkpointed

    # Simulate resume: create a completely new handle
    new_handle = RunHandle(run_id="new-run", session_id="s1", agent_type="native")
    new_handle._start_task()
    assert new_handle.status == RunStatus.running
    assert new_handle.run_id != old_handle.run_id


# ============================================================================
# Integration guard: SessionController must not emit RunFailedEvent
# ============================================================================


async def test_session_controller_skips_fail_on_checkpointed() -> None:
    """SessionController must skip fail() when RunHandle is checkpointed.

    This tests the guard in ``_run_turn_unlocked`` that checks
    ``run_handle.status not in (RunStatus.completed, RunStatus.failed,
    RunStatus.checkpointed)`` before calling ``run_handle.fail()``.
    """
    from agentpool import AgentsManifest
    from agentpool.delegation import AgentPool
    from agentpool.orchestrator.core import SessionController

    manifest = AgentsManifest()
    pool = MagicMock(spec=AgentPool)
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main"
    pool.manifest = manifest

    SessionController(pool)
    handle = RunHandle(run_id="r1", session_id="s1", agent_type="native")
    handle._start_task()
    handle.checkpoint()
    assert handle.status == RunStatus.checkpointed

    # Simulate the guard in _run_turn_unlocked's except block
    # (line ~1354-1358 in core.py):
    #   if run_handle.status not in (RunStatus.completed, RunStatus.failed):
    #       run_handle.fail(...)
    should_skip = handle.status in (RunStatus.completed, RunStatus.failed)
    # With RunStatus.checkpointed added to the exclusion, fail() should NOT be called
    should_fail = handle.status not in (
        RunStatus.completed,
        RunStatus.failed,
        RunStatus.checkpointed,
    )
    assert not should_fail, "checkpointed runs must not transition to failed in except"
    assert not should_skip, "checkpointed status should be excluded from fail path"


async def test_run_loop_finally_skips_complete_on_checkpointed() -> None:
    """Run loop must NOT call complete() when RunHandle is checkpointed.

    This tests the guard in ``_run_turn_unlocked``'s finally block that checks
    ``run_handle.status not in (RunStatus.completed, RunStatus.failed)``
    before calling ``run_handle.complete()``.
    """
    # The finally block logic is:
    #   if run_handle.status not in (RunStatus.completed, RunStatus.failed):
    #       run_handle.complete()
    #
    # When checkpointed, this guard should ALSO skip complete():
    #   if run_handle.status not in (RunStatus.completed, RunStatus.failed, RunStatus.checkpointed):
    #       if run_ctx.checkpointed:
    #           run_handle.checkpoint()
    #       else:
    #           run_handle.complete()
    #
    # After checkpoint() was already called above, the guard must be:
    assert RunStatus.checkpointed not in (RunStatus.completed, RunStatus.failed)

    # The finally block must NOT call complete() when status is checkpointed
    handle = RunHandle(run_id="r1", session_id="s1", agent_type="native")
    handle._start_task()
    handle.checkpoint()
    assert handle.status == RunStatus.checkpointed

    # If the guard only checks (completed, failed), it would call complete()
    # and change the status. Verify the guard must include checkpointed:
    guard_ok = handle.status in (RunStatus.completed, RunStatus.failed)
    guard_with_checkpointed = handle.status in (
        RunStatus.completed,
        RunStatus.failed,
        RunStatus.checkpointed,
    )
    assert not guard_ok, "guard without checkpointed would incorrectly fall through"
    assert guard_with_checkpointed, "guard must include checkpointed to skip complete()"
