"""Integration tests for VikingCapability.

Covers tasks 9.1-9.4 from openspec/changes/viking-capability/tasks.md.
Tests YAML config loading, config fallback, mode-based tool exposure,
and error handling — all with mocked AsyncHTTPClient.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import yamling

from agentpool.capabilities.viking import VikingCapability
from agentpool.capabilities.viking.tools import build_tools
from agentpool_config.capabilities import VikingCapabilityConfig, build_capability


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 9.1 — Test YAML config loading
# ---------------------------------------------------------------------------


def test_yaml_config_loading_viking_all() -> None:
    """YAML config with type=viking, mode=all produces VikingCapabilityConfig."""
    from agentpool import AgentsManifest

    yaml_str = """
agents:
  test_agent:
    type: native
    model: test
    capabilities:
      - type: viking
        mode: all
"""
    d = yamling.load_yaml(yaml_str, verify_type=dict)
    manifest = AgentsManifest.model_validate(d)
    cap_configs = manifest.agents["test_agent"].capabilities
    assert len(cap_configs) == 1
    cfg = cap_configs[0]
    assert isinstance(cfg, VikingCapabilityConfig)
    assert cfg.type == "viking"
    assert cfg.mode == "all"


def test_yaml_config_loading_viking_retrieve() -> None:
    """YAML config with mode=retrieve produces VikingCapabilityConfig with that mode."""
    from agentpool import AgentsManifest

    yaml_str = """
agents:
  test_agent:
    type: native
    model: test
    capabilities:
      - type: viking
        mode: retrieve
"""
    d = yamling.load_yaml(yaml_str, verify_type=dict)
    manifest = AgentsManifest.model_validate(d)
    cfg = manifest.agents["test_agent"].capabilities[0]
    assert isinstance(cfg, VikingCapabilityConfig)
    assert cfg.mode == "retrieve"


def test_yaml_config_loading_viking_with_fields() -> None:
    """YAML config with all fields populated parses correctly."""
    from agentpool import AgentsManifest

    yaml_str = """
agents:
  test_agent:
    type: native
    model: test
    capabilities:
      - type: viking
        mode: write
        url: https://viking.example.com
        api_key: secret
        account: acct123
        user: alice
        timeout: 30.0
        skills_uri: viking://user/alice/skills/
        multimodal_bridge: true
"""
    d = yamling.load_yaml(yaml_str, verify_type=dict)
    manifest = AgentsManifest.model_validate(d)
    cfg = manifest.agents["test_agent"].capabilities[0]
    assert isinstance(cfg, VikingCapabilityConfig)
    assert cfg.url == "https://viking.example.com"
    assert cfg.api_key == "secret"
    assert cfg.account == "acct123"
    assert cfg.user == "alice"
    assert cfg.timeout == 30.0
    assert cfg.skills_uri == "viking://user/alice/skills/"
    assert cfg.multimodal_bridge is True


def test_yaml_config_loading_default_mode() -> None:
    """YAML config without mode defaults to 'all'."""
    from agentpool import AgentsManifest

    yaml_str = """
agents:
  test_agent:
    type: native
    model: test
    capabilities:
      - type: viking
"""
    d = yamling.load_yaml(yaml_str, verify_type=dict)
    manifest = AgentsManifest.model_validate(d)
    cfg = manifest.agents["test_agent"].capabilities[0]
    assert isinstance(cfg, VikingCapabilityConfig)
    assert cfg.mode == "all"


# ---------------------------------------------------------------------------
# 9.2 — Test config fallback
# ---------------------------------------------------------------------------


def test_config_fallback_no_url_no_api_key() -> None:
    """Config with no url/api_key has url=None, api_key=None (SDK resolves from env)."""
    cfg = VikingCapabilityConfig()
    assert cfg.url is None
    assert cfg.api_key is None


def test_config_fallback_no_account_no_user() -> None:
    """Config with no account/user has account=None, user=None."""
    cfg = VikingCapabilityConfig()
    assert cfg.account is None
    assert cfg.user is None


def test_config_fallback_no_timeout() -> None:
    """Config with no timeout has timeout=None (SDK uses default 60s)."""
    cfg = VikingCapabilityConfig()
    assert cfg.timeout is None


def test_config_fallback_no_skills_uri() -> None:
    """Config with no skills_uri has skills_uri=None (capability uses default convention)."""
    cfg = VikingCapabilityConfig()
    assert cfg.skills_uri is None


def test_config_fallback_no_resources_uri() -> None:
    """Config with no resources_uri has resources_uri=None."""
    cfg = VikingCapabilityConfig()
    assert cfg.resources_uri is None


def test_config_fallback_no_uploads_uri() -> None:
    """Config with no uploads_uri has uploads_uri=None."""
    cfg = VikingCapabilityConfig()
    assert cfg.uploads_uri is None


def test_config_fallback_no_public_download_base_url() -> None:
    """Config with no public_download_base_url has public_download_base_url=None."""
    cfg = VikingCapabilityConfig()
    assert cfg.public_download_base_url is None


def test_config_fallback_multimodal_bridge_default_false() -> None:
    """Config defaults multimodal_bridge to False."""
    cfg = VikingCapabilityConfig()
    assert cfg.multimodal_bridge is False


# ---------------------------------------------------------------------------
# 9.3 — Test mode-based tool exposure end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_tool_exposure_retrieve() -> None:
    """Load config, build capability, enter context, get toolset — retrieve mode."""
    cfg = VikingCapabilityConfig(mode="retrieve")
    cap = build_capability(cfg)
    assert isinstance(cap, VikingCapability)

    mock_client = AsyncMock()
    cap._client = mock_client

    toolset = cap.get_toolset()
    assert toolset is not None
    tool_names = list(toolset.tools.keys())  # type: ignore[attr-defined]
    assert len(tool_names) == 7


@pytest.mark.asyncio
async def test_mode_tool_exposure_write() -> None:
    """Load config, build capability, enter context, get toolset — write mode."""
    cfg = VikingCapabilityConfig(mode="write")
    cap = build_capability(cfg)
    assert isinstance(cap, VikingCapability)

    mock_client = AsyncMock()
    cap._client = mock_client

    toolset = cap.get_toolset()
    assert toolset is not None
    tool_names = list(toolset.tools.keys())  # type: ignore[attr-defined]
    assert len(tool_names) == 6


@pytest.mark.asyncio
async def test_mode_tool_exposure_graph() -> None:
    """Load config, build capability, enter context, get toolset — graph mode."""
    cfg = VikingCapabilityConfig(mode="graph")
    cap = build_capability(cfg)
    assert isinstance(cap, VikingCapability)

    mock_client = AsyncMock()
    cap._client = mock_client

    toolset = cap.get_toolset()
    assert toolset is not None
    tool_names = list(toolset.tools.keys())  # type: ignore[attr-defined]
    assert len(tool_names) == 2


@pytest.mark.asyncio
async def test_mode_tool_exposure_all() -> None:
    """Load config, build capability, enter context, get toolset — all mode."""
    cfg = VikingCapabilityConfig(mode="all")
    cap = build_capability(cfg)
    assert isinstance(cap, VikingCapability)

    mock_client = AsyncMock()
    cap._client = mock_client

    toolset = cap.get_toolset()
    assert toolset is not None
    tool_names = list(toolset.tools.keys())  # type: ignore[attr-defined]
    assert len(tool_names) == 15


@pytest.mark.asyncio
async def test_mode_tool_exposure_with_config_fields() -> None:
    """Build capability from config with all fields, verify toolset works."""
    cfg = VikingCapabilityConfig(
        mode="all",
        url="https://viking.example.com",
        api_key="key",
        user="alice",
        timeout=30.0,
    )
    cap = build_capability(cfg)
    assert isinstance(cap, VikingCapability)
    assert cap.url == "https://viking.example.com"
    assert cap.user == "alice"

    mock_client = AsyncMock()
    cap._client = mock_client

    toolset = cap.get_toolset()
    assert toolset is not None
    tool_names = list(toolset.tools.keys())  # type: ignore[attr-defined]
    assert len(tool_names) == 15


# ---------------------------------------------------------------------------
# 9.4 — Test error handling (integration-level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_error_graceful() -> None:
    """Network errors are caught and returned as error strings."""
    cap = VikingCapability(mode="all")
    mock_client = AsyncMock()
    mock_client.search = AsyncMock(side_effect=ConnectionError("network down"))
    cap._client = mock_client

    tools = build_tools(cap)
    search_tool = next(t for t in tools if t.__name__ == "viking_search")

    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.session_id = "test"

    result = await search_tool(ctx, query="test")
    assert "viking_search error: network down" in result
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_invalid_uri_graceful() -> None:
    """Invalid URI errors are caught and returned as error strings."""
    cap = VikingCapability(mode="all")
    mock_client = AsyncMock()
    mock_client.read = AsyncMock(side_effect=ValueError("invalid URI format"))
    cap._client = mock_client

    tools = build_tools(cap)
    read_tool = next(t for t in tools if t.__name__ == "viking_read")

    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.session_id = "test"

    result = await read_tool(ctx, uris="not-a-valid-uri")
    assert "viking_read error: invalid URI format" in result


@pytest.mark.asyncio
async def test_permission_error_graceful() -> None:
    """Permission errors are caught and returned as error strings."""
    cap = VikingCapability(mode="all")
    mock_client = AsyncMock()
    mock_client.write = AsyncMock(side_effect=PermissionError("access denied"))
    cap._client = mock_client

    tools = build_tools(cap)
    write_tool = next(t for t in tools if t.__name__ == "viking_write")

    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.session_id = "test"

    result = await write_tool(ctx, uri="viking://protected/doc.md", content="data")
    assert "viking_write error: access denied" in result


@pytest.mark.asyncio
async def test_timeout_error_graceful() -> None:
    """Timeout errors are caught and returned as error strings."""
    cap = VikingCapability(mode="all")
    mock_client = AsyncMock()
    mock_client.search = AsyncMock(side_effect=TimeoutError("request timed out"))
    cap._client = mock_client

    tools = build_tools(cap)
    search_tool = next(t for t in tools if t.__name__ == "viking_search")

    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.session_id = "test"

    result = await search_tool(ctx, query="slow query")
    assert "viking_search error: request timed out" in result


@pytest.mark.asyncio
async def test_generic_exception_graceful() -> None:
    """Generic exceptions are caught and returned as error strings."""
    cap = VikingCapability(mode="all")
    mock_client = AsyncMock()
    mock_client.ls = AsyncMock(side_effect=Exception("unexpected error"))
    cap._client = mock_client

    tools = build_tools(cap)
    ls_tool = next(t for t in tools if t.__name__ == "viking_ls")

    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.session_id = "test"

    result = await ls_tool(ctx, uri="viking://broken/")
    assert "viking_ls error: unexpected error" in result


@pytest.mark.asyncio
async def test_build_capability_from_config_and_use() -> None:
    """Full end-to-end: build capability from config, inject client, use a tool."""
    cfg = VikingCapabilityConfig(mode="retrieve", user="alice")
    cap = build_capability(cfg)
    assert isinstance(cap, VikingCapability)

    mock_client = AsyncMock()
    mock_client.search = AsyncMock(
        return_value={"results": [{"uri": "viking://found.md", "score": 0.95}]}
    )
    cap._client = mock_client

    tools = build_tools(cap)
    search_tool = next(t for t in tools if t.__name__ == "viking_search")

    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.session_id = "session-1"

    result = await search_tool(ctx, query="find me", limit=5)
    assert "viking://found.md" in result
    mock_client.search.assert_called_once()
