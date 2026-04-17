"""Regression tests for async prompt handling in OpenCode server."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from agentpool_server.opencode_server.models import MessageRequest, TextPartInput


class TestPromptAsync:
    """Tests for `/prompt_async` session serialization."""

    @pytest.mark.asyncio
    async def test_prompt_async_marks_busy_before_scheduling(
        self,
        async_client,
        server_state,
    ) -> None:
        """The first async prompt should lock the session before scheduling work."""
        response = await async_client.post("/session", json={"title": "Async Lock"})
        session_id = response.json()["id"]

        background_calls: list[str | None] = []

        def fake_create_background_task(coro, *, name=None):
            background_calls.append(name)
            coro.close()
            return Mock()

        server_state.create_background_task = Mock(side_effect=fake_create_background_task)
        server_state.agent.agent_pool.all_agents = {"default": server_state.agent}
        server_state.agent.queue_prompt = Mock()

        request = MessageRequest(
            parts=[TextPartInput(text="first")],
            agent="default",
            message_id="msg-1",
        )
        response = await async_client.post(
            f"/session/{session_id}/prompt_async",
            json=request.model_dump(mode="json"),
        )
        assert response.status_code == 204
        assert server_state.session_status[session_id].type == "busy"
        assert server_state.create_background_task.call_count == 1

        second_request = MessageRequest(
            parts=[TextPartInput(text="second")],
            agent="default",
            message_id="msg-2",
        )
        response = await async_client.post(
            f"/session/{session_id}/prompt_async",
            json=second_request.model_dump(mode="json"),
        )
        assert response.status_code == 204
        assert server_state.create_background_task.call_count == 1
        server_state.agent.queue_prompt.assert_called_once()
        assert background_calls == [f"process_message_{session_id}"]
