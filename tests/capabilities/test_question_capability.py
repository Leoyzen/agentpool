"""Unit tests for QuestionCapability."""

from pydantic_ai.capabilities import CapabilityOrdering, NativeTool, ProcessHistory
from pydantic_ai.toolsets import FunctionToolset

from agentpool.capabilities.question import QuestionCapability


def test_get_toolset_returns_function_toolset_without_schemas():
    """get_toolset() should return a FunctionToolset with the question tool."""
    capability = QuestionCapability()
    toolset = capability.get_toolset()

    assert toolset is not None
    assert isinstance(toolset, FunctionToolset)
    tool_names = set(toolset.tools.keys())
    assert "question" in tool_names
    assert len(tool_names) == 1


def test_get_toolset_respects_enabled_tools():
    """get_toolset() should only include tools listed in enabled_tools."""
    capability = QuestionCapability(enabled_tools=["question"])
    toolset = capability.get_toolset()

    assert toolset is not None
    assert isinstance(toolset, FunctionToolset)
    tool_names = set(toolset.tools.keys())
    assert "question" in tool_names


def test_get_toolset_returns_none_when_no_tools_enabled():
    """get_toolset() should return None when no tools are enabled."""
    capability = QuestionCapability(enabled_tools=[])
    toolset = capability.get_toolset()

    assert toolset is None


def test_get_ordering_declares_middleware_position():
    """get_ordering() should declare wrapped_by ProcessHistory and NativeTool."""
    capability = QuestionCapability()
    ordering = capability.get_ordering()

    assert ordering is not None
    assert isinstance(ordering, CapabilityOrdering)
    assert ProcessHistory in ordering.wrapped_by
    assert NativeTool in ordering.wrapped_by


def test_enabled_tools_filters_unknown_names():
    """enabled_tools should silently drop names not in available list."""
    capability = QuestionCapability(enabled_tools=["question", "nonexistent"])
    toolset = capability.get_toolset()

    assert toolset is not None
    tool_names = set(toolset.tools.keys())
    assert "question" in tool_names
    assert len(tool_names) == 1


def test_schemas_dict_enables_matching_tools(tmp_path):
    """Providing schemas dict keys should enable matching tools even without enabled_tools."""
    schema_file = tmp_path / "question.yaml"
    schema_file.write_text(
        "name: question\n"
        "description: Ask structured questions.\n"
        "parameters:\n"
        "  type: object\n"
        "  properties:\n"
        "    questions:\n"
        "      type: string\n"
        "      description: XML questionnaire string\n"
        "  required:\n"
        "    - questions\n"
    )
    capability = QuestionCapability(schemas={"question": str(schema_file)})
    toolset = capability.get_toolset()

    assert toolset is not None
    tool_names = set(toolset.tools.keys())
    assert "question" in tool_names
    assert len(tool_names) == 1


def test_question_tool_enabled_by_default():
    """The 'question' tool should be enabled by default."""
    capability = QuestionCapability()
    toolset = capability.get_toolset()

    assert toolset is not None
    tool_names = set(toolset.tools.keys())
    assert "question" in tool_names


def test_question_tool_can_be_enabled_alone():
    """The 'question' tool can be enabled alone via enabled_tools."""
    capability = QuestionCapability(enabled_tools=["question"])
    toolset = capability.get_toolset()

    assert toolset is not None
    tool_names = set(toolset.tools.keys())
    assert tool_names == {"question"}


def test_question_tool_can_be_enabled_via_schemas(tmp_path):
    """The 'question' tool can be enabled via schemas dict."""
    schema_file = tmp_path / "question.yaml"
    schema_file.write_text(
        "name: question\n"
        "description: Ask a clarifying question.\n"
        "parameters:\n"
        "  type: object\n"
        "  properties:\n"
        "    questions:\n"
        "      type: string\n"
        "      description: XML questionnaire string\n"
        "  required:\n"
        "    - questions\n"
    )
    capability = QuestionCapability(schemas={"question": str(schema_file)})
    toolset = capability.get_toolset()

    assert toolset is not None
    tool_names = set(toolset.tools.keys())
    assert "question" in tool_names
    assert len(tool_names) == 1
