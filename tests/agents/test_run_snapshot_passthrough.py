"""Regression test: BaseAgent.run() must accept snapshot kwarg.

init_session() in the OpenCode server calls agent.run(..., snapshot=snapshot).
Before the fix, BaseAgent.run() lacked the `snapshot` parameter, causing
``TypeError: unexpected keyword argument 'snapshot'`` at runtime.

This test ensures the parameter is present and passed through to run_stream().
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import pytest

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.context import RunSnapshot


if TYPE_CHECKING:
    pass


def test_run_accepts_snapshot_parameter() -> None:
    """BaseAgent.run() must accept a snapshot keyword argument.

    This is the direct regression test: if the parameter is missing from
    the signature, calling agent.run(snapshot=...) would raise TypeError.
    """
    sig = inspect.signature(BaseAgent.run)
    assert "snapshot" in sig.parameters, (
        "BaseAgent.run() must accept 'snapshot' parameter for init_session compatibility"
    )
    param = sig.parameters["snapshot"]
    assert param.default is not inspect.Parameter.empty, (
        "snapshot parameter must have a default value for backward compatibility"
    )
    annotation = param.annotation
    # Accept both "RunSnapshot | None" and "Optional[RunSnapshot]" string forms
    assert "RunSnapshot" in str(annotation), (
        f"snapshot parameter must be typed as RunSnapshot | None, got: {annotation}"
    )


def test_run_snapshot_default_is_none() -> None:
    """snapshot parameter defaults to None for backward compatibility."""
    sig = inspect.signature(BaseAgent.run)
    param = sig.parameters["snapshot"]
    assert param.default is None, (
        f"snapshot default must be None for backward compat, got: {param.default}"
    )


def test_run_signature_matches_run_stream_for_snapshot() -> None:
    """Both run() and run_stream() must expose the same snapshot parameter type."""
    run_sig = inspect.signature(BaseAgent.run)
    stream_sig = inspect.signature(BaseAgent.run_stream)
    run_snapshot = run_sig.parameters["snapshot"]
    stream_snapshot = stream_sig.parameters["snapshot"]
    assert str(run_snapshot.annotation) == str(stream_snapshot.annotation), (
        f"run() snapshot annotation ({run_snapshot.annotation}) must match "
        f"run_stream() snapshot annotation ({stream_snapshot.annotation})"
    )


@pytest.mark.asyncio
async def test_run_delegates_snapshot_to_run_stream() -> None:
    """BaseAgent.run() must pass snapshot through to run_stream().

    Verifies the internal delegation: when run() is called with snapshot,
    it must forward that value to run_stream() rather than dropping it.
    """
    # We inspect the source to verify the delegation — this is more reliable
    # than trying to instantiate a full agent for a signature passthrough test.
    source = inspect.getsource(BaseAgent.run)
    # The run() method should pass snapshot=snapshot in the run_stream() call
    assert "snapshot=snapshot" in source, (
        "BaseAgent.run() must delegate snapshot to run_stream() via 'snapshot=snapshot'"
    )


def test_run_snapshot_type_annotation() -> None:
    """snapshot parameter must accept RunSnapshot instances."""
    # Verify RunSnapshot is constructible and the type is correct
    snapshot = RunSnapshot(session_id="test-session-123")
    assert snapshot.session_id == "test-session-123"
    # The parameter annotation should accept this type
    sig = inspect.signature(BaseAgent.run)
    param = sig.parameters["snapshot"]
    # Check "None" appears in the annotation (optional parameter)
    assert "None" in str(param.annotation), (
        f"snapshot must be optional (None allowed), got: {param.annotation}"
    )
