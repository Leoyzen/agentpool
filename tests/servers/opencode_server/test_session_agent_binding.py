"""Regression tests for OpenCode session-level agent binding."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest


if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_session_binds_requested_agent(
    async_client: AsyncClient,
    server_state,
) -> None:
    """`POST /session` should pass the requested agent into SessionPool."""
    reviewer = Mock()
    reviewer.description = "Reviewer"
    server_state.pool.manifest.agents = {
        "test-agent": server_state.agent,
        "rebuttal_agent": reviewer,
    }

    response = await async_client.post(
        "/session",
        json={"title": "Rebuttal Batch", "agent": "rebuttal_agent"},
    )

    assert response.status_code == 200
    server_state.pool.session_pool.create_session.assert_awaited_once()
    assert server_state.pool.session_pool.create_session.await_args.kwargs["agent_name"] == (
        "rebuttal_agent"
    )


@pytest.mark.asyncio
async def test_create_session_rejects_unknown_agent(
    async_client: AsyncClient,
    server_state,
) -> None:
    """Unknown session agents should fail before creating mixed-agent state."""
    server_state.pool.manifest.agents = {"test-agent": server_state.agent}

    response = await async_client.post(
        "/session",
        json={"title": "Bad Agent", "agent": "missing_agent"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown agent: missing_agent"
    server_state.pool.session_pool.create_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_async_inherits_session_bound_agent(
    async_client: AsyncClient,
    server_state,
) -> None:
    """Messages without `agent` should inherit the session-level agent binding."""
    session_response = await async_client.post("/session", json={"title": "Bound"})
    session_id = session_response.json()["id"]
    server_state.pool.session_pool.sessions.get_session.return_value = Mock(
        agent_name="rebuttal_agent"
    )

    receive_calls: list[dict] = []
    original_receive_request = server_state.pool.session_pool.send_message

    async def spy_receive_request(*args, **kwargs):
        receive_calls.append(kwargs)
        return await original_receive_request(*args, **kwargs)

    server_state.pool.session_pool.send_message = spy_receive_request

    response = await async_client.post(
        f"/session/{session_id}/prompt_async",
        json={"parts": [{"type": "text", "text": "review this"}]},
    )

    assert response.status_code == 204
    messages = server_state.messages[session_id]
    assert messages[-1].info.agent == "rebuttal_agent"
    assert receive_calls[0]["session_id"] == session_id
