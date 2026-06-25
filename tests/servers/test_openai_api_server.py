"""Unit tests for the OpenAI-compatible API server."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.usage import RunUsage

from agentpool import Agent, AgentPool
from agentpool_server.openai_api_server.server import (
    OpenAIAPIServer,
    _serialize_completion_usage,
)


from collections.abc import AsyncGenerator


@pytest.fixture
async def client() -> AsyncGenerator[TestClient, None]:
    """Create a test client backed by a minimal agent pool with a session pool."""

    def callback(message: str) -> str:
        return f"Echo: {message}"

    agent = Agent.from_callback(name="libarian", callback=callback)
    pool = AgentPool()
    pool.register("libarian", agent)
    async with pool:
        server = OpenAIAPIServer(pool, docs=False)
        yield TestClient(server.app)


@pytest.mark.usefixtures("client")
class TestChatCompletions:
    """Chat completions tests using the client fixture."""

    async def test_chat_completions_requires_authorization_header(self, client: TestClient) -> None:
        """Requests without authorization should be rejected."""

        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "libarian",
                "messages": [{"role": "user", "content": "test"}],
                "stream": False,
            },
        )

        assert response.status_code == 401
        assert response.json() == {"detail": "Missing API key"}

    async def test_chat_completions_accepts_bearer_authorization_header(self, client: TestClient) -> None:
        """Requests with a bearer token should pass auth validation."""

        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer dummy"},
            json={
                "model": "libarian",
                "messages": [{"role": "user", "content": "test"}],
                "stream": False,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "libarian"
        assert data["choices"][0]["message"]["content"] == "Echo: test"


class TestResponses:
    """Responses API tests."""

    async def test_responses_accepts_bearer_authorization_header(self, client: TestClient) -> None:
        """Responses requests with a bearer token should pass auth validation."""

        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer dummy"},
            json={
                "model": "libarian",
                "input": "test",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "libarian"


def test_serialize_completion_usage_converts_runusage_to_dict() -> None:
    """RunUsage should be converted to the OpenAI usage dict shape."""

    usage = RunUsage(input_tokens=11, output_tokens=7, cache_read_tokens=3)

    assert _serialize_completion_usage(usage) == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }
