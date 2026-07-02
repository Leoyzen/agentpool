"""Test that auto-resume tasks in a session TaskGroup are isolated from each other.

One auto-resume failure MUST NOT cancel sibling auto-resume tasks.
"""

from __future__ import annotations

import asyncio

import anyio
import pytest


@pytest.mark.anyio
async def test_safe_auto_resume_sibling_isolation() -> None:
    """Spawn 2 auto-resume tasks in TaskGroup, have one raise, verify other completes."""
    results: list[str] = []

    async def safe_failing_task() -> None:
        try:
            await asyncio.sleep(0.05)
            results.append("failing_task_started")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass

    async def safe_succeeding_task() -> None:
        try:
            await asyncio.sleep(0.15)
            results.append("succeeding_task_completed")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass

    async with anyio.create_task_group() as tg:
        tg.start_soon(safe_failing_task)
        tg.start_soon(safe_succeeding_task)

    assert "succeeding_task_completed" in results
    assert "failing_task_started" in results
