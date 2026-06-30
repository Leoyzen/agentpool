"""Regression tests for ``PromptInjectionManager``.

Tests cover the full lifecycle of both the inject/consume path
(tool result augmentation) and the queue/pop_queued/flush_pending_to_queue
path (follow-up prompts for ACP agents).

These tests serve as a **behavioral baseline** before the thinning refactor
removes the queue/pop_queued/flush_pending_to_queue methods from the
native agent path.
"""

from __future__ import annotations

import pytest

from agentpool.agents.prompt_injection import PromptInjectionManager


# ---------------------------------------------------------------------------
# inject / consume path
# ---------------------------------------------------------------------------


async def test_inject_then_consume_returns_xml_wrapped():
    """inject() stores message, consume() pops it wrapped in XML tags."""
    mgr = PromptInjectionManager()
    mgr.inject("check tests")

    result = await mgr.consume()
    assert result is not None
    assert "<injected-context>" in result
    assert "check tests" in result
    assert "</injected-context>" in result


async def test_consume_returns_none_when_empty():
    """consume() returns None when no pending injections."""
    mgr = PromptInjectionManager()
    assert await mgr.consume() is None


async def test_consume_is_fifo():
    """Multiple injections are consumed in FIFO order."""
    mgr = PromptInjectionManager()
    mgr.inject("first")
    mgr.inject("second")
    mgr.inject("third")

    r1 = await mgr.consume()
    r2 = await mgr.consume()
    r3 = await mgr.consume()

    assert r1 is not None and "first" in r1
    assert r2 is not None and "second" in r2
    assert r3 is not None and "third" in r3


async def test_consume_all_drains_all():
    """consume_all() drains all pending injections at once."""
    mgr = PromptInjectionManager()
    mgr.inject("a")
    mgr.inject("b")

    results = await mgr.consume_all()
    assert len(results) == 2
    assert "a" in results[0]
    assert "b" in results[1]
    # all drained
    assert not mgr.has_pending()


async def test_consume_all_empty_returns_empty_list():
    """consume_all() returns [] when no pending."""
    mgr = PromptInjectionManager()
    assert await mgr.consume_all() == []


def test_has_pending_true_after_inject():
    """has_pending() returns True after inject()."""
    mgr = PromptInjectionManager()
    assert not mgr.has_pending()
    mgr.inject("x")
    assert mgr.has_pending()


def test_has_pending_false_after_consume():
    """has_pending() returns False after all injections consumed."""
    mgr = PromptInjectionManager()
    mgr.inject("x")
    # consume is async but we can check state before
    assert mgr.has_pending()


# ---------------------------------------------------------------------------
# queue / pop_queued path
# ---------------------------------------------------------------------------


def test_queue_then_pop_returns_prompts():
    """queue() stores prompts, pop_queued() returns them in FIFO order."""
    mgr = PromptInjectionManager()
    mgr.queue("hello", "world")

    result = mgr.pop_queued()
    assert result is not None
    assert result == ("hello", "world")
    assert not mgr.has_queued()


def test_pop_queued_returns_none_when_empty():
    """pop_queued() returns None when queue is empty."""
    mgr = PromptInjectionManager()
    assert mgr.pop_queued() is None


def test_queue_multiple_groups_fifo():
    """Multiple queue() calls produce FIFO order on pop_queued()."""
    mgr = PromptInjectionManager()
    mgr.queue("first")
    mgr.queue("second")
    mgr.queue("third")

    assert mgr.pop_queued() == ("first",)
    assert mgr.pop_queued() == ("second",)
    assert mgr.pop_queued() == ("third",)
    assert mgr.pop_queued() is None


def test_has_queued_true_after_queue():
    """has_queued() returns True after queue()."""
    mgr = PromptInjectionManager()
    assert not mgr.has_queued()
    mgr.queue("x")
    assert mgr.has_queued()


# ---------------------------------------------------------------------------
# insert_queued (front insertion)
# ---------------------------------------------------------------------------


def test_insert_queued_adds_at_front():
    """insert_queued() adds prompts at the front of the queue."""
    mgr = PromptInjectionManager()
    mgr.queue("existing")
    mgr.insert_queued(("priority",))

    # priority should come out first
    assert mgr.pop_queued() == ("priority",)
    assert mgr.pop_queued() == ("existing",)


def test_insert_queued_on_empty_queue():
    """insert_queued() works on an empty queue."""
    mgr = PromptInjectionManager()
    mgr.insert_queued(("first",))
    assert mgr.has_queued()
    assert mgr.pop_queued() == ("first",)


# ---------------------------------------------------------------------------
# flush_pending_to_queue
# ---------------------------------------------------------------------------


def test_flush_moves_injections_to_queue():
    """flush_pending_to_queue() moves unconsumed injections to queued prompts."""
    mgr = PromptInjectionManager()
    mgr.inject("unconsumed1")
    mgr.inject("unconsumed2")

    assert mgr.has_pending()
    assert not mgr.has_queued()

    mgr.flush_pending_to_queue()

    assert not mgr.has_pending()
    assert mgr.has_queued()

    # Each injection becomes a single-element tuple
    p1 = mgr.pop_queued()
    p2 = mgr.pop_queued()
    assert p1 == ("unconsumed1",)
    assert p2 == ("unconsumed2",)


def test_flush_noop_when_no_pending():
    """flush_pending_to_queue() does nothing when no pending injections."""
    mgr = PromptInjectionManager()
    mgr.flush_pending_to_queue()
    assert not mgr.has_queued()


def test_flush_preserves_existing_queue():
    """flush_pending_to_queue() appends after existing queued prompts."""
    mgr = PromptInjectionManager()
    mgr.queue("already_queued")
    mgr.inject("pending")

    mgr.flush_pending_to_queue()

    # existing queue items come first
    assert mgr.pop_queued() == ("already_queued",)
    assert mgr.pop_queued() == ("pending",)


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_clear_removes_everything():
    """clear() removes both pending injections and queued prompts."""
    mgr = PromptInjectionManager()
    mgr.inject("p1")
    mgr.queue("q1")
    mgr.queue("q2")

    mgr.clear()

    assert not mgr.has_pending()
    assert not mgr.has_queued()
    assert mgr.pop_queued() is None


def test_clear_on_empty_manager():
    """clear() is safe on an empty manager."""
    mgr = PromptInjectionManager()
    mgr.clear()  # should not raise
    assert not mgr.has_pending()
    assert not mgr.has_queued()


def test_clear_idempotent():
    """clear() can be called multiple times safely."""
    mgr = PromptInjectionManager()
    mgr.inject("x")
    mgr.clear()
    mgr.clear()  # second call should be noop
    assert not mgr.has_pending()


# ---------------------------------------------------------------------------
# Full lifecycle scenario
# ---------------------------------------------------------------------------


async def test_full_lifecycle_inject_consume_flush_pop():
    """Full lifecycle: inject → consume → flush → pop_queued."""
    mgr = PromptInjectionManager()

    # 1. Inject two messages
    mgr.inject("msg1")
    mgr.inject("msg2")

    # 2. Consume one (simulates tool hook consuming)
    consumed = await mgr.consume()
    assert consumed is not None
    assert "msg1" in consumed

    # 3. Flush remaining unconsumed to queue
    mgr.flush_pending_to_queue()
    assert not mgr.has_pending()
    assert mgr.has_queued()

    # 4. Pop from queue
    popped = mgr.pop_queued()
    assert popped == ("msg2",)

    # 5. Queue empty now
    assert not mgr.has_queued()


async def test_xml_tag_format():
    """Verify the exact XML tag format produced by consume()."""
    mgr = PromptInjectionManager()
    mgr.inject("test content")

    result = await mgr.consume()
    assert result == "<injected-context>\ntest content\n</injected-context>"


async def test_xml_tag_format_consume_all():
    """Verify the exact XML tag format produced by consume_all()."""
    mgr = PromptInjectionManager()
    mgr.inject("a")
    mgr.inject("b")

    results = await mgr.consume_all()
    assert results[0] == "<injected-context>\na\n</injected-context>"
    assert results[1] == "<injected-context>\nb\n</injected-context>"


def test_repr_shows_counts():
    """__repr__ shows pending and queued counts."""
    mgr = PromptInjectionManager()
    mgr.inject("x")
    mgr.queue("y")
    r = repr(mgr)
    assert "pending=1" in r
    assert "queued=1" in r


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
