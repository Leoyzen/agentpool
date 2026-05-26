"""Tests for model state configured-first logic (Phase 1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from acp.schema import SessionModelState
from agentpool_server.acp_server.provider_router import ProviderRouter
from agentpool_server.shared.model_utils import build_model_state_for_acp


class MockManifest:
    """Mock manifest for testing."""

    def __init__(self, model_variants=None):
        self.model_variants = model_variants or {}


class MockPool:
    """Mock agent pool for testing."""

    def __init__(self, manifest=None):
        self.manifest = manifest


class MockAgent:
    """Mock agent for testing."""

    def __init__(self, model_name="gpt-4o", pool=None, toko_models=None):
        self.model_name = model_name
        self.agent_pool = pool
        self._toko_models = toko_models or []

    async def get_available_models(self):
        return self._toko_models


def create_toko_model(model_id, name, description=""):
    """Create a mock tokonomics model."""
    m = MagicMock()
    m.id = model_id
    m.id_override = None
    m.name = name
    m.description = description
    return m


@pytest.fixture
def manifest_with_variants():
    """Manifest with configured variants."""
    from llmling_models_config import StringModelConfig

    variants = {
        "fast_gpt": StringModelConfig(identifier="openai:gpt-4o-mini"),
        "smart": StringModelConfig(identifier="anthropic:claude-sonnet-4-5"),
    }
    return MockManifest(model_variants=variants)


@pytest.fixture
def empty_manifest():
    """Empty manifest."""
    return MockManifest()


class TestBuildModelStateForAcp:
    """Test build_model_state_for_acp configured-first logic."""

    async def test_configured_first_priority(self, manifest_with_variants):
        """Configured variants take priority over tokonomics."""
        pool = MockPool(manifest=manifest_with_variants)
        toko_models = [create_toko_model("openai:gpt-4o", "GPT-4o")]
        agent = MockAgent(model_name="fast_gpt", pool=pool, toko_models=toko_models)
        router = ProviderRouter(manifest_with_variants)  # type: ignore[arg-type]

        state = await build_model_state_for_acp(agent, router)  # type: ignore[arg-type]

        assert state is not None
        assert isinstance(state, SessionModelState)
        model_ids = {m.model_id for m in state.available_models}
        assert "fast_gpt" in model_ids
        assert "smart" in model_ids
        # Tokonomics models should NOT appear when configured variants exist
        assert "openai:gpt-4o" not in model_ids

    async def test_tokonomics_fallback(self, empty_manifest):
        """When no configured variants, tokonomics is used."""
        pool = MockPool(manifest=empty_manifest)
        toko_models = [
            create_toko_model("openai:gpt-4o", "GPT-4o"),
            create_toko_model("anthropic:claude-sonnet", "Claude Sonnet"),
        ]
        agent = MockAgent(model_name="openai:gpt-4o", pool=pool, toko_models=toko_models)
        router = ProviderRouter(empty_manifest)  # type: ignore[arg-type]

        state = await build_model_state_for_acp(agent, router)  # type: ignore[arg-type]

        assert state is not None
        model_ids = {m.model_id for m in state.available_models}
        assert "openai:gpt-4o" in model_ids
        assert "anthropic:claude-sonnet" in model_ids

    async def test_provider_filtering(self, manifest_with_variants):
        """Disabled providers are filtered out."""
        pool = MockPool(manifest=manifest_with_variants)
        agent = MockAgent(model_name="fast_gpt", pool=pool)
        router = ProviderRouter(manifest_with_variants)  # type: ignore[arg-type]
        await router.disable_provider("openai")

        state = await build_model_state_for_acp(agent, router)  # type: ignore[arg-type]

        assert state is not None
        model_ids = {m.model_id for m in state.available_models}
        # openai provider models should be filtered out
        assert "fast_gpt" not in model_ids
        # anthropic provider models should remain
        assert "smart" in model_ids

    async def test_empty_state(self):
        """No configured variants and no tokonomics returns None."""
        pool = MockPool(manifest=MockManifest())
        agent = MockAgent(model_name="gpt-4o", pool=pool, toko_models=[])
        router = ProviderRouter(MockManifest())  # type: ignore[arg-type]

        state = await build_model_state_for_acp(agent, router)  # type: ignore[arg-type]

        assert state is None

    async def test_error_handling(self):
        """get_available_models() raising returns None gracefully."""
        pool = MockPool(manifest=MockManifest())
        agent = MockAgent(model_name="gpt-4o", pool=pool)
        agent.get_available_models = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        router = ProviderRouter(MockManifest())  # type: ignore[arg-type]

        state = await build_model_state_for_acp(agent, router)  # type: ignore[arg-type]

        assert state is None

    async def test_current_model_in_configured(self, manifest_with_variants):
        """Current model is set correctly when in configured list."""
        pool = MockPool(manifest=manifest_with_variants)
        agent = MockAgent(model_name="smart", pool=pool)
        router = ProviderRouter(manifest_with_variants)  # type: ignore[arg-type]

        state = await build_model_state_for_acp(agent, router)  # type: ignore[arg-type]

        assert state is not None
        assert state.current_model_id == "smart"

    async def test_current_model_not_in_list(self, manifest_with_variants):
        """Current model defaults to first configured variant when not in list."""
        pool = MockPool(manifest=manifest_with_variants)
        agent = MockAgent(model_name="unknown-model", pool=pool)
        router = ProviderRouter(manifest_with_variants)  # type: ignore[arg-type]

        state = await build_model_state_for_acp(agent, router)  # type: ignore[arg-type]

        assert state is not None
        assert state.current_model_id in {"fast_gpt", "smart"}
