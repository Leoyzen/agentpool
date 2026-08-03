"""QuestionCapability — user interaction tools with YAML schema overrides.

Provides ``question_for_user`` and ``ask_followup_question`` tools backed by
``agentpool_toolsets.builtin.question_tools.QuestionTools``.  Accepts optional
YAML schema files to override the LLM-facing parameter descriptions, mirroring
the ``BackgroundTaskCapability`` pattern.

Declared via the ``question`` entry point in ``pyproject.toml`` so consumers
can reference it in YAML config as ``type: question``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai import RunContext, Tool
from pydantic_ai.capabilities import (
    AbstractCapability,
    CapabilityOrdering,
    NativeTool,
    ProcessHistory,
)
from pydantic_ai.toolsets import AgentToolset, FunctionToolset

from agentpool.agents.context import AgentContext
from agentpool.utils.tool_schema import apply_params_schema, load_tool_schema
from agentpool_config.context import get_config_dir


if TYPE_CHECKING:
    from schemez.functionschema import OpenAIFunctionDefinition


class QuestionCapability(AbstractCapability[AgentContext]):
    """Capability providing user interaction question tools.

    Wraps :class:`~agentpool_toolsets.builtin.question_tools.QuestionTools`
    and applies optional YAML schema overrides for richer LLM-facing
    parameter descriptions.

    Provides one or both of:
    - ``question_for_user``: Rich multi-question questionnaire tool
    - ``ask_followup_question``: Simpler single-question tool with suggestions

    Tool selection is controlled by the ``schemas`` dict keys and
    ``enabled_tools`` list, mirroring ``BackgroundTaskCapability``.
    """

    def __init__(
        self,
        schemas: dict[str, str] | None = None,
        enabled_tools: list[str] | None = None,
    ) -> None:
        """Initialize the question capability.

        Args:
            schemas: Optional dictionary mapping tool names to schema file
                paths.  Expected keys: ``"question_for_user"``,
                ``"ask_followup_question"``.  Paths are resolved relative
                to the config directory using ``CONFIG_DIR``.
            enabled_tools: Optional list of tools to enable.  If ``None``
                or empty, all tools whose schemas are loaded are enabled.
                Expected values: ``"question_for_user"``,
                ``"ask_followup_question"``.
        """
        self._schemas = schemas or {}

        # Schema loading
        self._question_for_user_schema: OpenAIFunctionDefinition | None = None
        self._ask_followup_question_schema: OpenAIFunctionDefinition | None = None

        if schemas:
            if (qfu_path := schemas.get("question_for_user")) is not None:
                self._question_for_user_schema = self._resolve_and_load_schema(qfu_path)
            if (afq_path := schemas.get("ask_followup_question")) is not None:
                self._ask_followup_question_schema = self._resolve_and_load_schema(afq_path)

        # Determine enabled tools
        # No schemas → default-enable both tools (matches old QuestionTools entry point).
        available: list[str] = []
        if not self._schemas:
            available = ["question_for_user", "ask_followup_question"]
        else:
            if self._question_for_user_schema is not None or "question_for_user" in self._schemas:
                available.append("question_for_user")
            if (
                self._ask_followup_question_schema is not None
                or "ask_followup_question" in self._schemas
            ):
                available.append("ask_followup_question")

        if enabled_tools is not None:
            self._enabled_tools = [t for t in enabled_tools if t in available]
        else:
            self._enabled_tools = available

    @staticmethod
    def _resolve_and_load_schema(schema_path_str: str) -> OpenAIFunctionDefinition:
        """Resolve a schema path relative to ``CONFIG_DIR`` and load it.

        Args:
            schema_path_str: The schema file path (absolute or relative
                to ``CONFIG_DIR``).

        Returns:
            The loaded schema as an ``OpenAIFunctionDefinition``.

        Raises:
            FileNotFoundError: If the schema file doesn't exist.
            ValueError: If the schema file can't be parsed.
        """
        schema_path = Path(schema_path_str)
        if not schema_path.is_absolute():
            config_dir = get_config_dir()
            if config_dir is not None:
                schema_path = Path(str(config_dir)) / schema_path
        result = load_tool_schema(str(schema_path))
        if result is None:
            msg = f"Tool schema at {schema_path} loaded as None"
            raise ValueError(msg)
        return result

    def get_toolset(self) -> AgentToolset[AgentContext] | None:
        """Return ``FunctionToolset`` with enabled question tools.

        Tool callables are sourced from the agentpool ``QuestionTools``
        instance, ensuring a single canonical implementation.  YAML schema
        overrides are applied on top for richer LLM-facing descriptions.
        """
        tools: list[Tool[AgentContext]] = []

        if "question_for_user" in self._enabled_tools:
            name = (
                self._question_for_user_schema.get("name")
                if self._question_for_user_schema
                else None
            ) or "question_for_user"
            description = (
                self._question_for_user_schema.get("description")
                if self._question_for_user_schema
                else None
            ) or "Ask structured questions to the user."
            tool = Tool(
                self._question_for_user,
                name=name,
                description=description,
                metadata={"category": "other"},
            )
            tools.append(apply_params_schema(tool, self._question_for_user_schema))

        if "ask_followup_question" in self._enabled_tools:
            name = (
                self._ask_followup_question_schema.get("name")
                if self._ask_followup_question_schema
                else None
            ) or "ask_followup_question"
            description = (
                self._ask_followup_question_schema.get("description")
                if self._ask_followup_question_schema
                else None
            ) or "Ask a follow-up question with suggestions."
            tool = Tool(
                self._ask_followup_question,
                name=name,
                description=description,
                metadata={"category": "other"},
            )
            tools.append(apply_params_schema(tool, self._ask_followup_question_schema))

        if not tools:
            return None
        return FunctionToolset(tools)

    def get_ordering(self) -> CapabilityOrdering | None:
        """Declare middleware chain position."""
        return CapabilityOrdering(wrapped_by=[ProcessHistory, NativeTool])

    # ---- Tool wrappers ----
    # These wrap the canonical tool functions from agentpool's QuestionTools,
    # adapting RunContext[AgentContext] → AgentContext for direct invocation.

    async def _question_for_user(self, ctx: RunContext[AgentContext], questionnaire: str):
        """Wrap ``question_for_user`` to accept ``RunContext``."""
        from agentpool_toolsets.builtin.question_tools import QuestionTools

        question_tools = QuestionTools(name="question_tools")
        return await question_tools.question_for_user(ctx.deps, questionnaire)

    async def _ask_followup_question(
        self,
        ctx: RunContext[AgentContext],
        question: str,
        follow_up: str,
    ):
        """Wrap ``ask_followup_question`` to accept ``RunContext``."""
        from agentpool_toolsets.builtin.question_tools import QuestionTools

        question_tools = QuestionTools(name="question_tools")
        return await question_tools.ask_followup_question(ctx.deps, question, follow_up)


__all__ = ["QuestionCapability"]
