"""Shared model utilities for AgentPool servers.

This module provides helper functions for extracting provider information,
building provider lists from tokonomics discovery, and merging configured
variants across ACP and OpenCode servers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool.models.model_configs import (
    AnthropicModelConfig,
    AnyModelConfig,
    FallbackModelConfig,
    GeminiModelConfig,
    OpenAIModelConfig,
    StringModelConfig,
)
from agentpool_server.shared.constants import (
    DEFAULT_MODEL_CONTEXT_LIMIT,
    DEFAULT_MODEL_INPUT_COST,
    DEFAULT_MODEL_OUTPUT_COST,
    DEFAULT_MODEL_OUTPUT_LIMIT,
)


if TYPE_CHECKING:
    from tokonomics.model_discovery.model_info import ModelInfo as TokoModelInfo

    from acp.schema import SessionModelState
    from agentpool.agents.base_agent import BaseAgent
    from agentpool_server.acp_server.provider_router import ProviderRouter
    from agentpool_server.opencode_server.models import Provider

logger = get_logger(__name__)


def _extract_provider_from_identifier(identifier: str) -> str:
    """Extract provider name from a model identifier string.

    Args:
        identifier: Model identifier string (e.g., "openai:gpt-4o")

    Returns:
        Provider name extracted from identifier (e.g., "openai"), or "unknown"
        if no provider prefix found.
    """
    if ":" in identifier:
        return identifier.split(":", 1)[0]
    return "unknown"


def _extract_provider(config: AnyModelConfig) -> str:  # noqa: PLR0911
    """Extract provider name from AnyModelConfig.

    Handles:
    - StringModelConfig: Extract provider from identifier (e.g., "openai:gpt-4o" -> "openai")
    - AnthropicModelConfig: Returns "anthropic"
    - OpenAIModelConfig: Returns "openai"
    - GeminiModelConfig: Returns "google"
    - FallbackModelConfig: Returns provider of first model in chain

    Args:
        config: Model configuration to extract provider from.

    Returns:
        Provider name as a string.
    """
    match config:
        case StringModelConfig(identifier=identifier):
            return _extract_provider_from_identifier(str(identifier))

        case AnthropicModelConfig():
            return "anthropic"

        case OpenAIModelConfig():
            return "openai"

        case GeminiModelConfig():
            return "google"

        case FallbackModelConfig(models=models) if models:
            first = models[0]
            if isinstance(first, str):
                return _extract_provider_from_identifier(first)
            if isinstance(
                first,
                StringModelConfig
                | AnthropicModelConfig
                | OpenAIModelConfig
                | GeminiModelConfig
                | FallbackModelConfig,
            ):
                return _extract_provider(first)
            return "unknown"

        case _:
            return "unknown"


def _resolve_variant_identifier(config: AnyModelConfig, variant_name: str) -> str:
    """Resolve a model variant config to its underlying model identifier.

    Returns the identifier in ``{system}:{model_name}`` format matching
    ``agent.model_name``, so the current model can be matched against
    configured variants.

    For StringModelConfig, resolves through ``config.get_model()`` to get
    the canonical system name (e.g., ``"openai-chat"`` → ``"openai"``).
    Falls back to the raw identifier if resolution fails.

    Args:
        config: Model variant configuration.
        variant_name: The variant name to use as fallback.

    Returns:
        Resolved model identifier string matching agent.model_name format.
    """
    if isinstance(config, StringModelConfig):
        try:
            model = config.get_model()
        except Exception:  # noqa: BLE001
            return config.identifier
        else:
            return f"{model.system}:{model.model_name}"
    return variant_name


def _build_providers_from_tokonomics(toko_models: list[TokoModelInfo]) -> list[Provider]:
    """Build providers list from tokonomics discovery results.

    Groups models by (provider, provider_display_name) and creates Provider
    objects with their associated models.

    Args:
        toko_models: List of tokonomics ModelInfo objects from discovery.

    Returns:
        List of Provider objects with models converted using Model.from_tokonomics().
    """
    from agentpool_server.opencode_server.models import Model, Provider

    providers_by_name: dict[str, Provider] = {}

    for info in toko_models:
        # Skip embedding models
        if info.is_embedding:
            continue

        provider_id = info.provider

        if provider_id not in providers_by_name:
            providers_by_name[provider_id] = Provider(
                id=provider_id,
                name=provider_id.title(),
                models={},
            )

        model_id = info.id_override or info.id
        providers_by_name[provider_id].models[model_id] = Model.from_tokonomics(info)

    return list(providers_by_name.values())


def _apply_configured_variants(
    providers: list[Provider],
    configured_variants: dict[str, dict[str, Any]],
) -> None:
    """Merge configured variants into providers list.

    Configured variants with matching IDs override discovered models.
    New configured variants are added to their respective providers.

    Args:
        providers: List of Provider objects to modify in place.
        configured_variants: Dictionary mapping variant names to their
            configuration dictionaries. Each config dict should have a
            "provider" key indicating which provider the variant belongs to.

    Note:
        This function modifies the providers list in place. New providers
        are created if a configured variant references a non-existent provider.
    """
    from agentpool_server.opencode_server.models import (
        Model,
        ModelCost,
        ModelLimit,
        Provider,
        ProviderCapabilities,
    )

    # Build lookup for provider name -> Provider object
    provider_lookup: dict[str, Provider] = {}
    for provider in providers:
        provider_lookup[provider.id.lower()] = provider

    for variant_name, variant_config in configured_variants.items():
        provider_name = variant_config.get("provider", "unknown").lower()

        if provider_name not in provider_lookup:
            # Create new provider entry for this variant
            provider_lookup[provider_name] = Provider(
                id=provider_name,
                name=provider_name.title(),
                models={},
            )
            providers.append(provider_lookup[provider_name])

        provider = provider_lookup[provider_name]

        # Check if model with this ID already exists
        if variant_name in provider.models:
            # Override existing (configured takes precedence)
            existing = provider.models[variant_name]
            existing.name = variant_name
            existing.capabilities.attachment = True  # Enable multimodal support
            # Note: variant-specific settings (temp, thinking) not exposed to client
        else:
            # Add new model - use a minimal Model creation
            provider.models[variant_name] = Model(
                id=variant_name,
                name=variant_name,
                capabilities=ProviderCapabilities(attachment=True),
                cost=ModelCost(
                    input=DEFAULT_MODEL_INPUT_COST,
                    output=DEFAULT_MODEL_OUTPUT_COST,
                ),
                limit=ModelLimit(
                    context=DEFAULT_MODEL_CONTEXT_LIMIT,
                    output=DEFAULT_MODEL_OUTPUT_LIMIT,
                ),
            )


async def build_model_state_for_acp(
    agent: BaseAgent[Any, Any],
    provider_router: ProviderRouter | None,
) -> SessionModelState | None:
    """Build SessionModelState for ACP with configured-first, tokonomics-fallback logic.

    1. Checks agent's pool manifest for configured model_variants
    2. Builds ACPModelInfo list from configured variants
    3. Filters out disabled providers via provider_router
    4. If configured list is non-empty → returns SessionModelState
    5. If empty → falls back to agent.get_available_models() (tokonomics)
    6. If both empty → returns None

    Args:
        agent: The agent to build model state for.
        provider_router: Optional provider router for disable filtering.

    Returns:
        SessionModelState with available models, or None if no models found.
    """
    from acp.schema import ModelInfo as ACPModelInfo, SessionModelState

    # Phase 1: Configured variants from manifest (configured-first)
    configured_models: list[ACPModelInfo] = []
    agent_pool = agent.host_context
    manifest = agent_pool.manifest if agent_pool else None

    if manifest and manifest.model_variants:
        for variant_name, config in manifest.model_variants.items():
            provider_name = _extract_provider(config)

            # Skip if provider is disabled
            if provider_router and provider_router.is_provider_disabled(provider_name):
                continue

            # Resolve the actual model identifier for the model_id field,
            # using variant name as the display name (alias).
            model_id = _resolve_variant_identifier(config, variant_name)
            configured_models.append(
                ACPModelInfo(
                    model_id=model_id,
                    name=variant_name,
                )
            )

    if configured_models:
        current_model = agent.model_name
        all_ids = [m.model_id for m in configured_models]
        if current_model and current_model in all_ids:
            current_model_id = current_model
        elif current_model:
            # Current model is not among configured variants — add it
            desc = "Currently configured model"
            model_info = ACPModelInfo(model_id=current_model, name=current_model, description=desc)
            configured_models.insert(0, model_info)
            current_model_id = current_model
        else:
            current_model_id = all_ids[0]
        return SessionModelState(
            available_models=configured_models,
            current_model_id=current_model_id,
        )

    # Phase 2: Tokonomics fallback
    try:
        toko_models = await agent.get_available_models()
    except Exception:
        logger.exception("Failed to get available models from agent")
        return None

    if not toko_models:
        return None

    # Filter disabled providers from raw tokonomics models (more accurate than parsing model_id)
    if provider_router:
        toko_models = [
            toko for toko in toko_models if not provider_router.is_provider_disabled(toko.provider)
        ]

    if not toko_models:
        return None

    acp_models_from_tokonomics = [
        ACPModelInfo(
            model_id=toko.id_override if toko.id_override else toko.id,
            name=toko.name,
            description=toko.description or "",
        )
        for toko in toko_models
    ]

    if not acp_models_from_tokonomics:
        return None

    all_ids = [m.model_id for m in acp_models_from_tokonomics]
    current_model = agent.model_name
    current_model_id = current_model if current_model and current_model in all_ids else all_ids[0]

    return SessionModelState(
        available_models=acp_models_from_tokonomics,
        current_model_id=current_model_id,
    )
