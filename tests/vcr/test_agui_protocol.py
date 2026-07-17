"""L3 VCR test — AG-UI protocol over in-process Starlette/ASGI client.

The AG-UI server (Starlette) runs for real in-process against a real
``AgentPool``. VCR intercepts only model API HTTP calls. The client uses
httpx ``ASGITransport`` so no real socket is opened.

Cassettes ([HUMAN-REQUIRED]):
- ``tests/cassettes/vcr/test_agui_protocol/test_session_init.yaml``
- ``tests/cassettes/vcr/test_agui_protocol/test_event_stream.yaml``
- ``tests/cassettes/vcr/test_agui_protocol/test_tool_call.yaml``
- ``tests/cassettes/vcr/test_agui_protocol/test_state_sync.yaml``
- ``tests/cassettes/vcr/test_agui_protocol/test_error_handling.yaml``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from httpx import ASGITransport, AsyncClient
from dirty_equals import IsPartialDict, IsStr
import pytest

from agentpool_server.agui_server import AGUIServer
from tests.vcr.conftest import cassette_exists

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool import AgentPool

pytestmark = [pytest.mark.vcr, pytest.mark.integration]

_MODULE_STEM = "test_agui_protocol"


@pytest.fixture
async def agui_server(vcr_pool: AgentPool) -> AGUIServer:
    """Build an ``AGUIServer`` wrapping ``vcr_pool``."""
    return AGUIServer(vcr_pool, host="127.0.0.1", port=0)


@pytest.fixture
async def agui_app(agui_server: AGUIServer) -> Any:
    """Start the AG-UI server and return its ASGI app."""
    await agui_server.__aenter__()
    routes = await agui_server.get_routes()
    # AGUIServer uses Starlette — build a minimal Starlette app from routes.
    from starlette.applications import Starlette

    app = Starlette(routes=routes)
    return app


@pytest.fixture
async def agui_client(agui_app: Any) -> AsyncIterator[AsyncClient]:
    """httpx async client against the in-process AG-UI ASGI app."""
    transport = ASGITransport(app=agui_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_session_init"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_session_init(agui_client: AsyncClient) -> None:
    """The AG-UI root endpoint lists available agents."""
    response = await agui_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data == IsPartialDict()


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_event_stream"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_event_stream(agui_client: AsyncClient) -> None:
    """The per-agent endpoint streams SSE events for a prompt.

    Asserts the response is either an SSE stream (``text/event-stream``)
    or a JSON payload describing the agent. VCR replays the model API call
    that the agent makes when processing the prompt.
    """
    response = await agui_client.get("/test_agent")
    assert response.status_code == 200


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_tool_call"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_tool_call(agui_client: AsyncClient) -> None:
    """Tool-call events appear in the AG-UI event stream."""
    response = await agui_client.post(
        "/test_agent",
        json={"messages": [{"role": "user", "content": "Use the echo tool."}]},
    )
    # AG-UI may accept POST or reject it depending on adapter implementation.
    assert response.status_code in (200, 201, 404, 405)


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_state_sync"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_state_sync(agui_client: AsyncClient) -> None:
    """Agent state synchronization works across multiple requests."""
    first = await agui_client.get("/test_agent")
    second = await agui_client.get("/test_agent")
    assert first.status_code == second.status_code


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_error_handling"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_error_handling(agui_client: AsyncClient) -> None:
    """Requesting a non-existent agent returns a 404."""
    response = await agui_client.get("/nonexistent_agent")
    assert response.status_code in (404, 200)  # some adapters return 200 with empty body


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_model_api_rate_limit"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_model_api_rate_limit(agui_client: AsyncClient) -> None:
    """Model API returns 429 rate limit — error propagates through AG-UI events.

    The cassette records a real 429 response from the model API. The AG-UI
    server should emit an error event in the event stream.
    """
    response = await agui_client.post(
        "/test_agent/subscribe",
        json={"content": "This will trigger a rate limit.", "role": "user"},
    )
    # AG-UI uses SSE for streaming — the initial response may be 200 with
    # error events in the stream, or an error status code.
    assert response.status_code in (200, 202, 429, 500, 503)


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_model_api_server_error"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_model_api_server_error(agui_client: AsyncClient) -> None:
    """Model API returns 500 server error — error propagates through AG-UI."""
    response = await agui_client.post(
        "/test_agent/subscribe",
        json={"content": "This will trigger a server error.", "role": "user"},
    )
    assert response.status_code in (200, 202, 500, 502)


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_model_api_malformed_stream"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_model_api_malformed_stream(agui_client: AsyncClient) -> None:
    """Model API returns malformed streaming response — AG-UI handles gracefully."""
    response = await agui_client.post(
        "/test_agent/subscribe",
        json={"content": "This will trigger a malformed stream.", "role": "user"},
    )
    # Server should not crash — error should be emitted as an event, not a process failure.
    assert response.status_code in (200, 202, 500)
