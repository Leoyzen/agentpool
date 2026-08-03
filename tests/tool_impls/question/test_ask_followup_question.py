"""Unit tests for the ask_followup_question tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from mcp.types import ElicitRequestFormParams, ElicitResult, ErrorData
import pytest

from agentpool.tools.base import ToolResult
from agentpool_toolsets.builtin.question_tools import QuestionTools


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_followup_question_standard() -> None:
    mock_ctx = MagicMock()
    mock_ctx.handle_elicitation = AsyncMock()

    # Mock the return value of handle_elicitation
    mock_result = MagicMock(spec=ElicitResult)
    mock_result.action = "accept"
    mock_result.content = {"value": "Yes"}
    mock_ctx.handle_elicitation.return_value = mock_result

    follow_up = "Would you like to continue? <suggest>Yes</suggest> <suggest>No</suggest>"
    result = await QuestionTools().ask_followup_question(
        mock_ctx, "Would you like to continue?", follow_up
    )

    # Check that handle_elicitation was called with correct parameters
    mock_ctx.handle_elicitation.assert_called_once()
    params = mock_ctx.handle_elicitation.call_args[0][0]
    assert isinstance(params, ElicitRequestFormParams)
    assert params.message == "Would you like to continue?"
    assert params.requestedSchema == {
        "type": "string",
        "enum": ["Yes", "No"],
    }

    # Check ToolResult
    assert isinstance(result, ToolResult)
    assert result.content == "Yes"
    assert result.metadata == {"answers": [["Yes"]]}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_followup_question_multiline() -> None:
    mock_ctx = MagicMock()
    mock_ctx.handle_elicitation = AsyncMock()

    mock_result = MagicMock(spec=ElicitResult)
    mock_result.action = "accept"
    mock_result.content = {"value": "Option 1"}
    mock_ctx.handle_elicitation.return_value = mock_result

    follow_up = """
    Select an option:
    <suggest>
    Option 1
    </suggest>
    <suggest>
    Option 2
    </suggest>
    """
    result = await QuestionTools().ask_followup_question(mock_ctx, "Select an option:", follow_up)

    params = mock_ctx.handle_elicitation.call_args[0][0]
    assert params.message == "Select an option:"
    assert params.requestedSchema == {
        "type": "string",
        "enum": ["Option 1", "Option 2"],
    }
    assert result.content == "Option 1"
    assert result.metadata == {"answers": [["Option 1"]]}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_followup_question_with_attributes() -> None:
    mock_ctx = MagicMock()
    mock_ctx.handle_elicitation = AsyncMock()

    mock_result = MagicMock(spec=ElicitResult)
    mock_result.action = "accept"
    mock_result.content = {"value": "Action"}
    mock_ctx.handle_elicitation.return_value = mock_result

    follow_up = '<suggest type="input" next_action="foo">Action</suggest>'
    result = await QuestionTools().ask_followup_question(mock_ctx, follow_up, follow_up)

    params = mock_ctx.handle_elicitation.call_args[0][0]
    # In this case, prompt becomes follow_up because it's only suggestions
    assert params.requestedSchema == {
        "type": "string",
        "default": "Action",
    }
    assert result.content == "Action"
    # agentpool's _format_followup_response returns answers metadata only; the
    # suggestion attributes are still honored in requestedSchema (default above).
    assert result.metadata == {
        "answers": [["Action"]],
    }


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_followup_question_no_suggestions() -> None:
    mock_ctx = MagicMock()
    mock_ctx.handle_elicitation = AsyncMock()

    mock_result = MagicMock(spec=ElicitResult)
    mock_result.action = "accept"
    mock_result.content = {"value": "User Answer"}
    mock_ctx.handle_elicitation.return_value = mock_result

    follow_up = "Just a question without suggestions."
    result = await QuestionTools().ask_followup_question(
        mock_ctx, "Just a question without suggestions.", follow_up
    )

    params = mock_ctx.handle_elicitation.call_args[0][0]
    assert params.message == "Just a question without suggestions."
    assert params.requestedSchema == {"type": "string"}
    assert result.content == "User Answer"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_followup_question_html_entities() -> None:
    mock_ctx = MagicMock()
    mock_ctx.handle_elicitation = AsyncMock()

    mock_result = MagicMock(spec=ElicitResult)
    mock_result.action = "accept"
    mock_result.content = {"value": "Fish & Chips"}
    mock_ctx.handle_elicitation.return_value = mock_result

    follow_up = "What do you want? <suggest>Fish &amp; Chips</suggest>"
    result = await QuestionTools().ask_followup_question(mock_ctx, "What do you want?", follow_up)

    params = mock_ctx.handle_elicitation.call_args[0][0]
    assert params.requestedSchema == {
        "type": "string",
        "enum": ["Fish & Chips"],
    }
    assert result.content == "Fish & Chips"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_followup_question_cancel() -> None:
    mock_ctx = MagicMock()
    mock_ctx.handle_elicitation = AsyncMock()

    mock_result = MagicMock(spec=ElicitResult)
    mock_result.action = "cancel"
    mock_ctx.handle_elicitation.return_value = mock_result

    follow_up = "Question? <suggest>Yes</suggest>"
    result = await QuestionTools().ask_followup_question(mock_ctx, "Question?", follow_up)

    assert result.content == "User cancelled the request"
    assert result.metadata == {"answers": []}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_followup_question_decline() -> None:
    mock_ctx = MagicMock()
    mock_ctx.handle_elicitation = AsyncMock()

    mock_result = MagicMock(spec=ElicitResult)
    mock_result.action = "decline"
    mock_ctx.handle_elicitation.return_value = mock_result

    follow_up = "Question? <suggest>Yes</suggest>"
    result = await QuestionTools().ask_followup_question(mock_ctx, "Question?", follow_up)

    assert result.content == "User declined to answer"
    assert result.metadata == {"answers": []}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_followup_question_error_data() -> None:
    mock_ctx = MagicMock()
    mock_ctx.handle_elicitation = AsyncMock()

    mock_error = MagicMock(spec=ErrorData)
    mock_error.message = "Something went wrong"
    mock_error.code = 500
    mock_ctx.handle_elicitation.return_value = mock_error

    follow_up = "Question? <suggest>Yes</suggest>"
    result = await QuestionTools().ask_followup_question(mock_ctx, "Question?", follow_up)

    assert result.content == "Error: Something went wrong"
    assert result.metadata == {"answers": []}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_followup_question_mixed_suggestions() -> None:
    mock_ctx = MagicMock()
    mock_ctx.handle_elicitation = AsyncMock()

    mock_result = MagicMock(spec=ElicitResult)
    mock_result.action = "accept"
    mock_result.content = {"value": "Maybe"}
    mock_ctx.handle_elicitation.return_value = mock_result

    follow_up = """
    Choose:
    <suggest>Yes</suggest>
    <suggest>No</suggest>
    <suggest type="input">Maybe</suggest>
    """
    await QuestionTools().ask_followup_question(mock_ctx, "Choose:", follow_up)

    params = mock_ctx.handle_elicitation.call_args[0][0]
    # input_suggestion "Maybe" should be added to enum
    assert params.requestedSchema == {
        "type": "string",
        "enum": ["Yes", "No", "Maybe"],
    }


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_followup_question_empty_prompt() -> None:
    mock_ctx = MagicMock()
    mock_ctx.handle_elicitation = AsyncMock()

    mock_result = MagicMock(spec=ElicitResult)
    mock_result.action = "accept"
    mock_result.content = {"value": "A"}
    mock_ctx.handle_elicitation.return_value = mock_result

    follow_up = "<suggest>A</suggest>"
    await QuestionTools().ask_followup_question(mock_ctx, follow_up, follow_up)

    params = mock_ctx.handle_elicitation.call_args[0][0]
    # If stripping tags leaves empty prompt, use follow_up
    assert params.message == follow_up


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_followup_question_non_dict_content() -> None:
    mock_ctx = MagicMock()
    mock_ctx.handle_elicitation = AsyncMock()

    mock_result = MagicMock(spec=ElicitResult)
    mock_result.action = "accept"
    # content is not a dict with "value"
    mock_result.content = "Direct Answer"
    mock_ctx.handle_elicitation.return_value = mock_result

    follow_up = "Question?"
    result = await QuestionTools().ask_followup_question(mock_ctx, "Question?", follow_up)

    assert result.content == "Direct Answer"
    assert result.metadata == {"answers": [["Direct Answer"]]}
