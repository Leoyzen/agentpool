"""Tests for TUI event routing filter and /global/routing-check endpoint.

Covers all 4 rules of the OpenCode TUI routing filter:
1. Sync events always dropped
2. Global directory always passes (except sync)
3. Workspace filtering (if active)
4. Directory must match exactly (string comparison, no normalization)

Plus edge cases and the HTTP endpoint integration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from agentpool_server.opencode_server.models import GlobalEvent
from agentpool_server.opencode_server.routes.routing import (
    RoutingCheckResponse,
    tui_event_filter,
)


if TYPE_CHECKING:
    from httpx import AsyncClient

    from agentpool_server.opencode_server.state import ServerState


# =============================================================================
# Helper
# =============================================================================


def _make_event(
    directory: str = "/project",
    workspace: str | None = None,
    payload_type: str | None = None,
) -> GlobalEvent:
    """Create a GlobalEvent for testing."""
    payload: dict[str, Any] = {}
    if payload_type is not None:
        payload["type"] = payload_type
    return GlobalEvent(directory=directory, workspace=workspace, payload=payload)


# =============================================================================
# Rule 1: sync events always dropped
# =============================================================================


def test_sync_event_dropped() -> None:
    """Sync events are always dropped regardless of other conditions."""
    event = _make_event(directory="global", payload_type="sync")
    passed, reason = tui_event_filter(event, "/project")
    assert passed is False
    assert reason == "sync_dropped"


def test_sync_event_dropped_even_with_matching_directory() -> None:
    """Sync event with matching directory is still dropped."""
    event = _make_event(directory="/project", payload_type="sync")
    passed, reason = tui_event_filter(event, "/project")
    assert passed is False
    assert reason == "sync_dropped"


def test_sync_event_dropped_with_matching_workspace() -> None:
    """Sync event with matching workspace is still dropped."""
    event = _make_event(directory="/project", workspace="ws1", payload_type="sync")
    passed, reason = tui_event_filter(event, "/project", current_workspace="ws1")
    assert passed is False
    assert reason == "sync_dropped"


def test_non_sync_payload_type_not_dropped() -> None:
    """Non-sync payload types are not affected by rule 1."""
    event = _make_event(directory="/project", payload_type="session.status")
    passed, reason = tui_event_filter(event, "/project")
    assert passed is True
    assert reason == "directory_match"


def test_no_payload_type_not_dropped() -> None:
    """Event without a payload type is not affected by rule 1."""
    event = _make_event(directory="/project")
    passed, reason = tui_event_filter(event, "/project")
    assert passed is True
    assert reason == "directory_match"


# =============================================================================
# Rule 2: global directory always passes (except sync)
# =============================================================================


def test_global_directory_passes() -> None:
    """Event with directory='global' always passes (non-sync)."""
    event = _make_event(directory="global")
    passed, reason = tui_event_filter(event, "/project")
    assert passed is True
    assert reason == "global_directory"


def test_global_directory_passes_regardless_of_project_directory() -> None:
    """Global directory passes even when project_directory differs."""
    event = _make_event(directory="global")
    passed, reason = tui_event_filter(event, "/completely/different")
    assert passed is True
    assert reason == "global_directory"


def test_global_directory_passes_with_workspace_active() -> None:
    """Global directory passes even when workspace filtering is active."""
    event = _make_event(directory="global", workspace="other-ws")
    passed, reason = tui_event_filter(event, "/project", current_workspace="ws1")
    assert passed is True
    assert reason == "global_directory"


def test_global_directory_with_sync_still_dropped() -> None:
    """Sync event with global directory is dropped (rule 1 takes precedence)."""
    event = _make_event(directory="global", payload_type="sync")
    passed, reason = tui_event_filter(event, "/project")
    assert passed is False
    assert reason == "sync_dropped"


# =============================================================================
# Rule 3: workspace filtering (if active)
# =============================================================================


def test_workspace_match_passes() -> None:
    """Event workspace matches current_workspace → passes."""
    event = _make_event(directory="/project", workspace="ws1")
    passed, reason = tui_event_filter(event, "/project", current_workspace="ws1")
    assert passed is True
    assert reason == "workspace_match"


def test_workspace_mismatch_fails() -> None:
    """Event workspace doesn't match current_workspace → fails."""
    event = _make_event(directory="/project", workspace="ws1")
    passed, reason = tui_event_filter(event, "/project", current_workspace="ws2")
    assert passed is False
    assert reason == "workspace_mismatch"


def test_workspace_match_passes_even_with_wrong_directory() -> None:
    """Workspace match takes priority over directory mismatch."""
    event = _make_event(directory="/other", workspace="ws1")
    passed, reason = tui_event_filter(event, "/project", current_workspace="ws1")
    assert passed is True
    assert reason == "workspace_match"


def test_workspace_mismatch_even_with_matching_directory() -> None:
    """Workspace mismatch fails even when directory matches."""
    event = _make_event(directory="/project", workspace="ws1")
    passed, reason = tui_event_filter(event, "/project", current_workspace="ws2")
    assert passed is False
    assert reason == "workspace_mismatch"


def test_workspace_none_event_with_active_workspace_fails() -> None:
    """Event with workspace=None fails when current_workspace is set."""
    event = _make_event(directory="/project", workspace=None)
    passed, reason = tui_event_filter(event, "/project", current_workspace="ws1")
    assert passed is False
    assert reason == "workspace_mismatch"


def test_empty_string_workspace_vs_none_mismatch() -> None:
    """Empty string workspace != None current_workspace."""
    event = _make_event(directory="/project", workspace="")
    passed, reason = tui_event_filter(event, "/project", current_workspace=None)
    assert passed is True
    assert reason == "directory_match"


def test_workspace_none_current_none_falls_through() -> None:
    """Both workspace and current_workspace None falls through to directory check."""
    event = _make_event(directory="/project", workspace=None)
    passed, reason = tui_event_filter(event, "/project", current_workspace=None)
    assert passed is True
    assert reason == "directory_match"


# =============================================================================
# Rule 4: directory must match exactly
# =============================================================================


def test_directory_match_passes() -> None:
    """Exact directory match → passes."""
    event = _make_event(directory="/project")
    passed, reason = tui_event_filter(event, "/project")
    assert passed is True
    assert reason == "directory_match"


def test_directory_mismatch_fails() -> None:
    """Directory mismatch → fails."""
    event = _make_event(directory="/other")
    passed, reason = tui_event_filter(event, "/project")
    assert passed is False
    assert reason == "directory_mismatch"


def test_directory_trailing_slash_not_normalized() -> None:
    """Trailing slash vs no trailing slash → NOT equal (no normalization)."""
    event = _make_event(directory="/project/")
    passed, reason = tui_event_filter(event, "/project")
    assert passed is False
    assert reason == "directory_mismatch"


def test_directory_no_trailing_vs_trailing_not_normalized() -> None:
    """No trailing slash vs trailing slash → NOT equal (no normalization)."""
    event = _make_event(directory="/project")
    passed, reason = tui_event_filter(event, "/project/")
    assert passed is False
    assert reason == "directory_mismatch"


def test_directory_case_sensitive() -> None:
    """Directory comparison is case-sensitive."""
    event = _make_event(directory="/Project")
    passed, reason = tui_event_filter(event, "/project")
    assert passed is False
    assert reason == "directory_mismatch"


def test_directory_empty_string_mismatch() -> None:
    """Empty string directory doesn't match a real path."""
    event = _make_event(directory="")
    passed, reason = tui_event_filter(event, "/project")
    assert passed is False
    assert reason == "directory_mismatch"


def test_directory_match_with_spaces() -> None:
    """Directory with spaces matches exactly."""
    event = _make_event(directory="/path with spaces/project")
    passed, reason = tui_event_filter(event, "/path with spaces/project")
    assert passed is True
    assert reason == "directory_match"


# =============================================================================
# Rule priority / interaction edge cases
# =============================================================================


def test_rule_priority_sync_over_global() -> None:
    """Rule 1 (sync) takes precedence over rule 2 (global directory)."""
    event = _make_event(directory="global", payload_type="sync")
    passed, reason = tui_event_filter(event, "/project")
    assert passed is False
    assert reason == "sync_dropped"


def test_rule_priority_global_over_workspace() -> None:
    """Rule 2 (global directory) takes precedence over rule 3 (workspace)."""
    event = _make_event(directory="global", workspace="wrong-ws")
    passed, reason = tui_event_filter(event, "/project", current_workspace="ws1")
    assert passed is True
    assert reason == "global_directory"


def test_rule_priority_workspace_over_directory() -> None:
    """Rule 3 (workspace) takes precedence over rule 4 (directory)."""
    event = _make_event(directory="/wrong", workspace="ws1")
    passed, reason = tui_event_filter(event, "/project", current_workspace="ws1")
    assert passed is True
    assert reason == "workspace_match"


def test_full_path_cjk_directory() -> None:
    """CJK characters in directory match exactly."""
    event = _make_event(directory="/项目/代码")
    passed, reason = tui_event_filter(event, "/项目/代码")
    assert passed is True
    assert reason == "directory_match"


# =============================================================================
# RoutingCheckResponse model tests
# =============================================================================


def test_routing_check_response_serialization() -> None:
    """RoutingCheckResponse serializes correctly with camelCase aliases."""
    response = RoutingCheckResponse(would_pass=True, reason="directory_match")
    dumped = response.model_dump(by_alias=True, exclude_none=True)
    assert dumped["wouldPass"] is True
    assert dumped["reason"] == "directory_match"


def test_routing_check_response_all_reasons() -> None:
    """All valid reason values produce valid RoutingCheckResponse."""
    valid_reasons = [
        "sync_dropped",
        "global_directory",
        "workspace_match",
        "workspace_mismatch",
        "directory_match",
        "directory_mismatch",
    ]
    for reason in valid_reasons:
        response = RoutingCheckResponse(would_pass=True, reason=reason)
        assert response.reason == reason


# =============================================================================
# /global/routing-check HTTP endpoint tests
# =============================================================================


@pytest.mark.anyio
async def test_routing_check_directory_match(async_client: AsyncClient) -> None:
    """Directory match returns would_pass=True, reason=directory_match."""
    response = await async_client.get(
        "/global/routing-check",
        params={"directory": "/my/project", "project_directory": "/my/project"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["wouldPass"] is True
    assert data["reason"] == "directory_match"


@pytest.mark.anyio
async def test_routing_check_directory_mismatch(async_client: AsyncClient) -> None:
    """Directory mismatch returns would_pass=False, reason=directory_mismatch."""
    response = await async_client.get(
        "/global/routing-check",
        params={"directory": "/different/path"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["wouldPass"] is False
    assert data["reason"] == "directory_mismatch"


@pytest.mark.anyio
async def test_routing_check_global_directory(async_client: AsyncClient) -> None:
    """Global directory returns would_pass=True, reason=global_directory."""
    response = await async_client.get(
        "/global/routing-check",
        params={"directory": "global"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["wouldPass"] is True
    assert data["reason"] == "global_directory"


@pytest.mark.anyio
async def test_routing_check_sync_dropped(async_client: AsyncClient) -> None:
    """Sync event with global directory is dropped by rule 1.

    Note: the endpoint constructs a GlobalEvent with an empty payload {},
    so we can't directly test sync_dropped via the endpoint (no payload.type
    parameter is exposed). This test verifies the general behavior.
    """
    # The endpoint doesn't support payload_type param, so we test the
    # pure function directly for sync_dropped and verify the endpoint
    # works for the other cases
    event = _make_event(directory="global", payload_type="sync")
    passed, reason = tui_event_filter(event, "/project")
    assert passed is False
    assert reason == "sync_dropped"


@pytest.mark.anyio
async def test_routing_check_workspace_match(async_client: AsyncClient) -> None:
    """Workspace match with current_workspace set returns would_pass=True."""
    response = await async_client.get(
        "/global/routing-check",
        params={
            "directory": "/project",
            "workspace": "ws1",
            "current_workspace": "ws1",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["wouldPass"] is True
    assert data["reason"] == "workspace_match"


@pytest.mark.anyio
async def test_routing_check_workspace_mismatch(async_client: AsyncClient) -> None:
    """Workspace mismatch with current_workspace set returns would_pass=False."""
    response = await async_client.get(
        "/global/routing-check",
        params={
            "directory": "/project",
            "workspace": "ws1",
            "current_workspace": "ws2",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["wouldPass"] is False
    assert data["reason"] == "workspace_mismatch"


@pytest.mark.anyio
async def test_routing_check_custom_project_directory(async_client: AsyncClient) -> None:
    """Custom project_directory parameter overrides state.working_dir."""
    response = await async_client.get(
        "/global/routing-check",
        params={
            "directory": "/custom/project",
            "project_directory": "/custom/project",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["wouldPass"] is True
    assert data["reason"] == "directory_match"


@pytest.mark.anyio
async def test_routing_check_default_project_directory(async_client: AsyncClient) -> None:
    """Without project_directory param, uses server's base_path (resolved).

    Since the server's base_path is a temp directory, we use a known
    different directory to verify it fails (directory_mismatch), which
    confirms the default is being used rather than matching any value.
    """
    response = await async_client.get(
        "/global/routing-check",
        params={"directory": "/definitely/not/the/working/dir"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["wouldPass"] is False
    assert data["reason"] == "directory_mismatch"


@pytest.mark.anyio
async def test_routing_check_default_uses_base_path(
    async_client: AsyncClient,
    server_state: ServerState,
) -> None:
    """Routing-check default is state.base_path, not raw working_dir.

    The endpoint should use the resolved canonical path (base_path) as
    the default project directory, matching how get_event_factory()
    already uses self.base_path for directory normalization.  Verify by
    sending directory=base_path without an explicit project_directory
    override — the event must pass the directory-match rule.
    """
    response = await async_client.get(
        "/global/routing-check",
        params={"directory": server_state.base_path},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["wouldPass"] is True
    assert data["reason"] == "directory_match"
