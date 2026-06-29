"""Red flag tests for RFC-0031: ACP per-session agent isolation.

These tests guard against regression of the config-relative path resolution
bug discovered during RFC-0031 implementation. The bug: _config_dir_global
is reset to None by ConfigContextManager.__exit__ after pool loading, causing
tool schema paths (and other config-relative paths) to fail during per-session
agent creation.

Run with: pytest tests/servers/acp_server/test_acp_per_session_agent_red_flags.py -v
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from upathtools import UPath

from agentpool.delegation import AgentPool
from agentpool_server.acp_server.v1.acp_agent import AgentPoolACPAgent


class FakeManifest:
    """Fake manifest for testing."""

    def __init__(self, config_file_path: str | None = None) -> None:
        self.config_file_path = config_file_path
        self.agents: dict[str, Any] = {}
        self.acp = None


class FakePool:
    """Fake AgentPool for testing."""

    def __init__(self, config_file_path: str | None = None) -> None:
        self.main_agent = FakeAgent("test_agent")
        self.manifest = FakeManifest(config_file_path)
        self.storage = MagicMock()
        self.storage.metadata_generated = MagicMock()
        self.storage.metadata_generated.connect = MagicMock()


class FakeAgent:
    """Fake BaseAgent for testing."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.agent_pool: AgentPool[Any] | None = None


class TestConfigPathResolutionRedFlags:
    """Red flag tests: config path resolution during per-session agent creation."""

    @pytest.mark.skip(reason="_resolve_agent_config_path removed; per-session agent creation now managed by SessionPool")
    def test_resolve_agent_config_path_returns_manifest_dir(
        self,
        tmp_path: Path,
    ) -> None:
        """RED FLAG: _resolve_agent_config_path must return the manifest config directory.

        Regression: If _resolve_agent_config_path returns None, tool schemas
        with relative paths will fail during agent.__aenter__().
        """
        from agentpool.models.agents import NativeAgentConfig

        config_file = tmp_path / "config.yml"
        config_file.write_text("agents:\n  test_agent:\n    type: native\n    model: test\n")

        agent_config = NativeAgentConfig(
            name="test_agent",
            model="test",
            config_file_path=str(config_file),
        )

        pool = FakePool(config_file_path=str(config_file))
        pool.manifest.agents["test_agent"] = agent_config

        default_agent = FakeAgent("test_agent")
        default_agent.agent_pool = pool  # type: ignore[assignment]

        acp_agent = AgentPoolACPAgent(
            default_agent=default_agent,  # type: ignore[arg-type]
            client=MagicMock(),
        )

        result = acp_agent._resolve_agent_config_path()
        assert result is not None, (
            "_resolve_agent_config_path returned None - tool schemas will fail! "
            "This is a regression of the RFC-0031 config path bug."
        )
        assert Path(result) == tmp_path, (
            f"_resolve_agent_config_path returned wrong directory: {result}, expected {tmp_path}"
        )

    @pytest.mark.skip(reason="_resolve_agent_config_path removed; per-session agent creation now managed by SessionPool")
    def test_resolve_agent_config_path_falls_back_to_manifest_level(
        self,
        tmp_path: Path,
    ) -> None:
        """RED FLAG: When agent config has no config_file_path, must fall back to manifest level.

        Regression: AgentsManifest.from_file propagates config_file_path to agents,
        but serve_acp.py may not. If fallback fails, _config_dir_global stays None.
        """
        from agentpool.models.agents import NativeAgentConfig

        config_file = tmp_path / "config.yml"
        config_file.write_text("agents:\n  test_agent:\n    type: native\n    model: test\n")

        # Agent config has NO config_file_path (simulating serve_acp.py behavior)
        agent_config = NativeAgentConfig(
            name="test_agent",
            model="test",
            # config_file_path is None - must fall back to manifest level
        )

        pool = FakePool(config_file_path=str(config_file))
        pool.manifest.agents["test_agent"] = agent_config

        default_agent = FakeAgent("test_agent")
        default_agent.agent_pool = pool  # type: ignore[assignment]

        acp_agent = AgentPoolACPAgent(
            default_agent=default_agent,  # type: ignore[arg-type]
            client=MagicMock(),
        )

        result = acp_agent._resolve_agent_config_path()
        assert result is not None, (
            "_resolve_agent_config_path returned None when agent config_file_path is None - "
            "manifest-level fallback failed! This is a regression of the RFC-0031 config path bug."
        )
        assert Path(result) == tmp_path, (
            f"Fallback returned wrong directory: {result}, expected {tmp_path}"
        )

    @pytest.mark.skip(reason="get_or_create_session_agent removed; per-session agent creation now managed by SessionPool")
    @pytest.mark.asyncio
    async def test_config_dir_contextvar_set_during_agent_creation(
        self,
        tmp_path: Path,
    ) -> None:
        """RED FLAG: CONFIG_DIR ContextVar must be set during get_or_create_session_agent.

        Regression: Some providers (e.g., xeno-agent) use CONFIG_DIR.get() directly
        instead of get_config_dir(). If CONFIG_DIR is None, tool schema paths fail.
        """
        import agentpool_config.context as ctx
        from agentpool.models.agents import NativeAgentConfig

        config_file = tmp_path / "config.yml"
        config_file.write_text("agents:\n  test_agent:\n    type: native\n    model: test\n")

        agent_config = NativeAgentConfig(
            name="test_agent",
            model="test",
            config_file_path=str(config_file),
        )

        pool = FakePool(config_file_path=str(config_file))
        pool.manifest.agents["test_agent"] = agent_config

        default_agent = FakeAgent("test_agent")
        default_agent.agent_pool = pool  # type: ignore[assignment]

        # Simulate post-pool-loading state: CONFIG_DIR is None
        original_config_dir = ctx.CONFIG_DIR.get()
        ctx._config_dir_global = None
        ctx.CONFIG_DIR.set(None)

        try:
            acp_agent = AgentPoolACPAgent(
                default_agent=default_agent,  # type: ignore[arg-type]
                client=MagicMock(),
            )

            # Track CONFIG_DIR during creation
            captured_values: list[str | None] = []

            def tracking_get_agent(*args: Any, **kwargs: Any) -> Any:
                config_dir = ctx.CONFIG_DIR.get()
                captured_values.append(str(config_dir) if config_dir is not None else None)
                mock_agent = MagicMock()
                mock_agent.session_id = None
                mock_agent.__aenter__ = MagicMock(return_value=asyncio.Future())
                mock_agent.__aenter__.return_value.set_result(mock_agent)
                mock_agent.__aexit__ = MagicMock(return_value=asyncio.Future())
                mock_agent.__aexit__.return_value.set_result(None)
                return mock_agent

            with patch.object(NativeAgentConfig, "get_agent", tracking_get_agent):
                await acp_agent.get_or_create_session_agent("test-session")

            assert len(captured_values) > 0, "get_agent was not called"
            assert captured_values[0] is not None, (
                "CONFIG_DIR ContextVar was None during get_agent() call - "
                "providers using CONFIG_DIR.get() directly will fail! "
                "This is a regression of the RFC-0031 config path bug."
            )
            assert captured_values[0] == str(tmp_path), (
                f"CONFIG_DIR was wrong during get_agent(): {captured_values[0]}, "
                f"expected {tmp_path}"
            )

        finally:
            ctx._config_dir_global = None
            if original_config_dir is not None:
                ctx.CONFIG_DIR.set(original_config_dir)
            else:
                ctx.CONFIG_DIR.set(None)

    @pytest.mark.skip(reason="get_or_create_session_agent removed; per-session agent creation now managed by SessionPool")
    @pytest.mark.asyncio
    async def test_config_dir_global_set_during_agent_creation(
        self,
        tmp_path: Path,
    ) -> None:
        """RED FLAG: _config_dir_global must be set during get_or_create_session_agent.

        Regression: If _config_dir_global is None during agent.__aenter__(),
        tool providers that load schema files from relative paths will fail.
        """
        import agentpool_config.context as ctx
        from agentpool.models.agents import NativeAgentConfig

        config_file = tmp_path / "config.yml"
        config_file.write_text("agents:\n  test_agent:\n    type: native\n    model: test\n")

        agent_config = NativeAgentConfig(
            name="test_agent",
            model="test",
            config_file_path=str(config_file),
        )

        pool = FakePool(config_file_path=str(config_file))
        pool.manifest.agents["test_agent"] = agent_config

        default_agent = FakeAgent("test_agent")
        default_agent.agent_pool = pool  # type: ignore[assignment]

        # Simulate post-pool-loading state: _config_dir_global is None
        original_dir = ctx._config_dir_global
        ctx._config_dir_global = None

        try:
            acp_agent = AgentPoolACPAgent(
                default_agent=default_agent,  # type: ignore[arg-type]
                client=MagicMock(),
            )

            # Track _config_dir_global during creation
            captured_values: list[str | None] = []

            # Create a mock get_agent that tracks _config_dir_global
            original_get_agent = agent_config.get_agent
            def tracking_get_agent(*args: Any, **kwargs: Any) -> Any:
                captured_values.append(str(ctx._config_dir_global) if ctx._config_dir_global is not None else None)
                # Return a mock agent that supports async context manager
                mock_agent = MagicMock()
                mock_agent.session_id = None
                mock_agent.__aenter__ = MagicMock(return_value=asyncio.Future())
                mock_agent.__aenter__.return_value.set_result(mock_agent)
                mock_agent.__aexit__ = MagicMock(return_value=asyncio.Future())
                mock_agent.__aexit__.return_value.set_result(None)
                return mock_agent

            # Patch the class method, not instance method
            with patch.object(NativeAgentConfig, "get_agent", tracking_get_agent):
                await acp_agent.get_or_create_session_agent("test-session")

            # Verify _config_dir_global was set during get_agent()
            assert len(captured_values) > 0, "get_agent was not called"
            assert captured_values[0] is not None, (
                "_config_dir_global was None during get_agent() call - "
                "tool schema paths will fail! This is a regression of the RFC-0031 config path bug."
            )
            assert captured_values[0] == str(tmp_path), (
                f"_config_dir_global was wrong during get_agent(): {captured_values[0]}, "
                f"expected {tmp_path}"
            )

        finally:
            ctx._config_dir_global = original_dir

    @pytest.mark.skip(reason="get_or_create_session_agent removed; per-session agent creation now managed by SessionPool")
    @pytest.mark.asyncio
    async def test_config_dir_global_restored_after_agent_creation(
        self,
        tmp_path: Path,
    ) -> None:
        """RED FLAG: _config_dir_global must be restored after get_or_create_session_agent.

        Regression: If _config_dir_global leaks after agent creation, subsequent
        operations (e.g., loading another agent) may use wrong base directory.
        """
        import agentpool_config.context as ctx
        from agentpool.models.agents import NativeAgentConfig

        config_file = tmp_path / "config.yml"
        config_file.write_text("agents:\n  test_agent:\n    type: native\n    model: test\n")

        agent_config = NativeAgentConfig(
            name="test_agent",
            model="test",
            config_file_path=str(config_file),
        )

        pool = FakePool(config_file_path=str(config_file))
        pool.manifest.agents["test_agent"] = agent_config

        default_agent = FakeAgent("test_agent")
        default_agent.agent_pool = pool  # type: ignore[assignment]

        # Set an original value to verify restoration
        original_dir = ctx._config_dir_global
        ctx._config_dir_global = UPath("/some/other/dir")

        try:
            acp_agent = AgentPoolACPAgent(
                default_agent=default_agent,  # type: ignore[arg-type]
                client=MagicMock(),
            )

            # Mock get_agent to return an async-context-manager-compatible agent
            mock_agent = MagicMock()
            mock_agent.session_id = None
            mock_agent.__aenter__ = MagicMock(return_value=asyncio.Future())
            mock_agent.__aenter__.return_value.set_result(mock_agent)
            mock_agent.__aexit__ = MagicMock(return_value=asyncio.Future())
            mock_agent.__aexit__.return_value.set_result(None)

            with patch.object(NativeAgentConfig, "get_agent", return_value=mock_agent):
                await acp_agent.get_or_create_session_agent("test-session")

            # Verify _config_dir_global was restored
            assert ctx._config_dir_global == UPath("/some/other/dir"), (
                f"_config_dir_global was not restored after agent creation: "
                f"{ctx._config_dir_global}, expected UPath('/some/other/dir'). "
                f"This is a regression - the context leaks between sessions."
            )

        finally:
            ctx._config_dir_global = original_dir

    @pytest.mark.skip(reason="get_or_create_session_agent removed; per-session agent creation now managed by SessionPool")
    @pytest.mark.asyncio
    async def test_config_dir_global_set_during_aenter(
        self,
        tmp_path: Path,
    ) -> None:
        """RED FLAG: _config_dir_global must be set during agent.__aenter__().

        Regression: Tool providers initialize during __aenter__() and need
        _config_dir_global to resolve relative schema paths. If it's None,
        FileNotFoundError will be raised.
        """
        import agentpool_config.context as ctx
        from agentpool.models.agents import NativeAgentConfig

        config_file = tmp_path / "config.yml"
        config_file.write_text("agents:\n  test_agent:\n    type: native\n    model: test\n")

        agent_config = NativeAgentConfig(
            name="test_agent",
            model="test",
            config_file_path=str(config_file),
        )

        pool = FakePool(config_file_path=str(config_file))
        pool.manifest.agents["test_agent"] = agent_config

        default_agent = FakeAgent("test_agent")
        default_agent.agent_pool = pool  # type: ignore[assignment]

        original_dir = ctx._config_dir_global
        ctx._config_dir_global = None

        try:
            acp_agent = AgentPoolACPAgent(
                default_agent=default_agent,  # type: ignore[arg-type]
                client=MagicMock(),
            )

            # Track _config_dir_global during __aenter__
            aenter_values: list[str | None] = []

            mock_agent = MagicMock()
            mock_agent.session_id = None

            async def tracking_aenter(*args: Any, **kwargs: Any) -> Any:
                aenter_values.append(str(ctx._config_dir_global) if ctx._config_dir_global is not None else None)
                return mock_agent

            mock_agent.__aenter__ = tracking_aenter
            mock_agent.__aexit__ = MagicMock(return_value=asyncio.Future())
            mock_agent.__aexit__.return_value.set_result(None)

            with patch.object(NativeAgentConfig, "get_agent", return_value=mock_agent):
                await acp_agent.get_or_create_session_agent("test-session")

            # Verify _config_dir_global was set during __aenter__()
            assert len(aenter_values) > 0, "__aenter__ was not called"
            assert aenter_values[0] is not None, (
                "_config_dir_global was None during agent.__aenter__() - "
                "tool providers will fail to resolve schema paths! "
                "This is a regression of the RFC-0031 config path bug."
            )
            assert aenter_values[0] == str(tmp_path), (
                f"_config_dir_global was wrong during __aenter__(): {aenter_values[0]}, "
                f"expected {tmp_path}"
            )

        finally:
            ctx._config_dir_global = original_dir

    @pytest.mark.skip(reason="_resolve_agent_config_path removed; per-session agent creation now managed by SessionPool")
    def test_resolve_agent_config_path_with_agent_name(
        self,
        tmp_path: Path,
    ) -> None:
        """RED FLAG: _resolve_agent_config_path must resolve correct config for agent_name.

        Regression: switch_active_agent() passes agent_name but if wrong config
        is resolved, the switched agent will use wrong base directory for paths.
        """
        from agentpool.models.agents import NativeAgentConfig

        config_dir_a = tmp_path / "agent_a"
        config_dir_a.mkdir()
        config_file_a = config_dir_a / "config.yml"
        config_file_a.write_text("agents:\n  agent_a:\n    type: native\n    model: test\n")

        config_dir_b = tmp_path / "agent_b"
        config_dir_b.mkdir()
        config_file_b = config_dir_b / "config.yml"
        config_file_b.write_text("agents:\n  agent_b:\n    type: native\n    model: test\n")

        agent_config_a = NativeAgentConfig(
            name="agent_a",
            model="test",
            config_file_path=str(config_file_a),
        )
        agent_config_b = NativeAgentConfig(
            name="agent_b",
            model="test",
            config_file_path=str(config_file_b),
        )

        pool = FakePool(config_file_path=str(tmp_path / "config.yml"))
        pool.manifest.agents["agent_a"] = agent_config_a
        pool.manifest.agents["agent_b"] = agent_config_b

        default_agent = FakeAgent("test_agent")
        default_agent.agent_pool = pool  # type: ignore[assignment]

        acp_agent = AgentPoolACPAgent(
            default_agent=default_agent,  # type: ignore[arg-type]
            client=MagicMock(),
        )

        result_a = acp_agent._resolve_agent_config_path("agent_a")
        assert result_a is not None
        assert Path(result_a) == config_dir_a, (
            f"Wrong config path for agent_a: {result_a}, expected {config_dir_a}"
        )

        result_b = acp_agent._resolve_agent_config_path("agent_b")
        assert result_b is not None
        assert Path(result_b) == config_dir_b, (
            f"Wrong config path for agent_b: {result_b}, expected {config_dir_b}"
        )

    @pytest.mark.skip(reason="_resolve_agent_config_path removed; per-session agent creation now managed by SessionPool")
    def test_resolve_agent_config_path_returns_none_for_nonexistent_agent(
        self,
        tmp_path: Path,
    ) -> None:
        """RED FLAG: _resolve_agent_config_path must return None for unknown agents.

        This ensures graceful fallback instead of crashing.
        """
        pool = FakePool(config_file_path=str(tmp_path / "config.yml"))
        default_agent = FakeAgent("test_agent")
        default_agent.agent_pool = pool  # type: ignore[assignment]

        acp_agent = AgentPoolACPAgent(
            default_agent=default_agent,  # type: ignore[arg-type]
            client=MagicMock(),
        )

        result = acp_agent._resolve_agent_config_path("nonexistent_agent")
        assert result is None, (
            f"_resolve_agent_config_path should return None for unknown agent, "
            f"got {result}"
        )
