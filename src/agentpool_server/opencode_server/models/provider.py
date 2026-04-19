"""Provider, model, and mode related models."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from pydantic import Field, model_validator

from agentpool_server.opencode_server.models.base import OpenCodeBaseModel
from agentpool_server.opencode_server.models.common import ModelRef  # noqa: TC001


if TYPE_CHECKING:
    from tokonomics.model_discovery.model_info import ModelInfo as TokoModelInfo


class CostCache(OpenCodeBaseModel):
    """Cache cost information."""

    read: float = 0.0
    write: float = 0.0


class ModelCost(OpenCodeBaseModel):
    """Cost information for a model."""

    input: float
    output: float
    cache: CostCache = Field(default_factory=CostCache)


class ProviderModalities(OpenCodeBaseModel):
    """Modalities supported by a model (boolean flags).

    Matches opencode's ProviderModalities schema where each modality
    is a boolean flag rather than a list of strings.
    """

    text: bool = True
    audio: bool = False
    image: bool = False
    video: bool = False
    pdf: bool = False


class ProviderApiInfo(OpenCodeBaseModel):
    """API connection information for a model's provider."""

    id: str = ""
    url: str = ""
    npm: str = ""


class ProviderCapabilities(OpenCodeBaseModel):
    """Model capabilities.

    Matches opencode's ProviderCapabilities schema with nested
    input/output modalities as boolean-flag objects and an
    interleaved field for thinking interleaving support.
    """

    attachment: bool = False
    reasoning: bool = False
    temperature: bool = True
    tool_call: bool = Field(default=True, alias="toolcall")
    input: ProviderModalities = Field(default_factory=ProviderModalities)
    output: ProviderModalities = Field(default_factory=ProviderModalities)
    interleaved: bool = False


class ModelLimit(OpenCodeBaseModel):
    """Limit information for a model."""

    context: float
    output: float


class Model(OpenCodeBaseModel):
    """Model information."""

    id: str
    name: str
    provider_id: str = ""
    api: ProviderApiInfo = Field(default_factory=ProviderApiInfo)
    capabilities: ProviderCapabilities = Field(default_factory=ProviderCapabilities)
    cost: ModelCost
    limit: ModelLimit
    status: str = "active"
    options: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    release_date: str = ""
    variants: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Model variants for reasoning/thinking levels.

    Maps variant names (e.g., 'low', 'medium', 'high', 'max') to
    provider-specific configuration options. The TUI uses this to
    let users cycle through thinking effort levels.
    """

    @classmethod
    def from_tokonomics(cls, model: TokoModelInfo) -> Self:
        """Convert a tokonomics ModelInfo to an OpenCode Model."""
        from tokonomics.model_discovery.model_info import ModelPricing

        pricing = model.pricing or ModelPricing()
        cost = ModelCost(
            input=(pricing.prompt * 1_000_000) if pricing.prompt else 0.0,
            output=(pricing.completion * 1_000_000) if pricing.completion else 0.0,
            cache=CostCache(
                read=(pricing.input_cache_read * 1_000_000) if pricing.input_cache_read else 0.0,
                write=(pricing.input_cache_write * 1_000_000) if pricing.input_cache_write else 0.0,
            ),
        )
        # Convert limits
        context = float(model.context_window) if model.context_window else 128000.0
        output = float(model.max_output_tokens) if model.max_output_tokens else 4096.0
        # Build modalities from tokonomics data (convert to boolean flags)
        input_mods = [str(m) for m in model.input_modalities] if model.input_modalities else []
        output_mods = [str(m) for m in model.output_modalities] if model.output_modalities else []
        # Use id_override if available (e.g., "opus" for Claude Code SDK)
        return cls(
            id=model.id_override or model.id,
            name=model.name,
            capabilities=ProviderCapabilities(
                attachment=False,
                reasoning="reasoning" in model.output_modalities
                or "thinking" in model.name.lower(),
                temperature=True,
                input=ProviderModalities(
                    text=True,
                    audio="audio" in input_mods,
                    image="image" in input_mods,
                    video="video" in input_mods,
                    pdf="pdf" in input_mods,
                ),
                output=ProviderModalities(
                    text=True,
                    audio="audio" in output_mods,
                    image="image" in output_mods,
                    video="video" in output_mods,
                    pdf="pdf" in output_mods,
                ),
            ),
            cost=cost,
            limit=ModelLimit(context=context, output=output),
            release_date=model.created_at.strftime("%Y-%m-%d") if model.created_at else "",
        )


class Provider(OpenCodeBaseModel):
    """Provider information.

    Matches opencode's Provider.Info schema which requires source and options.
    """

    id: str
    name: str
    source: str = "config"
    """Provider source: 'env', 'config', 'custom', or 'api'."""

    env: list[str] = Field(default_factory=list)
    key: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    models: dict[str, Model] = Field(default_factory=dict)
    api: str | None = None
    npm: str | None = None

    @model_validator(mode="after")
    def _populate_model_refs(self) -> Self:
        """Auto-populate provider_id and api on all models."""
        for model in self.models.values():
            model.provider_id = self.id
            if not model.api.id:
                model.api = ProviderApiInfo(
                    id=self.api or "",
                    npm=self.npm or "",
                )
        return self


class ProvidersResponse(OpenCodeBaseModel):
    """Response for /config/providers endpoint."""

    providers: list[Provider]
    default: dict[str, str] = Field(default_factory=dict)


class ProviderListResponse(OpenCodeBaseModel):
    """Response for /provider endpoint."""

    all: list[Provider]
    default: dict[str, str] = Field(default_factory=dict)
    connected: list[str] = Field(default_factory=list)


class Mode(OpenCodeBaseModel):
    """Agent mode configuration."""

    name: str
    tools: dict[str, bool] = Field(default_factory=dict)
    model: ModelRef | None = None
    prompt: str | None = None
    temperature: float | None = None
