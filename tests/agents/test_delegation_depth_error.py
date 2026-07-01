"""Tests for DelegationDepthError and MAX_DELEGATION_DEPTH primitives."""

from __future__ import annotations

import pytest

from agentpool.agents.exceptions import MAX_DELEGATION_DEPTH, DelegationDepthError


def test_max_delegation_depth_value() -> None:
    """MAX_DELEGATION_DEPTH should be 10."""
    assert MAX_DELEGATION_DEPTH == 10
    assert isinstance(MAX_DELEGATION_DEPTH, int)


def test_delegation_depth_error_is_runtime_error() -> None:
    """DelegationDepthError should be a RuntimeError subclass."""
    assert issubclass(DelegationDepthError, RuntimeError)


def test_delegation_depth_error_message() -> None:
    """DelegationDepthError message should contain depth info."""
    err = DelegationDepthError(current_depth=11)
    assert "11" in str(err)
    assert "10" in str(err)


def test_delegation_depth_error_custom_max_depth() -> None:
    """DelegationDepthError should accept a custom max_depth."""
    err = DelegationDepthError(current_depth=5, max_depth=3)
    assert "5" in str(err)
    assert "3" in str(err)


def test_delegation_depth_error_attributes() -> None:
    """DelegationDepthError should store current_depth and max_depth."""
    err = DelegationDepthError(current_depth=15, max_depth=10)
    assert err.current_depth == 15
    assert err.max_depth == 10


def test_delegation_depth_error_default_max_depth() -> None:
    """DelegationDepthError should default max_depth to MAX_DELEGATION_DEPTH."""
    err = DelegationDepthError(current_depth=12)
    assert err.max_depth == MAX_DELEGATION_DEPTH


def test_delegation_depth_error_raises() -> None:
    """DelegationDepthError should be raisable and catchable."""
    with pytest.raises(DelegationDepthError, match="exceeds maximum"):
        raise DelegationDepthError(current_depth=20)


def test_delegation_depth_error_catchable_as_runtime_error() -> None:
    """DelegationDepthError should be catchable as RuntimeError."""
    with pytest.raises(RuntimeError):
        raise DelegationDepthError(current_depth=11)


def test_import_from_agents_init() -> None:
    """DelegationDepthError and MAX_DELEGATION_DEPTH should be importable from agents package."""
    from agentpool.agents import MAX_DELEGATION_DEPTH as MDD, DelegationDepthError as DDE

    assert DDE is DelegationDepthError
    assert MDD is MAX_DELEGATION_DEPTH
