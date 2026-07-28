"""Unit + integration tests for AgentFactory modality capability injection.

Tests cover:
- 5.5: image_output mapping to pydantic-ai Model profile
- 5.6: conditional injection of ModalityFilterCapability
  - Skip for fully multimodal models
  - Auto-inject when model lacks input modalities
  - User-config precedence over auto-injection
  - FallbackModelConfig intersection (pessimistic)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from pydantic_ai.models.test import TestModel
import pytest

from agentpool.agents.native_agent.agent import (
    Agent,
    _intersect_capabilities,
    _model_config_names,
)
from agentpool.capabilities.modality_filter import ModalityFilterCapability
from agentpool.models.agents import NativeAgentConfig
from agentpool.models.model_configs import (
    FallbackModelConfig,
    OpenAIModelConfig,
    StringModelConfig,
    TestModelConfig as _TestModelConfig,
)
from agentpool_config.model_capabilities import ModelCapabilities


pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _caps(
    *,
    image_input: bool | None = None,
    audio_input: bool | None = None,
    video_input: bool | None = None,
    document_input: bool | None = None,
    image_output: bool | None = None,
) -> ModelCapabilities:
    """Build a ModelCapabilities instance."""
    return ModelCapabilities(
        image_input=image_input,
        audio_input=audio_input,
        video_input=video_input,
        document_input=document_input,
        image_output=image_output,
    )


def _all_true_caps() -> ModelCapabilities:
    """Capabilities with all modalities enabled."""
    return ModelCapabilities(
        image_input=True,
        audio_input=True,
        video_input=True,
        document_input=True,
        image_output=True,
    )


def _text_only_caps() -> ModelCapabilities:
    """Capabilities with all multimodal inputs disabled."""
    return ModelCapabilities(
        image_input=False,
        audio_input=False,
        video_input=False,
        document_input=False,
        image_output=False,
    )


# ---------------------------------------------------------------------------
# 5.5 — image_output mapping to pydantic-ai Model profile
# ---------------------------------------------------------------------------


async def test_image_output_true_flows_to_model_profile() -> None:
    """image_output=True should set supports_image_output in model profile."""
    agent = Agent(name="test", model="test")
    caps = _caps(image_output=True)
    model = TestModel()
    result = agent._apply_image_output_profile(model, caps)
    assert result is model
    assert result.profile.get("supports_image_output") is True


async def test_image_output_false_flows_to_model_profile() -> None:
    """image_output=False should set supports_image_output=False in profile."""
    agent = Agent(name="test", model="test")
    caps = _caps(image_output=False)
    model = TestModel()
    result = agent._apply_image_output_profile(model, caps)
    assert result is model
    assert result.profile.get("supports_image_output") is False


async def test_image_output_none_does_not_modify_profile() -> None:
    """image_output=None should not modify the model profile."""
    agent = Agent(name="test", model="test")
    caps = _caps(image_output=None)
    model = TestModel()
    original_profile = model._profile
    result = agent._apply_image_output_profile(model, caps)
    assert result is model
    assert result._profile is original_profile


async def test_image_output_merges_with_existing_profile() -> None:
    """image_output should merge into an existing dict profile."""
    agent = Agent(name="test", model="test")
    caps = _caps(image_output=True)
    model = TestModel()
    # Set an existing profile dict.
    model._profile = {"supports_thinking": True}
    result = agent._apply_image_output_profile(model, caps)
    assert result.profile.get("supports_thinking") is True
    assert result.profile.get("supports_image_output") is True


# ---------------------------------------------------------------------------
# 5.6 — Conditional injection: skip for fully multimodal
# ---------------------------------------------------------------------------


async def test_needs_modality_filter_false_for_full_multimodal() -> None:
    """All-True capabilities should NOT trigger modality filter."""
    agent = Agent(name="test", model="test")
    assert agent._needs_modality_filter(_all_true_caps()) is False


async def test_needs_modality_filter_true_when_image_disabled() -> None:
    """image_input=False should trigger modality filter."""
    agent = Agent(name="test", model="test")
    caps = _caps(image_input=False, audio_input=True, video_input=True, document_input=True)
    assert agent._needs_modality_filter(caps) is True


async def test_needs_modality_filter_true_when_audio_disabled() -> None:
    """audio_input=False should trigger modality filter."""
    agent = Agent(name="test", model="test")
    caps = _caps(image_input=True, audio_input=False, video_input=True, document_input=True)
    assert agent._needs_modality_filter(caps) is True


async def test_needs_modality_filter_false_when_all_none() -> None:
    """All-None capabilities (unknown) should NOT trigger filter (no explicit False)."""
    agent = Agent(name="test", model="test")
    caps = ModelCapabilities()
    assert agent._needs_modality_filter(caps) is False


# ---------------------------------------------------------------------------
# 5.6 — Auto-inject ModalityFilterCapability in get_agentlet
# ---------------------------------------------------------------------------


async def test_auto_inject_modality_filter_for_text_only_model() -> None:
    """get_agentlet should auto-inject ModalityFilterCapability when model lacks inputs."""
    config = NativeAgentConfig(
        model=_TestModelConfig(capabilities=_text_only_caps()),
    )
    agent = Agent(name="test", model="test", agent_config=config)
    pydantic_agent = await agent.get_agentlet(model=None, output_type=str)
    # Find ModalityFilterCapability in the agent's capabilities.
    filter_caps = [
        cap
        for cap in pydantic_agent.root_capability.capabilities
        if isinstance(cap, ModalityFilterCapability)
    ]
    assert len(filter_caps) == 1
    assert filter_caps[0].capabilities.image_input is False
    assert filter_caps[0].capabilities.audio_input is False


async def test_no_inject_for_fully_multimodal_model() -> None:
    """get_agentlet should NOT inject ModalityFilterCapability for fully multimodal model."""
    config = NativeAgentConfig(
        model=_TestModelConfig(capabilities=_all_true_caps()),
    )
    agent = Agent(name="test", model="test", agent_config=config)
    pydantic_agent = await agent.get_agentlet(model=None, output_type=str)
    filter_caps = [
        cap
        for cap in pydantic_agent.root_capability.capabilities
        if isinstance(cap, ModalityFilterCapability)
    ]
    assert len(filter_caps) == 0


async def test_no_inject_when_no_config() -> None:
    """get_agentlet should NOT inject when no agent_config is provided."""
    agent = Agent(name="test", model="test")
    pydantic_agent = await agent.get_agentlet(model=None, output_type=str)
    filter_caps = [
        cap
        for cap in pydantic_agent.root_capability.capabilities
        if isinstance(cap, ModalityFilterCapability)
    ]
    assert len(filter_caps) == 0


# ---------------------------------------------------------------------------
# 5.6 — User-config precedence: populate user's instance
# ---------------------------------------------------------------------------


async def test_user_config_modality_filter_populated_with_resolved_caps() -> None:
    """When user pre-configures a ModalityFilterCapability, it should be populated."""
    user_filter = ModalityFilterCapability(capabilities=_caps(image_input=False))
    config = NativeAgentConfig(
        model=_TestModelConfig(capabilities=_text_only_caps()),
    )
    agent = Agent(
        name="test",
        model="test",
        agent_config=config,
        capabilities=[user_filter],
    )
    pydantic_agent = await agent.get_agentlet(model=None, output_type=str)
    filter_caps = [
        cap
        for cap in pydantic_agent.root_capability.capabilities
        if isinstance(cap, ModalityFilterCapability)
    ]
    # Should have exactly one (the user's instance, not auto-injected).
    assert len(filter_caps) == 1
    # The user's instance should be populated with resolved capabilities.
    assert filter_caps[0] is user_filter
    assert filter_caps[0].capabilities.image_input is False
    assert filter_caps[0].capabilities.audio_input is False
    assert filter_caps[0].capabilities.video_input is False
    assert filter_caps[0].capabilities.document_input is False


# ---------------------------------------------------------------------------
# 5.6 — FallbackModelConfig intersection (pessimistic)
# ---------------------------------------------------------------------------


def test_intersect_capabilities_pessimistic() -> None:
    """Intersection: False wins over True for each modality."""
    caps_a = ModelCapabilities(
        image_input=True,
        audio_input=True,
        video_input=False,
        document_input=True,
        image_output=True,
    )
    caps_b = ModelCapabilities(
        image_input=False,
        audio_input=True,
        video_input=False,
        document_input=True,
        image_output=False,
    )
    result = _intersect_capabilities([caps_a, caps_b])
    assert result.image_input is False  # a=True, b=False → False
    assert result.audio_input is True  # both True
    assert result.video_input is False  # both False
    assert result.document_input is True
    assert result.image_output is False


def test_intersect_capabilities_all_true() -> None:
    """Intersection: all True → all True."""
    caps_a = _all_true_caps()
    caps_b = _all_true_caps()
    result = _intersect_capabilities([caps_a, caps_b])
    assert result.image_input is True
    assert result.audio_input is True
    assert result.video_input is True
    assert result.document_input is True
    assert result.image_output is True


# ---------------------------------------------------------------------------
# 5.6 — _model_config_names helper
# ---------------------------------------------------------------------------


def test_model_config_names_string_config() -> None:
    """StringModelConfig should return its identifier."""
    config = StringModelConfig(identifier="openai:gpt-4o")
    names = _model_config_names(config)
    assert names == ["openai:gpt-4o"]


def test_model_config_names_openai_config() -> None:
    """OpenAIModelConfig should return its identifier."""
    config = OpenAIModelConfig(identifier="gpt-5-pro")
    names = _model_config_names(config)
    assert names == ["gpt-5-pro"]


def test_model_config_names_test_config() -> None:
    """_TestModelConfig has no identifier → empty list."""
    config = _TestModelConfig()
    names = _model_config_names(config)
    assert names == []


def test_model_config_names_fallback_with_strings() -> None:
    """FallbackModelConfig with plain string sub-models."""
    config = FallbackModelConfig(models=["openai:gpt-4o", "anthropic:claude-sonnet-4-5"])
    names = _model_config_names(config)
    assert names == ["openai:gpt-4o", "anthropic:claude-sonnet-4-5"]


def test_model_config_names_fallback_with_configs() -> None:
    """FallbackModelConfig with nested BaseModelConfig sub-models."""
    config = FallbackModelConfig(
        models=[
            StringModelConfig(identifier="openai:gpt-4o"),
            OpenAIModelConfig(identifier="gpt-5-pro"),
        ],
    )
    names = _model_config_names(config)
    assert names == ["openai:gpt-4o", "gpt-5-pro"]


# ---------------------------------------------------------------------------
# 5.6 — Integration: resolve_capabilities called in get_agentlet
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_resolve_capabilities_called_for_string_model() -> None:
    """get_agentlet should call resolve_capabilities with the model name."""
    config = NativeAgentConfig(
        model=StringModelConfig(
            identifier="openai:gpt-4o",
            capabilities=_caps(image_input=False),
        ),
    )
    agent = Agent(name="test", model="test", agent_config=config)

    # Mock resolve_capabilities to avoid real tokonomics calls.
    mock_caps = _text_only_caps()
    with patch(
        "agentpool.host.stubs.resolve_capabilities",
        new_callable=AsyncMock,
        return_value=mock_caps,
    ) as mock_resolve:
        await agent.get_agentlet(model=None, output_type=str)
        mock_resolve.assert_called_once()
        # First arg should be the model name.
        call_args = mock_resolve.call_args
        assert call_args[0][0] == "openai:gpt-4o"


@pytest.mark.integration
async def test_fallback_model_calls_resolve_for_each_sub_model() -> None:
    """FallbackModelConfig should call resolve_capabilities for each sub-model."""
    config = NativeAgentConfig(
        model=FallbackModelConfig(
            models=["openai:gpt-4o", "anthropic:claude-sonnet-4-5"],
            capabilities=_caps(),
        ),
    )
    agent = Agent(name="test", model="test", agent_config=config)

    # Each sub-model resolves to all-True (so no filter needed).
    mock_caps = _all_true_caps()
    with patch(
        "agentpool.host.stubs.resolve_capabilities",
        new_callable=AsyncMock,
        return_value=mock_caps,
    ) as mock_resolve:
        await agent.get_agentlet(model=None, output_type=str)
        assert mock_resolve.call_count == 2
        call_names = [call.args[0] for call in mock_resolve.call_args_list]
        assert "openai:gpt-4o" in call_names
        assert "anthropic:claude-sonnet-4-5" in call_names


@pytest.mark.integration
async def test_fallback_model_intersection_injects_filter() -> None:
    """Fallback where one sub-model lacks image should inject filter."""
    config = NativeAgentConfig(
        model=FallbackModelConfig(
            models=["openai:gpt-4o", "openai:gpt-3.5-turbo"],
            capabilities=_caps(),
        ),
    )
    agent = Agent(name="test", model="test", agent_config=config)

    # gpt-4o has image, gpt-3.5-turbo does not → intersection = False.
    async def mock_resolve(name: str, declared: Any) -> ModelCapabilities:
        if "gpt-4o" in name:
            return ModelCapabilities(
                image_input=True,
                audio_input=True,
                video_input=False,
                document_input=False,
                image_output=False,
            )
        return ModelCapabilities(
            image_input=False,
            audio_input=True,
            video_input=False,
            document_input=False,
            image_output=False,
        )

    with patch(
        "agentpool.host.stubs.resolve_capabilities",
        side_effect=mock_resolve,
    ):
        pydantic_agent = await agent.get_agentlet(model=None, output_type=str)
        filter_caps = [
            cap
            for cap in pydantic_agent.root_capability.capabilities
            if isinstance(cap, ModalityFilterCapability)
        ]
        assert len(filter_caps) == 1
        # Intersection: gpt-4o=True, gpt-3.5=False → False (pessimistic).
        assert filter_caps[0].capabilities.image_input is False
