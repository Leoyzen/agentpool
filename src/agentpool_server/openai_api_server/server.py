"""OpenAI-compatible API server for AgentPool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import anyenv
from fastapi import Header

from agentpool.log import get_logger
from agentpool.orchestrator.core import EventEnvelope
from agentpool_server import BaseServer
from agentpool_server.mixins import ProtocolEventConsumerMixin
from agentpool_server.openai_api_server.completions.helpers import stream_response
from agentpool_server.openai_api_server.completions.models import (
    ChatCompletionRequest,
    CompletionUsage,
    ChatCompletionResponse,
    Choice,
    OpenAIMessage,
    OpenAIModelInfo,
)
from agentpool_server.openai_api_server.responses.helpers import handle_request
from agentpool_server.openai_api_server.responses.models import (
    Response as ResponsesResponse,
    ResponseRequest,
)


if TYPE_CHECKING:
    from fastapi import Response

    from agentpool import AgentPool
    from agentpool.orchestrator.core import EventBus
logger = get_logger(__name__)


def _serialize_completion_usage(token_usage: Any | None) -> CompletionUsage | None:
    """Convert AgentPool token usage objects into OpenAI response format."""
    if token_usage is None:
        return None

    return CompletionUsage(
        input_tokens=token_usage.input_tokens,
        output_tokens=token_usage.output_tokens,
        total_tokens=token_usage.total_tokens,
    )


class OpenAIAPIServer(BaseServer, ProtocolEventConsumerMixin):
    """OpenAI-compatible API server backed by AgentPool.

    Provides both chat completions and responses endpoints.
    """

    @property
    def event_bus(self) -> EventBus:
        """Return the EventBus instance to subscribe to."""
        session_pool = self.pool.session_pool
        if session_pool is None:
            raise RuntimeError("SessionPool not available")
        return session_pool.event_bus

    def _get_subscription_scope(self) -> str:
        """Return the EventBus subscription scope.

        Returns:
            The subscription scope string.
        """
        return "session"

    async def _handle_event(self, session_id: str, envelope: EventEnvelope) -> None:
        """Handle a single event from the EventBus.

        The OpenAI API server is stateless and does not maintain
        persistent session connections, so this is a no-op.

        Args:
            session_id: The session whose consumer received the event.
            envelope: The event envelope to handle.
        """

    async def _on_spawn_session_start(self, session_id: str, envelope: EventEnvelope) -> None:
        """Start child consumer when a SpawnSessionStart event is received.

        Args:
            session_id: The session whose consumer received the event.
            envelope: The event envelope containing the spawn session start event.
        """
        event = envelope.event
        if hasattr(event, "child_session_id"):
            await self.start_event_consumer(event.child_session_id)

    def __init__(
        self,
        pool: AgentPool,
        *,
        name: str | None = None,
        host: str = "0.0.0.0",
        port: int = 8000,
        cors: bool = True,
        docs: bool = True,
        api_key: str | None = None,
        raise_exceptions: bool = False,
    ) -> None:
        """Initialize OpenAI-compatible server.

        Args:
            pool: AgentPool containing available agents
            name: Optional Server name (auto-generated if None)
            host: Host to bind server to
            port: Port to bind server to
            cors: Whether to enable CORS middleware
            docs: Whether to enable API documentation endpoints
            api_key: Optional API key for authentication
            raise_exceptions: Whether to raise exceptions during server start
        """
        ProtocolEventConsumerMixin.__init__(self)
        super().__init__(pool, name=name, raise_exceptions=raise_exceptions)
        self.host = host
        self.port = port
        self.api_key = api_key
        from fastapi import Depends, FastAPI
        import logfire

        self.app = FastAPI()
        logfire.instrument_fastapi(self.app)

        if cors:
            from fastapi.middleware.cors import CORSMiddleware

            self.app.add_middleware(
                CORSMiddleware,  # ty: ignore[invalid-argument-type]
                allow_origins=["*"],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

        if not docs:
            self.app.docs_url = None
            self.app.redoc_url = None

        # Add routes with authentication dependency
        dep = Depends(self.verify_api_key)
        self.app.get("/v1/models")(self.list_models)
        self.app.post("/v1/chat/completions", dependencies=[dep], response_model=None)(
            self.create_chat_completion
        )
        self.app.post("/v1/responses", dependencies=[dep])(self.create_response)

    def verify_api_key(
        self, authorization: Annotated[str | None, Header(alias="Authorization")] = None
    ) -> None:
        """Verify API key if configured."""
        from fastapi import HTTPException

        if not authorization:
            raise HTTPException(401, "Missing API key")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Invalid authorization format")
        if self.api_key and authorization != f"Bearer {self.api_key}":
            raise HTTPException(401, "Invalid API key")

    async def list_models(self) -> dict[str, Any]:
        """List available agents as models."""
        models = []
        for name, agent in self.pool.all_agents.items():
            info = OpenAIModelInfo(id=name, created=0, description=agent.description)
            models.append(info)
        return {"object": "list", "data": models}

    async def create_chat_completion(self, request: ChatCompletionRequest) -> Response:
        """Handle chat completion requests."""
        from fastapi import HTTPException, Response
        from fastapi.responses import StreamingResponse

        try:
            agent = self.pool.all_agents[request.model]
        except KeyError:
            raise HTTPException(404, f"Model {request.model} not found") from None

        # Just take the last message content - let agent handle history
        content = request.messages[-1].content or ""
        # TEMP: log the full prompt for debugging batch tests
        logger.info("create_chat_completion model=%s prompt:\n%s", request.model, content)

        # Each HTTP request is an independent task — use a fresh MessageHistory
        # to prevent context accumulation across requests on the shared agent.
        from agentpool.talk.message_history import MessageHistory

        request_history = MessageHistory()

        if request.stream:
            return StreamingResponse(
                stream_response(agent, content, request, message_history=request_history),
                media_type="text/event-stream",
            )
        try:
            response = await agent.run(content, message_history=request_history)
            message = OpenAIMessage(role="assistant", content=str(response.content))
            completion_response = ChatCompletionResponse(
                id=response.message_id,
                created=int(response.timestamp.timestamp()),
                model=request.model,
                choices=[Choice(message=message)],
                usage=_serialize_completion_usage(
                    response.cost_info.token_usage if response.cost_info else None
                ),
            )
            json = completion_response.model_dump_json()
            return Response(content=json, media_type="application/json")
        except Exception as e:
            self.log.exception("Error processing chat completion")
            raise HTTPException(500, f"Error: {e!s}") from e

    async def create_response(self, req_body: ResponseRequest) -> ResponsesResponse:
        """Handle response creation requests."""
        from fastapi import HTTPException

        try:
            agent = self.pool.all_agents[req_body.model]
            return await handle_request(req_body, agent)
        except KeyError:
            raise HTTPException(404, f"Model {req_body.model} not found") from None
        except Exception as e:
            raise HTTPException(500, str(e)) from e

    async def _start_async(self) -> None:
        """Start the server (blocking async - runs until stopped)."""
        import uvicorn

        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            ws="websockets-sansio",
        )
        server = uvicorn.Server(config)
        await server.serve()


if __name__ == "__main__":
    import anyio
    import httpx

    from agentpool import Agent, AgentPool

    async def test_completions() -> None:
        """Test the chat completions API."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8000/v1/chat/completions",
                headers={"Authorization": "Bearer dummy"},
                json={
                    "model": "gpt-5-mini",
                    "messages": [{"role": "user", "content": "Tell me a joke"}],
                    "stream": True,
                },
                timeout=30.0,
            )

            if response.is_success:
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        data = line[6:]  # Remove "data: " prefix
                        if data == "[DONE]":
                            break
                        chunk = anyenv.load_json(data, return_type=dict)
                        delta = chunk["choices"][0]["delta"]
                        if "content" in delta:
                            print(delta["content"], end="", flush=True)
                print("\n")
            else:
                print("Completions error:", response.text)

    async def test_responses() -> None:
        """Test the responses API."""
        timeout = httpx.Timeout(30.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                "http://localhost:8000/v1/responses",
                headers={"Authorization": "Bearer dummy"},
                json={
                    "model": "gpt-5-mini",
                    "input": "Tell me a three sentence bedtime story about a unicorn.",
                },
            )
            print("Responses result:", response.text)

            if not response.is_success:
                print("Responses error:", response.text)

    async def main() -> None:
        """Run server and test both endpoints."""
        pool = AgentPool()
        agent = Agent(name="gpt-5-mini", model="openai:gpt-5-mini")
        await pool.add_agent(agent)
        async with (
            OpenAIAPIServer(pool, host="0.0.0.0", port=8000) as server,
            server.run_context(),
        ):
            await anyio.sleep(1)  # Wait for server to start
            print("Testing completions endpoint...")
            await test_completions()
            print("\nTesting responses endpoint...")
            await test_responses()

    anyio.run(main)
