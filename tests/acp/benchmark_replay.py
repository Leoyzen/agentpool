"""Benchmark: batch vs sequential replay performance."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

from pydantic_ai import ModelRequest, ModelResponse, TextPart, UserPromptPart

from acp.agent.notifications import ACPNotifications


def make_messages(count: int) -> list[ModelRequest | ModelResponse]:
    msgs: list[ModelRequest | ModelResponse] = []
    for i in range(count):
        if i % 2 == 0:
            msgs.append(ModelRequest(parts=[UserPromptPart(content=f"User message {i}")]))
        else:
            msgs.append(ModelResponse(parts=[TextPart(content=f"Agent response {i}")]))
    return msgs


async def benchmark_sequential(count: int) -> float:
    client = AsyncMock()
    client.session_update = AsyncMock()
    client.ext_notification = AsyncMock()
    n = ACPNotifications(client=client, session_id="bench")
    msgs = make_messages(count)
    start = time.perf_counter()
    await n.replay(msgs)
    return time.perf_counter() - start


async def benchmark_batch(count: int) -> float:
    client = AsyncMock()
    client.session_update = AsyncMock()
    client.ext_notification = AsyncMock()
    n = ACPNotifications(client=client, session_id="bench")
    n.set_batch_support(True)
    msgs = make_messages(count)
    start = time.perf_counter()
    await n.replay(msgs)
    return time.perf_counter() - start


async def main() -> None:
    for count in [50, 100, 200]:
        seq = await benchmark_sequential(count)
        bat = await benchmark_batch(count)
        reduction = ((seq - bat) / seq * 100) if seq > 0 else 0
        print(
            f"{count:4d} msgs | sequential: {seq:.4f}s | batch: {bat:.4f}s"
            f" | reduction: {reduction:.1f}%"
        )


if __name__ == "__main__":
    asyncio.run(main())
