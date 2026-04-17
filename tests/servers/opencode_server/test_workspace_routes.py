"""Tests for experimental workspace compatibility routes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agentpool_storage.opencode_provider import helpers


if TYPE_CHECKING:
    from httpx import AsyncClient

    from agentpool_server.opencode_server.state import ServerState


pytestmark = pytest.mark.asyncio


async def test_list_workspaces_returns_singleton_local_workspace(
    async_client: AsyncClient,
    server_state: ServerState,
) -> None:
    """Workspace list returns the singleton local workspace expected by OpenCode."""
    response = await async_client.get("/experimental/workspace")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1

    workspace = data[0]
    expected_directory = server_state.base_path
    expected_project_id = helpers.compute_project_id(expected_directory)

    assert workspace["id"] == f"wrk_{expected_project_id[:12]}"
    assert workspace["type"] == "local"
    assert workspace["name"] == Path(expected_directory).name
    assert workspace["branch"] is None
    assert workspace["directory"] == expected_directory
    assert workspace["extra"] is None
    assert workspace["projectID"] == expected_project_id


async def test_workspace_status_returns_array_with_matching_workspace_id(
    async_client: AsyncClient,
) -> None:
    """Workspace status returns an array compatible with TUI bootstrap mapping."""
    workspace_response = await async_client.get("/experimental/workspace")
    status_response = await async_client.get("/experimental/workspace/status")

    workspace_data = workspace_response.json()
    status_data = status_response.json()

    assert workspace_response.status_code == 200
    assert status_response.status_code == 200
    assert isinstance(status_data, list)
    assert len(status_data) == 1
    assert status_data[0]["workspaceID"] == workspace_data[0]["id"]
    assert status_data[0]["status"] == "connected"
    assert status_data[0]["error"] is None


async def test_workspace_routes_accept_sdk_query_params(
    async_client: AsyncClient,
    server_state: ServerState,
) -> None:
    """Workspace routes stay JSON-shaped when SDK sends directory/workspace params."""
    params = {
        "directory": server_state.base_path,
        "workspace": "wrk_local_override",
    }

    list_response = await async_client.get("/experimental/workspace", params=params)
    status_response = await async_client.get("/experimental/workspace/status", params=params)

    assert list_response.status_code == 200
    assert status_response.status_code == 200
    assert isinstance(list_response.json(), list)
    assert isinstance(status_response.json(), list)
