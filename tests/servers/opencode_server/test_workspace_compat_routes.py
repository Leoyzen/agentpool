"""Compatibility tests for OpenCode experimental workspace routes."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def test_list_workspaces_returns_local_response(client: TestClient) -> None:
    """GET /experimental/workspace should not fall through to the Web UI proxy."""
    response = client.get("/experimental/workspace")

    assert response.status_code == 200
    assert response.json() == []
