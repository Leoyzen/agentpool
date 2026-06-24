"""Test helpers for mocking anyio memory streams in sync fixtures."""

from __future__ import annotations

import anyio


class EmptyReceiveStream:
    """A mock receive stream that immediately signals EndOfStream.

    Use in sync fixtures where ``anyio.create_memory_object_stream()``
    can't be called (no running event loop).
    """

    async def receive(self) -> anyio.abc.ObjectReceiveStream:
        raise anyio.EndOfStream

    def receive_nowait(self) -> anyio.abc.ObjectReceiveStream:
        raise anyio.WouldBlock

    async def aclose(self) -> None:
        pass

    def __aiter__(self) -> EmptyReceiveStream:
        return self

    async def __anext__(self) -> None:
        raise StopAsyncIteration
