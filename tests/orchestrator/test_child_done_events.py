"""Tests for child_done_events processing.

In the per-prompt model, ``_drain_events()`` and the between-turns
idle-loop wait are removed from RunHandle. Background subagent
completion is handled by ``SessionState.steer_from_background_task()``
which routes to the active RunHandle or enqueues to
``SessionState.feedback_queue`` for the next RunHandle.

The original tests covered:
- _drain_events() between-turn waiting (removed)
- child_done_events timeout (removed — no idle loop to block)
- queued_steer_messages becoming next turn prompts (removed —
  routing moves to SessionState)
- _drain_events source code inspections (removed — method deleted)

Background task completion across turn boundaries is now covered by
the per-prompt model tests in ``test_run_handle.py`` and the
SessionState routing tests.
"""

# All tests removed — _drain_events() and between-turn child_done_events
# processing are removed from RunHandle in the per-prompt model.
# Background task routing is handled by SessionState.steer_from_background_task().
