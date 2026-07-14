"""Model configuration.

Replacement for llmling_models_config.configs.
Provides Pydantic-based model configurations for agentpool agents.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import ConfigDict, Field, ImportString
from pydantic_ai import ModelSettings as PyAIModelSettings
from schemez import Schema
from tokonomics.model_names import ModelId
from tokonomics.model_names.anthropic import AnthropicModelName
from tokonomics.model_names.gemini import GeminiModelName
from tokonomics.model_names.openai import OpenaiModelName


if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.models.anthropic import AnthropicModelSettings
    from pydantic_ai.models.fallback import FallbackModel
    from pydantic_ai.models.gemini import GeminiModelSettings
    from pydantic_ai.models.openai import OpenAIResponsesModelSettings


class BaseModelConfig(Schema):
    """Base for model configurations."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Base model"})

    type: str = Field(init=False)
    """Type discriminator for model configs."""

    def get_model(self) -> Model:
        """Create and return actual model instance."""
        msg = f"Model creation not implemented for {self.__class__.__name__}"
        raise NotImplementedError(msg)

    def get_model_settings(self) -> PyAIModelSettings:
        """Return model settings as a dictionary."""
        return PyAIModelSettings()


class PrePostPromptConfig(Schema):
    """Configuration for pre/post prompts."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Pre/post prompt"})

    text: str = Field(
        examples=["You are a helpful assistant", "Process this carefully"],
        title="Prompt text",
    )
    """The prompt text to be applied."""

    model: ModelId | BaseModelConfig | str = Field(
        examples=[["openai:gpt-5-nano", "anthropic:claude-sonnet-4-5"]],
        title="Model identifier",
    )
    """The model to use for processing the prompt."""


class StringModelConfig(BaseModelConfig):
    """Configuration for string-based model references."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "String model"})

    type: Literal["string"] = Field(default="string", init=False)
    """Type identifier for string model."""

    identifier: ModelId | str = Field(
        examples=["openai:gpt-5-nano", "anthropic:claude-sonnet-4-5"],
        title="Model identifier",
    )
    """String identifier for the model."""

    max_tokens: int | None = Field(
        default=None,
        ge=1,
        examples=[1024, 2048, 4096],
        title="Maximum tokens",
    )
    """The maximum number of tokens to generate before stopping."""

    temperature: float | None = Field(
        default=None,
        ge=0.0,
        examples=[0.0, 0.7, 1.0, 2.0],
        title="Temperature",
    )
    """Amount of randomness injected into the response."""

    top_p: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        examples=[0.1, 0.9, 1.0],
        title="Top-p (nucleus sampling)",
    )
    """An alternative to sampling with temperature, called nucleus sampling."""

    timeout: float | None = Field(
        default=None,
        ge=0.0,
        examples=[30.0, 60.0, 120.0],
        title="Request timeout",
    )
    """Override the client-level default timeout for a request, in seconds."""

    parallel_tool_calls: bool | None = Field(
        default=None,
        title="Allow parallel tool calls",
    )
    """Whether to allow parallel tool calls."""

    seed: int | None = Field(
        default=None,
        examples=[42, 123, 999],
        title="Random seed",
    )
    """The random seed to use for the model, theoretically allowing for deterministic results."""

    presence_penalty: float | None = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        examples=[-1.0, 0.0, 0.5, 1.0],
        title="Presence penalty",
    )
    """Penalize new tokens based on whether they have appeared in the text so far."""

    frequency_penalty: float | None = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        examples=[-1.0, 0.0, 0.5, 1.0],
        title="Frequency penalty",
    )
    """Penalize new tokens based on their existing frequency in the text so far."""

    logit_bias: dict[str, int] | None = Field(
        default=None,
        title="Logit bias",
        examples=[{"5678": -100}, {"1234": 100}],
    )
    """Modify the likelihood of specified tokens appearing in the completion."""

    stop_sequences: list[str] | None = Field(
        default=None,
        examples=[["STOP", "END"], ["\n\n"]],
        title="Stop sequences",
    )
    """Sequences that will cause the model to stop generating."""

    extra_headers: dict[str, str] | None = Field(
        default=None,
        examples=[{"Custom-Header": "value"}],
        title="Extra headers",
    )
    """Extra headers to send to the model."""

    extra_body: Any | None = Field(
        default=None,
        title="Extra body",
    )
    """Extra body to send to the model."""

    def get_model_settings(self) -> PyAIModelSettings:
        """Get model settings in pydantic-ai format."""
        from pydantic_ai.settings import ModelSettings

        settings = {
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "timeout": self.timeout,
            "parallel_tool_calls": self.parallel_tool_calls,
            "seed": self.seed,
            "presence_penalty": self.presence_penalty,
            "frequency_penalty": self.frequency_penalty,
            "logit_bias": self.logit_bias,
            "stop_sequences": self.stop_sequences,
            "extra_headers": self.extra_headers,
            "extra_body": self.extra_body,
        }
        return ModelSettings(**{k: v for k, v in settings.items() if v is not None})  # type: ignore[typeddict-item, no-any-return]

    def get_model(self) -> Model:
        from agentpool.utils.model_helpers import infer_model

        return infer_model(self.identifier)


class ImportModelConfig(BaseModelConfig):
    """Configuration for importing external models."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Import model"})

    type: Literal["import"] = Field(default="import", init=False)
    """Type identifier for import model."""

    model: ImportString[Any] = Field(
        examples=["my_models.CustomModel"],
        title="Model import path",
    )
    """Import path to the model class or function."""

    kw_args: dict[str, str] = Field(default_factory=dict, title="Model arguments")
    """Keyword arguments to pass to the imported model."""

    def get_model(self) -> Any:
        return self.model(**self.kw_args) if isinstance(self.model, type) else self.model


class FunctionModelConfig(BaseModelConfig):
    """Configuration for function-based model references."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Function model"})

    type: Literal["function"] = Field(default="function", init=False)
    """Type identifier for function model."""

    function: ImportString[Callable[..., Any]] = Field(title="Function import path")
    """Function identifier for the model."""

    def get_model(self) -> Any:
        from agentpool.utils.model_helpers import function_to_model

        return function_to_model(self.function)


class InputModelConfig(BaseModelConfig):
    """Configuration for human input model."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Input model"})

    type: Literal["input"] = Field(default="input", init=False)
    """Type identifier for input model."""

    prompt_template: str = Field(
        default="👤 Please respond to: {prompt}",
        examples=["👤 Please respond to: {prompt}", "User input required: {prompt}"],
        title="Prompt display template",
    )
    """Template for displaying the prompt to the user."""

    show_system: bool = Field(default=True, title="Show system messages")
    """Whether to show system messages."""

    input_prompt: str = Field(
        default="Your response: ",
        examples=["Your response: ", "Enter reply: "],
        title="Input request text",
    )
    """Text displayed when requesting input."""

    handler: ImportString[Any] = Field(
        default="llmling_models:DefaultInputHandler",
        validate_default=True,
        title="Input handler",
    )
    """Handler for processing user input."""

    def get_model(self) -> Any:
        """InputModel requires llmling-models which is no longer a dependency."""
        msg = "InputModel requires llmling-models"
        raise NotImplementedError(msg)


class FallbackModelConfig(BaseModelConfig):
    """Configuration for fallback strategy."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Fallback model"})

    type: Literal["fallback"] = Field(default="fallback", init=False)
    """Type identifier for fallback model."""

    models: list[ModelId | str | BaseModelConfig] = Field(
        min_length=1,
        title="Fallback models",
        examples=[["openai:gpt-5-nano", "anthropic:claude-sonnet-4-5"]],
    )
    """Ordered list of models to try in sequence."""

    def get_model(self) -> FallbackModel:
        from pydantic_ai.models.fallback import FallbackModel

        # Convert nested configs to models
        converted_models = [
            m.get_model()
            if isinstance(m, BaseModelConfig)
            else StringModelConfig(identifier=m).get_model()
            for m in self.models
        ]
        return FallbackModel(*converted_models)


class TestModelConfig(BaseModelConfig):
    """Configuration for test models."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Test model"})

    type: Literal["test"] = Field(default="test", init=False)
    """Type identifier for test model."""

    custom_output_text: str | None = Field(
        default=None,
        examples=["Test response", "Mock output for testing"],
        title="Custom output text",
    )
    """Optional custom text to return from the test model."""

    call_tools: list[str] | Literal["all"] = Field(
        default="all",
        examples=["all", ["tool1", "tool2"]],
        title="Available tools",
    )
    """Tools that can be called by the test model."""

    tool_args: dict[str, dict[str, Any]] | None = Field(
        default=None,
        examples=[{"read": {"path": "/test/file.txt"}}],
        title="Fixed tool arguments",
    )
    """Optional mapping of tool_name -> args to use instead of generated args."""

    seed: int = Field(default=0, title="Random seed")
    """Seed for generating random tool arguments (when tool_args not specified)."""

    def get_model(self) -> Any:
        if self.tool_args:
            from agentpool.models.fixed_args_test_model import FixedArgsTestModel

            return FixedArgsTestModel(
                tool_args=self.tool_args,
                custom_output_text=self.custom_output_text,
                call_tools=self.call_tools,
                seed=self.seed,
            )
        from pydantic_ai.models.test import TestModel

        return TestModel(
            custom_output_text=self.custom_output_text,
            call_tools=self.call_tools,
            seed=self.seed,
        )


class OpenAIModelConfig(BaseModelConfig):
    """Configuration for OpenAI models."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "OpenAI model"})

    type: Literal["openai"] = Field(default="openai", init=False)
    """Type identifier for OpenAI model."""

    identifier: OpenaiModelName = Field(examples=["gpt-4", "gpt-4-turbo"], title="Model identifier")
    """String identifier for the model."""

    max_tokens: int | None = Field(
        default=None,
        ge=1,
        examples=[1024, 2048, 4096],
        title="Maximum tokens",
    )
    """The maximum number of tokens to generate before stopping."""

    temperature: float | None = Field(
        default=None,
        ge=0.0,
        examples=[0.0, 0.7, 1.0, 2.0],
        title="Temperature",
    )
    """Amount of randomness injected into the response."""

    top_p: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        examples=[0.1, 0.9, 1.0],
        title="Top-p (nucleus sampling)",
    )
    """An alternative to sampling with temperature, called nucleus sampling."""

    timeout: float | None = Field(
        default=None,
        examples=[30.0, 60.0, 120.0],
        title="Request timeout",
    )
    """Override the client-level default timeout for a request, in seconds."""

    parallel_tool_calls: bool | None = Field(
        default=None,
        title="Allow parallel tool calls",
    )
    """Whether to allow parallel tool calls."""

    seed: int | None = Field(
        default=None,
        examples=[42, 123, 999],
        title="Random seed",
    )
    """The random seed to use for the model, theoretically allowing for deterministic results."""

    presence_penalty: float | None = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        examples=[-1.0, 0.0, 0.5, 1.0],
        title="Presence penalty",
    )
    """Penalize new tokens based on whether they have appeared in the text so far."""

    frequency_penalty: float | None = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        examples=[-1.0, 0.0, 0.5, 1.0],
        title="Frequency penalty",
    )
    """Penalize new tokens based on their existing frequency in the text so far."""

    logit_bias: dict[str, int] | None = Field(
        default=None,
        title="Logit bias",
        examples=[{"5678": -100}, {"1234": 100}],
    )
    """Modify the likelihood of specified tokens appearing in the completion."""

    stop_sequences: list[str] | None = Field(
        default=None,
        examples=[["STOP", "END"], ["\n\n"]],
        title="Stop sequences",
    )
    """Sequences that will cause the model to stop generating."""

    extra_headers: dict[str, str] | None = Field(
        default=None,
        examples=[{"Custom-Header": "value"}],
        title="Extra headers",
    )
    """Extra headers to send to the model."""

    extra_body: Any | None = Field(
        default=None,
        title="Extra body",
    )
    """Extra body to send to the model."""

    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = Field(
        default=None,
        title="Reasoning effort",
    )
    """Constrains effort on reasoning for reasoning models."""

    logprobs: bool | None = Field(
        default=None,
        title="Include log probabilities",
    )
    """Include log probabilities in the response."""

    top_logprobs: int | None = Field(
        default=None,
        ge=0,
        le=20,
        title="Top log probabilities",
    )
    """Include log probabilities of the top n tokens in the response."""

    user: str | None = Field(
        default=None,
        title="User identifier",
    )
    """A unique identifier representing the end-user."""

    service_tier: Literal["auto", "default", "flex", "priority"] | None = Field(
        default=None,
        title="Service tier",
    )
    """The service tier to use for the model request."""

    prompt_cache_key: str | None = Field(
        default=None,
        title="Prompt cache key",
    )
    """Used by OpenAI to cache responses for similar requests to optimize your cache hit rates.

    See the [OpenAI Prompt Caching documentation](https://platform.openai.com/docs/guides/prompt-caching#how-it-works) for more information.
    """  # noqa: E501

    prompt_cache_retention: Literal["in-memory", "24h"] | None = Field(
        default=None,
        title="Prompt cache retention",
    )
    """The retention policy for the prompt cache. Set to 24h to enable extended prompt caching, which keeps cached prefixes active for longer, up to a maximum of 24 hours.

    See the [OpenAI Prompt Caching documentation](https://platform.openai.com/docs/guides/prompt-caching#how-it-works) for more information.
    """  # noqa: E501

    prediction: dict[str, Any] | None = Field(
        default=None,
        title="Predicted output",
        examples=[{"type": "content", "content": "predicted response text"}],
    )
    """Predicted output for the model to use as a starting point.

    Can be a simple string content or structured with text parts:
    - Simple: {"type": "content", "content": "predicted text"}
    - Parts: {"type": "content", "content": [{"type": "text", "text": "predicted"}]}
    """

    # Responses API specific settings
    builtin_tools: list[dict[str, Any]] | None = Field(
        default=None,
        title="Built-in tools",
    )
    """The provided OpenAI built-in tools to use (file_search, web_search, computer).

    See [OpenAI's built-in tools](https://platform.openai.com/docs/guides/tools?api-mode=responses)
    for more details.
    """

    reasoning_summary: Literal["detailed", "concise", "auto"] | None = Field(
        default=None,
        title="Reasoning summary",
    )
    """A summary of the reasoning performed by the model.

    This can be useful for debugging and understanding the model's reasoning process.
    One of `concise`, `detailed`, or `auto`.
    """

    send_reasoning_ids: bool | None = Field(
        default=None,
        title="Send reasoning IDs",
    )
    """Whether to send the unique IDs of reasoning, text, and function call parts from the message
    history to the model.

    Enabled by default for reasoning models. Disable if you get errors about items not matching.
    """

    truncation: Literal["disabled", "auto"] | None = Field(
        default=None,
        title="Truncation strategy",
    )
    """The truncation strategy to use for the model response.

    - `disabled` (default): Request fails if response exceeds context window.
    - `auto`: Model truncates by dropping input items in the middle of the conversation.
    """

    text_verbosity: Literal["low", "medium", "high"] | None = Field(
        default=None,
        title="Text verbosity",
    )
    """Constrains the verbosity of the model's text response.

    Lower values will result in more concise responses, while higher values will
    result in more verbose responses.
    """

    previous_response_id: Literal["auto"] | str | None = Field(  # noqa: PYI051
        default=None,
        title="Previous response ID",
    )
    """The ID of a previous response to use as the starting point for a continued conversation.

    When set to `'auto'`, the request automatically uses the most recent provider_response_id.
    """

    include_code_execution_outputs: bool | None = Field(
        default=None,
        title="Include code execution outputs",
    )
    """Whether to include the code execution results in the response."""

    include_web_search_sources: bool | None = Field(
        default=None,
        title="Include web search sources",
    )
    """Whether to include the web search results in the response."""

    include_file_search_results: bool | None = Field(
        default=None,
        title="Include file search results",
    )
    """Whether to include the file search results in the response."""

    def get_model_settings(self) -> OpenAIResponsesModelSettings:
        """Get model settings in pydantic-ai format."""
        from pydantic_ai.models.openai import OpenAIResponsesModelSettings

        settings = {
            # Base model settings
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "timeout": self.timeout,
            "parallel_tool_calls": self.parallel_tool_calls,
            "seed": self.seed,
            "presence_penalty": self.presence_penalty,
            "frequency_penalty": self.frequency_penalty,
            "logit_bias": self.logit_bias,
            "stop_sequences": self.stop_sequences,
            "extra_headers": self.extra_headers,
            "extra_body": self.extra_body,
            # OpenAI Chat settings
            "openai_reasoning_effort": self.reasoning_effort,
            "openai_logprobs": self.logprobs,
            "openai_top_logprobs": self.top_logprobs,
            "openai_user": self.user,
            "openai_service_tier": self.service_tier,
            "openai_prompt_cache_key": self.prompt_cache_key,
            "openai_prompt_cache_retention": self.prompt_cache_retention,
            "openai_prediction": self.prediction,
            # Responses API specific settings
            "openai_builtin_tools": self.builtin_tools,
            "openai_reasoning_summary": self.reasoning_summary,
            "openai_send_reasoning_ids": self.send_reasoning_ids,
            "openai_truncation": self.truncation,
            "openai_text_verbosity": self.text_verbosity,
            "openai_previous_response_id": self.previous_response_id,
            "openai_include_code_execution_outputs": self.include_code_execution_outputs,
            "openai_include_web_search_sources": self.include_web_search_sources,
            "openai_include_file_search_results": self.include_file_search_results,
        }
        return OpenAIResponsesModelSettings(**{k: v for k, v in settings.items() if v is not None})  # type: ignore[typeddict-item, no-any-return]

    def get_model(self) -> Any:
        from agentpool.utils.model_helpers import infer_model

        return infer_model("openai:" + self.identifier)


class AnthropicModelConfig(BaseModelConfig):
    """Configuration for Anthropic models."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Anthropic model"})

    type: Literal["anthropic"] = Field(default="anthropic", init=False)
    """Type identifier for Anthropic model."""

    identifier: AnthropicModelName = Field(
        examples=["claude-3-opus", "claude-3-sonnet"],
        title="Model identifier",
    )
    """String identifier for the model."""

    auth_method: Literal["api_key", "oauth"] = Field(
        default="api_key",
        title="Authentication method",
        description="Use 'oauth' for Claude Max/Pro subscription authentication",
    )
    """Authentication method: 'api_key' (default) or 'oauth' for Claude Max/Pro."""

    max_tokens: int | None = Field(
        default=None,
        ge=1,
        examples=[1024, 2048, 4096],
        title="Maximum tokens",
    )
    """The maximum number of tokens to generate before stopping."""

    temperature: float | None = Field(
        default=None,
        ge=0.0,
        examples=[0.0, 0.7, 1.0, 2.0],
        title="Temperature",
    )
    """Amount of randomness injected into the response."""

    top_p: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        examples=[0.1, 0.9, 1.0],
        title="Top-p (nucleus sampling)",
    )
    """An alternative to sampling with temperature, called nucleus sampling."""

    timeout: float | None = Field(
        default=None,
        examples=[30.0, 60.0, 120.0],
        title="Request timeout",
    )
    """Override the client-level default timeout for a request, in seconds."""

    parallel_tool_calls: bool | None = Field(
        default=None,
        title="Allow parallel tool calls",
    )
    """Whether to allow parallel tool calls."""

    seed: int | None = Field(
        default=None,
        examples=[42, 123, 999],
        title="Random seed",
    )
    """The random seed to use for the model, theoretically allowing for deterministic results."""

    presence_penalty: float | None = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        examples=[-1.0, 0.0, 0.5, 1.0],
        title="Presence penalty",
    )
    """Penalize new tokens based on whether they have appeared in the text so far."""

    frequency_penalty: float | None = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        examples=[-1.0, 0.0, 0.5, 1.0],
        title="Frequency penalty",
    )
    """Penalize new tokens based on their existing frequency in the text so far."""

    logit_bias: dict[str, int] | None = Field(
        default=None,
        title="Logit bias",
        examples=[{"5678": -100}, {"1234": 100}],
    )
    """Modify the likelihood of specified tokens appearing in the completion."""

    stop_sequences: list[str] | None = Field(
        default=None,
        examples=[["STOP", "END"], ["\n\n"]],
        title="Stop sequences",
    )
    """Sequences that will cause the model to stop generating."""

    extra_headers: dict[str, str] | None = Field(
        default=None,
        examples=[{"Custom-Header": "value"}],
        title="Extra headers",
    )
    """Extra headers to send to the model."""

    extra_body: Any | None = Field(
        default=None,
        title="Extra body",
    )
    """Extra body to send to the model."""

    metadata: dict[str, Any] | None = Field(
        default=None,
        title="Request metadata",
    )
    """An object describing metadata about the request."""

    cache_tool_definitions: bool | Literal["5m", "1h"] | None = Field(
        default=None,
        title="Cache tool definitions",
    )
    """Whether to add cache_control to the last tool definition."""

    cache_instructions: bool | Literal["5m", "1h"] | None = Field(
        default=None,
        title="Cache instructions",
    )
    """Whether to add cache_control to the last system prompt block."""

    cache_messages: bool | Literal["5m", "1h"] | None = Field(
        default=None,
        title="Cache messages",
    )
    """Convenience setting to enable caching for the last user message."""

    thinking_budget: int | None = Field(
        default=None,
        ge=1024,
        examples=[10000, 50000, 100000],
        title="Thinking budget tokens",
    )
    """Budget tokens for extended thinking mode.

    When set, enables Claude's extended thinking capability, allowing the model
    to reason through complex problems before responding. Higher values allow
    for more thorough reasoning but increase latency and cost.
    """

    container: dict[str, Any] | Literal[False] | None = Field(
        default=None,
        title="Container sandbox",
        examples=[
            {"id": "container-123"},
            {"id": "my-container", "skills": [{"skill_id": "computer", "type": "anthropic"}]},
            False,
        ],
    )
    """Container sandbox configuration for Claude.

    Enables running Claude in a sandboxed container environment with optional skills.
    Set to False to explicitly disable container mode, or provide config dict:
    - id: Container identifier
    - skills: List of skills with skill_id, type ('anthropic' or 'custom'), and version
    """

    def get_model_settings(self) -> AnthropicModelSettings:
        """Get model settings in pydantic-ai format."""
        from pydantic_ai.models.anthropic import AnthropicModelSettings

        settings: dict[str, Any] = {
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "timeout": self.timeout,
            "parallel_tool_calls": self.parallel_tool_calls,
            "seed": self.seed,
            "presence_penalty": self.presence_penalty,
            "frequency_penalty": self.frequency_penalty,
            "logit_bias": self.logit_bias,
            "stop_sequences": self.stop_sequences,
            "extra_headers": self.extra_headers,
            "extra_body": self.extra_body,
            "anthropic_metadata": self.metadata,
            "anthropic_cache_tool_definitions": self.cache_tool_definitions,
            "anthropic_cache_instructions": self.cache_instructions,
            "anthropic_cache_messages": self.cache_messages,
        }
        # Add thinking config if budget is set
        if self.thinking_budget is not None:
            settings["anthropic_thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget,
            }
        # Add container config if set (can be dict or False)
        if self.container is not None:
            settings["anthropic_container"] = self.container
        return AnthropicModelSettings(**{k: v for k, v in settings.items() if v is not None})  # type: ignore[typeddict-item, no-any-return]

    def get_model(self) -> Any:
        if self.auth_method == "oauth":
            from pydantic_ai.models.anthropic import AnthropicModel

            from agentpool.auth.anthropic_auth import (
                AnthropicMaxProvider,
            )

            provider = AnthropicMaxProvider()
            return AnthropicModel(self.identifier, provider=provider)  # type: ignore[arg-type]

        from agentpool.utils.model_helpers import infer_model

        return infer_model(self.identifier)


class GeminiModelConfig(BaseModelConfig):
    """Configuration for Gemini models."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Gemini model"})

    type: Literal["gemini"] = Field(default="gemini", init=False)
    """Type identifier for Gemini model."""

    identifier: GeminiModelName = Field(
        examples=["gemini-2.0-flash", "gemini-1.5-pro"],
        title="Model identifier",
    )
    """String identifier for the model."""

    max_tokens: int | None = Field(
        default=None,
        ge=1,
        examples=[1024, 2048, 4096],
        title="Maximum tokens",
    )
    """The maximum number of tokens to generate before stopping."""

    temperature: float | None = Field(
        default=None,
        ge=0.0,
        examples=[0.0, 0.7, 1.0, 2.0],
        title="Temperature",
    )
    """Amount of randomness injected into the response."""

    top_p: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        examples=[0.1, 0.9, 1.0],
        title="Top-p (nucleus sampling)",
    )
    """An alternative to sampling with temperature, called nucleus sampling."""

    timeout: float | None = Field(
        default=None,
        examples=[30.0, 60.0, 120.0],
        title="Request timeout",
    )
    """Override the client-level default timeout for a request, in seconds."""

    parallel_tool_calls: bool | None = Field(
        default=None,
        title="Allow parallel tool calls",
    )
    """Whether to allow parallel tool calls."""

    seed: int | None = Field(
        default=None,
        examples=[42, 123, 999],
        title="Random seed",
    )
    """The random seed to use for the model, theoretically allowing for deterministic results."""

    presence_penalty: float | None = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        examples=[-1.0, 0.0, 0.5, 1.0],
        title="Presence penalty",
    )
    """Penalize new tokens based on whether they have appeared in the text so far."""

    frequency_penalty: float | None = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        examples=[-1.0, 0.0, 0.5, 1.0],
        title="Frequency penalty",
    )
    """Penalize new tokens based on their existing frequency in the text so far."""

    logit_bias: dict[str, int] | None = Field(
        default=None,
        title="Logit bias",
        examples=[{"5678": -100}, {"1234": 100}],
    )
    """Modify the likelihood of specified tokens appearing in the completion."""

    stop_sequences: list[str] | None = Field(
        default=None,
        examples=[["STOP", "END"], ["\n\n"]],
        title="Stop sequences",
    )
    """Sequences that will cause the model to stop generating."""

    extra_headers: dict[str, str] | None = Field(
        default=None,
        examples=[{"Custom-Header": "value"}],
        title="Extra headers",
    )
    """Extra headers to send to the model."""

    extra_body: Any | None = Field(
        default=None,
        title="Extra body",
    )
    """Extra body to send to the model."""

    safety_settings: list[dict[str, Any]] | None = Field(
        default=None,
        title="Safety settings",
    )
    """Safety settings options for Gemini model request."""

    thinking_config: dict[str, Any] | None = Field(
        default=None,
        title="Thinking configuration",
    )
    """Thinking features configuration."""

    labels: dict[str, str] | None = Field(
        default=None,
        title="Vertex AI labels",
    )
    """User-defined metadata to break down billed charges."""

    def get_model_settings(self) -> GeminiModelSettings:
        """Get model settings in pydantic-ai format."""
        from pydantic_ai.models.gemini import GeminiModelSettings

        settings = {
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "timeout": self.timeout,
            "parallel_tool_calls": self.parallel_tool_calls,
            "seed": self.seed,
            "presence_penalty": self.presence_penalty,
            "frequency_penalty": self.frequency_penalty,
            "logit_bias": self.logit_bias,
            "stop_sequences": self.stop_sequences,
            "extra_headers": self.extra_headers,
            "extra_body": self.extra_body,
            "gemini_safety_settings": self.safety_settings,
            "gemini_thinking_config": self.thinking_config,
            "gemini_labels": self.labels,
        }
        return GeminiModelSettings(**{k: v for k, v in settings.items() if v is not None})  # type: ignore[typeddict-item, no-any-return]

    def get_model(self) -> Any:
        from agentpool.utils.model_helpers import infer_model

        return infer_model("gemini:" + self.identifier)


class ModelSettings(Schema):
    """Settings to configure an LLM."""

    model_config = ConfigDict(json_schema_extra={"x-doc-title": "Model settings"})

    max_output_tokens: int | None = Field(
        default=None,
        examples=[1024, 2048, 4096],
        title="Maximum output tokens",
    )
    """The maximum number of tokens to generate."""

    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        examples=[0.7, 1.0, 1.5],
        title="Temperature",
    )
    """Amount of randomness in the response (0.0 - 2.0)."""

    top_p: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        examples=[0.9, 0.95, 1.0],
        title="Top-p (nucleus sampling)",
    )
    """An alternative to sampling with temperature, called nucleus sampling."""

    timeout: float | None = Field(
        default=None,
        ge=0.0,
        examples=[30.0, 60.0, 120.0],
        title="Request timeout",
    )
    """Override the client-level default timeout for a request, in seconds."""

    parallel_tool_calls: bool | None = Field(
        default=None,
        title="Allow parallel tool calls",
    )
    """Whether to allow parallel tool calls."""

    seed: int | None = Field(default=None, examples=[42, 123, 999], title="Random seed")
    """The random seed to use for the model."""

    presence_penalty: float | None = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        examples=[0.0, 0.5, 1.0],
        title="Presence penalty",
    )
    """Penalize new tokens based on whether they have appeared in the text so far."""

    frequency_penalty: float | None = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        examples=[0.0, 0.5, 1.0],
        title="Frequency penalty",
    )
    """Penalize new tokens based on their existing frequency in the text so far."""

    logit_bias: dict[str, int] | None = Field(
        default=None,
        title="Logit bias",
        examples=[{"5678": -100}, {"1234": 100}],
    )
    """Modify the likelihood of specified tokens appearing in the completion."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to TypedDict format for pydantic-ai."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


AnyModelConfig = Annotated[
    FallbackModelConfig
    | FunctionModelConfig
    | ImportModelConfig
    | InputModelConfig
    | StringModelConfig
    | TestModelConfig
    | OpenAIModelConfig
    | AnthropicModelConfig
    | GeminiModelConfig,
    Field(discriminator="type"),
]
