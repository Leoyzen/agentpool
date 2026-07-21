"""Tests for journal instance consistency.

In the per-prompt model, lifecycle dimensions (Journal, CommChannel,
EventTransport, TriggerSource) are owned by ``SessionState``, not
``RunHandle``. The ``__post_init__`` dimension initialization that
previously wired ``RunHandle._journal`` to ``CommChannel._journal``
is removed — dimensions are passed from SessionState at RunHandle
creation.

The original tests verified:
1. DirectChannel default path: ``__post_init__`` creates
   ``DirectChannel(self._journal)`` (removed — __post_init__ no longer
   initializes dimensions)
2. ProtocolChannel explicit path: both ``_comm_channel`` and
   ``_journal`` passed to RunHandle (removed — these fields are on
   SessionState)
3. ProtocolChannel reuse path: ``__post_init__`` reuses CommChannel's
   journal (removed — no __post_init__ dimension init)

Dimension ownership is now verified by:
- ``test_lifecycle_dimensions_not_closed_when_run_handle_terminates``
  in ``test_run_handle.py``
"""

# All tests removed — _journal and _comm_channel moved from RunHandle
# to SessionState in the per-prompt migration.
