"""Tests for the RunStatus enum in agentpool.orchestrator.run."""

from __future__ import annotations

from enum import Enum

import pytest

from agentpool.orchestrator.run import RunStatus


@pytest.mark.unit
def test_run_status_enum_values() -> None:
    """Given the RunStatus enum, it should define exactly 7 lifecycle states."""
    expected_values: set[str] = {
        "pending",
        "running",
        "completed",
        "failed",
        "checkpointed",
        "idle",
        "done",
    }
    actual_values: set[str] = {m.name for m in RunStatus}
    assert actual_values == expected_values


@pytest.mark.unit
def test_run_status_is_enum() -> None:
    """Given RunStatus, it should be a proper Enum subclass."""
    assert issubclass(RunStatus, Enum)


@pytest.mark.unit
def test_run_status_idle_and_done_are_distinct() -> None:
    """Given RunStatus has idle and done, they should be distinct values from all existing states."""
    assert RunStatus.idle is not RunStatus.done
    assert RunStatus.idle is not RunStatus.pending
    assert RunStatus.idle is not RunStatus.running
    assert RunStatus.idle is not RunStatus.completed
    assert RunStatus.idle is not RunStatus.failed
    assert RunStatus.idle is not RunStatus.checkpointed
    assert RunStatus.done is not RunStatus.pending
    assert RunStatus.done is not RunStatus.running
    assert RunStatus.done is not RunStatus.completed
    assert RunStatus.done is not RunStatus.failed
    assert RunStatus.done is not RunStatus.checkpointed


@pytest.mark.unit
def test_run_status_idle_name() -> None:
    """Given the idle member, its name should be 'idle'."""
    assert RunStatus.idle.name == "idle"


@pytest.mark.unit
def test_run_status_done_name() -> None:
    """Given the done member, its name should be 'done'."""
    assert RunStatus.done.name == "done"
