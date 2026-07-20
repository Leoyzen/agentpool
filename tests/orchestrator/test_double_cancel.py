"""Double-cancel regression tests for the per-prompt RunHandle.

In the per-prompt model, the idle-loop double-cancel bug is eliminated
by construction — there is no idle loop to kill. Cancel idempotency
is tested in ``test_run_handle.py::test_cancel_called_twice_on_running_run_handle``.

The original tests covered:
- _force_cancelling flag (removed — no idle generator to force-cancel)
- _turn_complete_event (removed — use complete_event)
- followup() (removed — routing moves to SessionState.prompt_queue)
- _idle_loop / _idle_event (removed — no idle loop)
- _closed / _closing (removed — use complete_event.is_set())
- _run_state / RunState (removed — use complete_event.is_set())

These scenarios are now covered by:
- test_cancel_called_twice_on_running_run_handle (double cancel idempotency)
- test_cancel_is_idempotent_when_complete (cancel on completed handle)
- test_close_is_idempotent (double close)
"""

# All tests removed — functionality tested here was for the old
# persistent-generator model with idle loop. The per-prompt model
# eliminates these bugs by construction.
