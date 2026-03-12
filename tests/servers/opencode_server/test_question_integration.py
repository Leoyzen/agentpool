"""Integration tests for OpenCode question system."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

from mcp import types
import pytest

from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider
from agentpool_server.opencode_server.state import ServerState


async def test_question_elicitation_single_select():
    """Test single-select question via elicitation."""
    # This is a basic unit test without full server
    # Create minimal mock agent (pool not needed for this test)
    mock_agent = Mock()
    mock_agent.agent_pool = None
    # Create minimal state
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    # Create provider
    provider = OpenCodeInputProvider(state=state, session_id="test_session")
    # Create elicitation params with enum
    schema = {"type": "string", "enum": ["PostgreSQL", "MySQL", "SQLite"]}
    params = types.ElicitRequestFormParams(message="Which database?", requestedSchema=schema)

    # Start elicitation in background
    async def get_answer():
        return await provider.get_elicitation(params)

    task = asyncio.create_task(get_answer())
    # Wait a bit for question to be created
    await asyncio.sleep(0.1)
    # Verify question was created
    assert len(state.pending_questions) == 1
    question_id = next(iter(state.pending_questions.keys()))
    pending = state.pending_questions[question_id]
    # Verify question structure
    assert pending.session_id == "test_session"
    assert len(pending.questions) == 1
    question_info = pending.questions[0]
    assert question_info.question == "Which database?"
    assert question_info.multiple is None
    assert len(question_info.options) == 3
    # Simulate user reply
    success = provider.resolve_question(question_id, [["PostgreSQL"]])
    assert success
    # Wait for result
    result = await task
    # Verify result
    assert isinstance(result, types.ElicitResult)
    assert result.action == "accept"
    assert result.content == {"value": "PostgreSQL"}
    # Verify cleanup
    assert question_id not in state.pending_questions


async def test_question_elicitation_multi_select():
    """Test multi-select question via elicitation."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")
    # Multi-select schema
    schema = {"type": "array", "items": {"type": "string", "enum": ["Auth", "API", "Admin"]}}
    params = types.ElicitRequestFormParams(message="Which features?", requestedSchema=schema)
    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)
    # Get question
    question_id = next(iter(state.pending_questions.keys()))
    pending = state.pending_questions[question_id]
    question_info = pending.questions[0]
    # Verify multi-select flag
    assert question_info.multiple is True
    # Reply with multiple selections
    provider.resolve_question(question_id, [["Auth", "Admin"]])
    result = await task
    # Multi-select returns list in dict
    assert isinstance(result, types.ElicitResult)
    assert result.action == "accept"
    assert result.content == {"value": ["Auth", "Admin"]}


async def test_question_cancellation():
    """Test question cancellation."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")
    schema = {"type": "string", "enum": ["PostgreSQL", "MySQL"]}
    params = types.ElicitRequestFormParams(message="Which database?", requestedSchema=schema)
    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)
    # Get question and cancel it
    question_id = next(iter(state.pending_questions.keys()))
    future = state.pending_questions[question_id].future
    future.cancel()
    result = await task
    # Should return cancel action
    assert isinstance(result, types.ElicitResult)
    assert result.action == "cancel"


async def test_question_with_descriptions():
    """Test question with option descriptions."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")
    # Schema with custom descriptions
    schema = {
        "type": "string",
        "enum": ["PostgreSQL", "MySQL", "SQLite"],
        "x-option-descriptions": {
            "PostgreSQL": "Best for production",
            "MySQL": "Compatible with many tools",
            "SQLite": "Lightweight, file-based",
        },
    }
    params = types.ElicitRequestFormParams(message="Which database?", requestedSchema=schema)
    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)
    # Verify descriptions were included
    question_id = next(iter(state.pending_questions.keys()))
    question_info = state.pending_questions[question_id].questions[0]
    options = question_info.options
    assert options[0].label == "PostgreSQL"
    assert options[0].description == "Best for production"
    assert options[1].description == "Compatible with many tools"
    # Clean up
    future = state.pending_questions[question_id].future
    future.cancel()
    await task


async def test_multi_question_rfc0010_example():
    """Test multi-question with RFC-0010 schema format (q0, q1, etc.).

    RFC-0010 example schema format:
    {
        "type": "object",
        "properties": {
            "q0": {"type": "string", "enum": ["opt1", "opt2"]},
            "q1": {"type": "array", "items": {"enum": ["val1", "val2"]}}
        }
    }
    """
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")

    # RFC-0010 example schema with q0, q1 format
    schema = {
        "type": "object",
        "properties": {
            "q0": {
                "type": "string",
                "enum": ["opt1", "opt2"],
                "title": "First Choice",
                "description": "Select your first option",
            },
            "q1": {
                "type": "array",
                "items": {"enum": ["val1", "val2"]},
                "title": "Features",
                "description": "Select multiple features",
            },
        },
    }
    params = types.ElicitRequestFormParams(
        message="Configuration questions", requestedSchema=schema
    )

    # Start elicitation in background
    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)

    # Verify question was created with multiple questions
    assert len(state.pending_questions) == 1
    question_id = next(iter(state.pending_questions.keys()))
    pending = state.pending_questions[question_id]

    # Verify 2 questions created
    assert len(pending.questions) == 2

    # First question (q0) - single-select enum
    question1 = pending.questions[0]
    assert question1.question == "Select your first option"
    assert question1.header == "First Choice"[:12]  # Truncated title
    assert question1.multiple is None  # Single-select
    assert len(question1.options) == 2
    assert question1.options[0].label == "opt1"
    assert question1.options[1].label == "opt2"

    # Second question (q1) - multi-select array
    question2 = pending.questions[1]
    assert question2.question == "Select multiple features"
    assert question2.header == "Features"[:12]  # Truncated title
    assert question2.multiple is True  # Multi-select
    assert len(question2.options) == 2
    assert question2.options[0].label == "val1"
    assert question2.options[1].label == "val2"

    # Simulate user answers (answering both questions)
    success = provider.resolve_question(question_id, [["opt1"], ["val1", "val2"]])
    assert success

    # Wait for result
    result = await task

    # Verify result preserves original property keys (q0, q1)
    assert isinstance(result, types.ElicitResult)
    assert result.action == "accept"
    assert result.content == {"q0": "opt1", "q1": ["val1", "val2"]}

    assert question_id not in state.pending_questions


async def test_multi_question_cancellation():
    """Test cancellation during multi-question flow."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")

    # Multi-question schema with 3 questions
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "title": "Name", "description": "Your name"},
            "role": {
                "type": "string",
                "enum": ["admin", "user"],
                "title": "Role",
                "description": "Select role",
            },
            "features": {
                "type": "array",
                "items": {"enum": ["a", "b"]},
                "title": "Features",
                "description": "Select features",
            },
        },
    }
    params = types.ElicitRequestFormParams(message="User details", requestedSchema=schema)

    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)

    # Get question and cancel it
    question_id = next(iter(state.pending_questions.keys()))
    future = state.pending_questions[question_id].future
    future.cancel()

    result = await task

    # Should return cancel action
    assert isinstance(result, types.ElicitResult)
    assert result.action == "cancel"

    # Clean up if still present
    assert question_id not in state.pending_questions


async def test_multi_question_partial_answers():
    """Test multi-question with partial answers (fewer than questions)."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")

    # Schema with 3 questions
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string", "enum": ["x", "y"], "title": "A", "description": "Select A"},
            "b": {"type": "string", "enum": ["m", "n"], "title": "B", "description": "Select B"},
            "c": {"type": "string", "enum": ["p", "q"], "title": "C", "description": "Select C"},
        },
    }
    params = types.ElicitRequestFormParams(message="Selections", requestedSchema=schema)

    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)

    question_id = next(iter(state.pending_questions.keys()))

    # Provide only 2 answers for 3 questions
    success = provider.resolve_question(question_id, [["x"], ["m"]])
    assert success

    result = await task

    assert isinstance(result, types.ElicitResult)
    assert result.action == "accept"
    # Only first 2 properties should have answers
    assert result.content == {"a": "x", "b": "m"}
    assert question_id not in state.pending_questions


async def test_multi_question_empty_object_declines():
    """Test that empty object schema returns decline."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")

    # Empty object schema (no properties)
    schema = {"type": "object", "properties": {}}
    params = types.ElicitRequestFormParams(message="Empty config", requestedSchema=schema)

    result = await provider.get_elicitation(params)

    # Empty object schema doesn't match len(props) >= 1, goes to fallback case
    # which returns decline
    assert isinstance(result, types.ElicitResult)
    assert result.action == "decline"


async def test_multi_question_rfc0010_backward_compat():
    """Test RFC-0010 schema maintains backward compatibility with single questions."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")

    # Single property schema (should still use multi-question handler per Task 4)
    schema = {
        "type": "object",
        "properties": {
            "q0": {
                "type": "string",
                "enum": ["yes", "no"],
                "title": "Confirm",
                "description": "Proceed?",
            },
        },
    }
    params = types.ElicitRequestFormParams(message="Confirm action", requestedSchema=schema)

    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)

    assert len(state.pending_questions) == 1
    question_id = next(iter(state.pending_questions.keys()))
    pending = state.pending_questions[question_id]

    # Single question in multi-question format
    assert len(pending.questions) == 1
    assert pending.questions[0].question == "Proceed?"

    # Resolve
    provider.resolve_question(question_id, [["yes"]])
    result = await task

    assert isinstance(result, types.ElicitResult)
    assert result.action == "accept"
    assert result.content == {"q0": "yes"}


async def test_multi_question_event_structure():
    """Test that SSE QuestionAskedEvent has correct structure for multi-questions."""
    from agentpool_server.opencode_server.models.events import QuestionAskedEvent
    from agentpool_server.opencode_server.models.question import QuestionInfo, QuestionOption

    # Create a QuestionsAskedEvent with multiple questions
    questions = [
        QuestionInfo(
            question="Select your first option",
            header="First Choice",
            options=[
                QuestionOption(label="opt1", description=""),
                QuestionOption(label="opt2", description=""),
            ],
            multiple=None,
        ),
        QuestionInfo(
            question="Select features",
            header="Features",
            options=[
                QuestionOption(label="val1", description=""),
                QuestionOption(label="val2", description=""),
            ],
            multiple=True,
        ),
    ]

    event = QuestionAskedEvent.create(
        request_id="test-req-123",
        session_id="test-session",
        questions=questions,
    )

    # Verify event structure
    assert event.type == "question.asked"
    assert event.properties.id == "test-req-123"
    assert event.properties.session_id == "test-session"

    # Verify questions array
    assert len(event.properties.questions) == 2

    # First question
    q1 = event.properties.questions[0]
    assert q1.question == "Select your first option"
    assert q1.header == "First Choice"
    assert q1.multiple is None
    assert len(q1.options) == 2
    assert q1.options[0].label == "opt1"

    # Second question
    q2 = event.properties.questions[1]
    assert q2.question == "Select features"
    assert q2.header == "Features"
    assert q2.multiple is True
    assert len(q2.options) == 2
    assert q2.options[0].label == "val1"

    # Verify tool is None (not passed)
    assert event.properties.tool is None


async def test_multi_question_max_limit():
    """Test that multi-questions are capped at 10."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")

    # Create schema with 12 properties (exceeds max)
    properties = {
        f"q{i}": {"type": "string", "enum": ["a", "b"], "title": f"Q{i}"} for i in range(12)
    }
    schema = {"type": "object", "properties": properties}
    params = types.ElicitRequestFormParams(message="Many questions", requestedSchema=schema)

    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)

    question_id = next(iter(state.pending_questions.keys()))
    pending = state.pending_questions[question_id]

    # Should be limited to 10 questions
    assert len(pending.questions) == 10

    # Clean up
    pending.future.cancel()
    await task


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
