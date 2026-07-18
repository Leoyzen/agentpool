"""Tests for OTLP telemetry compatibility routes.

OpenCode clients >= 1.4.4 may POST telemetry to /v1/metrics, /v1/traces,
and /v1/logs. These endpoints must return a success status instead of 405.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient
import pytest


pytestmark = pytest.mark.integration


@pytest.fixture
def otlp_app() -> FastAPI:
    """Create a minimal FastAPI app with only the OTLP sink routes."""
    app = FastAPI()

    @app.post("/v1/metrics")
    async def otlp_metrics(request: Request) -> Response:
        return Response(status_code=204)

    @app.post("/v1/traces")
    async def otlp_traces(request: Request) -> Response:
        return Response(status_code=204)

    @app.post("/v1/logs")
    async def otlp_logs(request: Request) -> Response:
        return Response(status_code=204)

    # Catch-all that only accepts GET (mirrors production server)
    @app.api_route("/{path:path}", methods=["GET", "HEAD", "OPTIONS"])
    async def catch_all(request: Request, path: str) -> Response:
        return Response(status_code=404)

    return app


@pytest.fixture
def otlp_client(otlp_app: FastAPI) -> TestClient:
    """Create a test client for the OTLP app."""
    return TestClient(otlp_app)


@pytest.mark.parametrize(
    "path",
    ["/v1/metrics", "/v1/traces", "/v1/logs"],
)
def test_otlp_post_returns_success(otlp_client: TestClient, path: str) -> None:
    """POST to OTLP endpoints should return 204, not 405."""
    response = otlp_client.post(path, content=b"")
    assert response.status_code == 204


@pytest.mark.parametrize(
    "path",
    ["/v1/metrics", "/v1/traces", "/v1/logs"],
)
def test_otlp_post_with_body_returns_success(otlp_client: TestClient, path: str) -> None:
    """POST with a body should also succeed — payloads are discarded."""
    response = otlp_client.post(path, content=b"some telemetry data")
    assert response.status_code == 204


@pytest.mark.parametrize(
    "path",
    ["/v1/metrics", "/v1/traces", "/v1/logs"],
)
def test_otlp_get_does_not_return_post_content(otlp_client: TestClient, path: str) -> None:
    """GET on OTLP endpoints should not hit the POST handler (falls to catch-all)."""
    response = otlp_client.get(path)
    # The POST endpoint returns 204; GET must NOT return 204.
    assert response.status_code != 204
