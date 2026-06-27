"""Tests for PromptInjectionManager deprecation warnings.

``queue()``, ``pop_queued()``, and ``flush_pending_to_queue()`` emit
``DeprecationWarning`` directing users toward ``RunHandle.followup()``.
``inject()`` and ``consume()`` must NOT emit any warning.
"""

from __future__ import annotations

import warnings

import pytest

from agentpool.agents.prompt_injection import PromptInjectionManager


# ---------------------------------------------------------------------------
# queue()
# ---------------------------------------------------------------------------


def test_queue_emits_deprecation_warning() -> None:
    """queue() emits DeprecationWarning with followup message."""
    mgr = PromptInjectionManager()

    with pytest.warns(DeprecationWarning, match="RunHandle.followup"):
        mgr.queue("test prompt")


def test_queue_still_functions_after_warning() -> None:
    """queue() still appends to queued prompts after emitting warning."""
    mgr = PromptInjectionManager()

    with pytest.warns(DeprecationWarning, match="RunHandle.followup"):
        mgr.queue("hello", "world")

    result = mgr.pop_queued()
    assert result == ("hello", "world")


# ---------------------------------------------------------------------------
# pop_queued()
# ---------------------------------------------------------------------------


def test_pop_queued_emits_deprecation_warning() -> None:
    """pop_queued() emits DeprecationWarning with followup message."""
    mgr = PromptInjectionManager()
    mgr.queue("test prompt")

    with pytest.warns(DeprecationWarning, match="RunHandle.followup"):
        mgr.pop_queued()


def test_pop_queued_still_functions_after_warning() -> None:
    """pop_queued() still returns the correct item after emitting warning."""
    mgr = PromptInjectionManager()
    mgr.queue("first", "second")

    with pytest.warns(DeprecationWarning, match="RunHandle.followup"):
        result = mgr.pop_queued()

    assert result == ("first", "second")
    assert mgr.has_queued() is False


# ---------------------------------------------------------------------------
# flush_pending_to_queue()
# ---------------------------------------------------------------------------


def test_flush_pending_to_queue_emits_deprecation_warning() -> None:
    """flush_pending_to_queue() emits DeprecationWarning with followup message."""
    mgr = PromptInjectionManager()
    mgr.inject("unconsumed injection")

    with pytest.warns(DeprecationWarning, match="RunHandle.followup"):
        mgr.flush_pending_to_queue()


def test_flush_pending_to_queue_still_functions_after_warning() -> None:
    """flush_pending_to_queue() still moves pending injections after warning."""
    mgr = PromptInjectionManager()
    mgr.inject("unconsumed injection")

    with pytest.warns(DeprecationWarning, match="RunHandle.followup"):
        mgr.flush_pending_to_queue()

    assert mgr.has_pending() is False
    assert mgr.has_queued() is True
    result = mgr.pop_queued()
    assert result == ("unconsumed injection",)


# ---------------------------------------------------------------------------
# inject() — NO deprecation warning
# ---------------------------------------------------------------------------


def test_inject_does_not_emit_deprecation_warning() -> None:
    """inject() does NOT emit DeprecationWarning."""
    mgr = PromptInjectionManager()

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        # Should complete without raising DeprecationWarning
        mgr.inject("test message for injection")

    assert mgr.has_pending() is True


# ---------------------------------------------------------------------------
# consume() — NO deprecation warning
# ---------------------------------------------------------------------------


def test_consume_does_not_emit_deprecation_warning() -> None:
    """consume() does NOT emit DeprecationWarning."""
    mgr = PromptInjectionManager()
    mgr.inject("test message")

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        # Should complete without raising DeprecationWarning
        import anyio

        result = anyio.run(mgr.consume)

    assert result is not None
    assert "test message" in result
