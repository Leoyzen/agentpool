"""Test helpers for mocking EventBus subscriber queues in sync fixtures.

Since EventBus migrated from anyio memory streams to ``asyncio.Queue``,
this helper provides a stand-in that behaves like a shut-down (empty)
queue. Use in fixtures where ``EventBus.subscribe()`` is mocked to return
this object — the consumer loop will immediately see ``QueueShutDown``
and exit gracefully.
"""

from __future__ import annotations

import asyncio
from typing import Any


class EmptyReceiveStream(asyncio.Queue[Any]):
    """A mock queue that immediately signals shutdown (empty/closed).

    Behaves like an ``asyncio.Queue`` that has been shut down:
    ``get()`` and ``get_nowait()`` raise ``QueueShutDown``, and
    ``empty()`` returns ``True``.

    Use in sync fixtures where a real ``asyncio.Queue`` can't be
    fully constructed (no running event loop for ``shutdown()``),
    or to simulate an already-closed EventBus subscription.
    """

    def __init__(self) -> None:
        # Don't call super().__init__ — we override all relevant methods
        # and Queue.__init__ may require an event loop on some Python versions.
        self._shutdown = True

    def get_nowait(self) -> Any:
        """Always raise QueueShutDown — the stream is empty/closed."""
        raise asyncio.QueueShutDown

    async def get(self) -> Any:
        """Always raise QueueShutDown — the stream is empty/closed."""
        raise asyncio.QueueShutDown

    def empty(self) -> bool:
        """Always True — no items will ever be available."""
        return True

    def shutdown(self, immediate: bool = True) -> None:
        """No-op — already shut down."""

    async def aclose(self) -> None:
        """No-op for anyio compatibility."""
