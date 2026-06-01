"""Integration tests for AgentPool SessionPool integration.

Tests cover SessionPool lifecycle, configuration, create_session convenience,
and protocol feature flags.
"""

from __future__ import annotations

import pytest

from agentpool import AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.orchestrator import SessionPool
from agentpool_config.session_pool import SessionPoolConfig


@pytest.fixture
def basic_manifest() -> AgentsManifest:
    """Create a minimal manifest with one agent."""
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    return AgentsManifest(agents={"test_agent": agent_config})


# =============================================================================
# SessionPool Lifecycle
# =============================================================================


class TestSessionPoolLifecycle:
    """Test SessionPool initialization and shutdown within AgentPool."""

    @pytest.mark.integration
    async def test_session_pool_not_initialized_by_default(
        self,
        basic_manifest: AgentsManifest,
    ) -> None:
        """SessionPool should be None by default (opt-in)."""
        async with AgentPool(basic_manifest) as pool:
            assert pool.session_pool is None

    @pytest.mark.integration
    async def test_session_pool_initialized_when_enabled(
        self,
        basic_manifest: AgentsManifest,
    ) -> None:
        """SessionPool should be initialized when enable_session_pool=True."""
        async with AgentPool(
            basic_manifest,
            enable_session_pool=True,
        ) as pool:
            assert pool.session_pool is not None
            assert isinstance(pool.session_pool, SessionPool)

    @pytest.mark.integration
    async def test_session_pool_shutdown_on_exit(
        self,
        basic_manifest: AgentsManifest,
    ) -> None:
        """SessionPool should be shut down when AgentPool exits."""
        pool = AgentPool(basic_manifest, enable_session_pool=True)
        async with pool:
            assert pool.session_pool is not None
        assert pool._session_pool is None

    @pytest.mark.integration
    async def test_multiple_enter_exit_cycles(
        self,
        basic_manifest: AgentsManifest,
    ) -> None:
        """AgentPool should support multiple enter/exit cycles with SessionPool."""
        pool = AgentPool(basic_manifest, enable_session_pool=True)

        for _ in range(3):
            async with pool:
                assert pool.session_pool is not None
            assert pool._session_pool is None


# =============================================================================
# SessionPool Configuration
# =============================================================================


class TestSessionPoolConfiguration:
    """Test SessionPool configuration propagation."""

    @pytest.mark.integration
    async def test_default_session_pool_config(self) -> None:
        """Default SessionPoolConfig should have expected defaults."""
        cfg = SessionPoolConfig()
        assert cfg.enable_auto_resume is True
        assert cfg.enable_event_bus is True
        assert cfg.session_ttl_seconds == 3600.0
        assert cfg.max_auto_resume == 10
        assert cfg.max_queue_size == 1000
        assert cfg.mcp_max_processes == 100

    @pytest.mark.integration
    async def test_custom_session_pool_config(self) -> None:
        """Custom SessionPoolConfig should propagate to SessionPool."""
        cfg = SessionPoolConfig(
            enable_auto_resume=False,
            enable_event_bus=False,
            session_ttl_seconds=1800.0,
            max_auto_resume=5,
            max_queue_size=500,
            mcp_max_processes=50,
        )
        manifest = AgentsManifest(
            agents={
                "test_agent": NativeAgentConfig(
                    name="test_agent",
                    model="test",
                    system_prompt="You are a test agent",
                )
            },
            session_pool=cfg,
        )

        async with AgentPool(manifest, enable_session_pool=True) as pool:
            sp = pool.session_pool
            assert sp is not None
            assert sp.turns._enable_auto_resume is False
            assert sp._enable_event_bus is False
            assert sp.sessions._session_ttl_seconds == 1800.0
            assert sp.sessions._mcp_max_processes == 50
            assert sp.turns.event_bus._max_queue_size == 500

    @pytest.mark.integration
    async def test_explicit_config_overrides_manifest(
        self,
        basic_manifest: AgentsManifest,
    ) -> None:
        """Explicit session_pool_config parameter should override manifest."""
        explicit_cfg = SessionPoolConfig(max_auto_resume=99)

        async with AgentPool(
            basic_manifest,
            enable_session_pool=True,
            session_pool_config=explicit_cfg,
        ) as pool:
            sp = pool.session_pool
            assert sp is not None
            assert sp.turns._max_auto_resume == 99


# =============================================================================
# create_session Convenience Method
# =============================================================================


class TestCreateSession:
    """Test AgentPool.create_session() convenience method."""

    @pytest.mark.integration
    async def test_create_session_raises_when_disabled(
        self,
        basic_manifest: AgentsManifest,
    ) -> None:
        """create_session should raise RuntimeError when SessionPool is disabled."""
        async with AgentPool(basic_manifest) as pool:
            with pytest.raises(RuntimeError, match="SessionPool is not enabled"):
                await pool.create_session("test-session")

    @pytest.mark.integration
    async def test_create_session_when_enabled(
        self,
        basic_manifest: AgentsManifest,
    ) -> None:
        """create_session should return a SessionState when enabled."""
        async with AgentPool(
            basic_manifest,
            enable_session_pool=True,
        ) as pool:
            state = await pool.create_session("test-session", agent_name="test_agent")
            assert state.session_id == "test-session"
            assert state.agent_name == "test_agent"

    @pytest.mark.integration
    async def test_create_session_with_metadata(
        self,
        basic_manifest: AgentsManifest,
    ) -> None:
        """create_session should pass metadata through to SessionPool."""
        async with AgentPool(
            basic_manifest,
            enable_session_pool=True,
        ) as pool:
            state = await pool.create_session(
                "test-session",
                agent_name="test_agent",
                custom_key="custom_value",
            )
            assert state.metadata.get("custom_key") == "custom_value"


# =============================================================================
# Protocol Feature Flags
# =============================================================================


class TestProtocolFeatureFlags:
    """Test per-protocol session pool feature flags on AgentsManifest."""

    def test_acp_config_default(self) -> None:
        """ACPConfig.use_session_pool should default to False."""
        manifest = AgentsManifest()
        assert manifest.acp.use_session_pool is False

    def test_opencode_config_default(self) -> None:
        """OpenCodeConfig.use_session_pool should default to False."""
        manifest = AgentsManifest()
        assert manifest.opencode.use_session_pool is False

    def test_acp_config_from_yaml(self) -> None:
        """ACP config should parse from YAML."""
        manifest = AgentsManifest.from_yaml("""
acp:
  use_session_pool: true
""")
        assert manifest.acp.use_session_pool is True

    def test_opencode_config_from_yaml(self) -> None:
        """OpenCode config should parse from YAML."""
        manifest = AgentsManifest.from_yaml("""
opencode:
  use_session_pool: true
""")
        assert manifest.opencode.use_session_pool is True

    def test_session_pool_config_from_yaml(self) -> None:
        """SessionPool config should parse from YAML."""
        manifest = AgentsManifest.from_yaml("""
session_pool:
  enable_auto_resume: false
  session_ttl_seconds: 7200.0
  max_auto_resume: 20
""")
        assert manifest.session_pool.enable_auto_resume is False
        assert manifest.session_pool.session_ttl_seconds == 7200.0
        assert manifest.session_pool.max_auto_resume == 20

    def test_full_manifest_with_session_pool(self) -> None:
        """Full manifest should include all session pool configurations."""
        manifest = AgentsManifest.from_yaml("""
agents:
  assistant:
    model: test
    system_prompt: "You are helpful."

session_pool:
  enable_auto_resume: true
  max_queue_size: 2000

acp:
  use_session_pool: true

opencode:
  use_session_pool: false
""")
        assert manifest.session_pool.max_queue_size == 2000
        assert manifest.acp.use_session_pool is True
        assert manifest.opencode.use_session_pool is False
