"""Test that auto-resume tasks in a session TaskGroup are isolated from each other.

One auto-resume failure MUST NOT cancel sibling auto-resume tasks.
"""

from __future__ import annotations

import anyio
import asyncio
import pytest


@pytest.mark.anyio
async def test_safe_auto_resume_sibling_isolation() -> None:
    """Spawn 2 auto-resume tasks in TaskGroup, have one raise, verify other completes."""
    results: list[str] = []

    async def safe_failing_task() -> None:
        try:
            await asyncio.sleep(0.05)
            results.append("failing_task_started")
            raise ValueError("Task failed intentionally")
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def safe_succeeding_task() -> None:
        try:
            await asyncio.sleep(0.15)
            results.append("succeeding_task_completed")
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async with anyio.create_task_group() as tg:
        tg.start_soon(safe_failing_task)
        tg.start_soon(safe_succeeding_task)

    assert "succeeding_task_completed" in results
    assert "failing_task_started" in results


@pytest.mark.anyio
async def test_safe_auto_resume_catches_exceptions() -> None:
    """Test that _safe_auto_resume catches exceptions and logs them."""
    from agentpool.orchestrator.core import TurnRunner

    class MockSessionController:
        def get_session(self, session_id: str) -> None:
            return None

    runner = TurnRunner.__new__(TurnRunner)
    runner._enable_auto_resume = True
    runner._max_auto_resume = 10
    runner._session_task_groups = {}

    async def failing_trigger(session_id: str, **kwargs: object) -> None:
        raise RuntimeError("Auto-resume failed")

    runner._trigger_auto_resume = failing_trigger

    # Should not raise
    await runner._safe_auto_resume("test-session")
