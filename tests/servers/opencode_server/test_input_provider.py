"""Test cases for OpenCode Input Provider multi-question elicitation scenarios.

These tests verify the RFC-0015 implementation for multi-question object schemas.
Note: These tests will FAIL initially - implementation is in Tasks 3-5.
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

from mcp import types
import pytest

from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider
from agentpool_server.opencode_server.state import ServerState


@pytest.mark.xfail(reason="Multi-question object schema not yet implemented")
async def test_multi_question_object_schema():
    """Test that object schema with multiple properties creates multiple QuestionInfo objects.

    Schema with 2+ properties should trigger multi-question handler, creating
    one QuestionInfo per property.
    """
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")

    # Object schema with 3 properties
    schema = {
        "type": "object",
        "properties": {
            "database": {
                "type": "string",
                "enum": ["PostgreSQL", "MySQL", "SQLite"],
                "title": "Database",
            },
            "features": {
                "type": "array",
                "items": {"enum": ["Auth", "API", "Admin"]},
                "title": "Features",
            },
            "project_name": {"type": "string", "title": "Project Name"},
        },
    }
    params = types.ElicitRequestFormParams(
        message="Configure your project",
        requestedSchema=schema,
    )

    # Start elicitation in background
    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)

    # Verify single pending question with multiple QuestionInfo objects
    assert len(state.pending_questions) == 1
    question_id = next(iter(state.pending_questions.keys()))
    pending = state.pending_questions[question_id]

    # Should have 3 questions (one per property)
    assert len(pending.questions) == 3

    # Verify first question (enum/single-select)
    q1 = pending.questions[0]
    assert q1.header == "Database"
    assert q1.multiple is None  # Single-select
    assert len(q1.options) == 3
    assert q1.options[0].label == "PostgreSQL"

    # Verify second question (multi-select array)
    q2 = pending.questions[1]
    assert q2.header == "Features"
    assert q2.multiple is True  # Multi-select
    assert len(q2.options) == 3

    # Verify third question (text input)
    q3 = pending.questions[2]
    assert q3.header == "Project Name"
    assert q3.options == []  # Empty options for text input
    assert q3.multiple is None

    # Clean up
    future = state.pending_questions[question_id].future
    future.cancel()
    await task


@pytest.mark.xfail(reason="Empty object schema handling not yet implemented")
async def test_empty_object_schema_declined():
    """Test that empty object properties returns decline action."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")

    # Object schema with no properties
    schema = {"type": "object", "properties": {}}
    params = types.ElicitRequestFormParams(
        message="No questions to ask",
        requestedSchema=schema,
    )

    result = await provider.get_elicitation(params)

    # Should return decline action for empty object
    assert isinstance(result, types.ElicitResult)
    assert result.action == "decline"
    assert len(state.pending_questions) == 0


@pytest.mark.xfail(reason="Answer key preservation not yet implemented")
async def test_answer_mapping_preserves_keys():
    """Test that answer dict preserves original property keys (not q0, q1).

    The result content must map back to original schema property keys,
    not use generated question IDs like q0, q1.
    """
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")

    schema = {
        "type": "object",
        "properties": {
            "db_engine": {"type": "string", "enum": ["postgres", "mysql"]},
            "enable_cache": {"type": "boolean"},
        },
    }
    params = types.ElicitRequestFormParams(
        message="Configure database",
        requestedSchema=schema,
    )

    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)

    question_id = next(iter(state.pending_questions.keys()))

    # Simulate user answers: postgres for db_engine, true for enable_cache
    provider.resolve_question(question_id, [["postgres"], ["true"]])

    result = await task

    # Verify result preserves original property keys
    assert isinstance(result, types.ElicitResult)
    assert result.action == "accept"
    content = result.content or {}
    assert "db_engine" in content
    assert "enable_cache" in content
    assert content["db_engine"] == "postgres"
    assert content["enable_cache"] == "true"

    # Verify NO q0, q1 style keys
    assert "q0" not in content
    assert "q1" not in content


@pytest.mark.xfail(reason="Max questions limit not yet implemented")
async def test_max_questions_limit():
    """Test that max limit of 10 questions is enforced with warning."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")

    # Create schema with 15 properties (exceeds limit of 10)
    properties = {f"field_{i}": {"type": "string"} for i in range(15)}
    schema = {"type": "object", "properties": properties}
    params = types.ElicitRequestFormParams(
        message="Too many questions",
        requestedSchema=schema,
    )

    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)

    # Should have at most 10 questions (the limit)
    assert len(state.pending_questions) == 1
    question_id = next(iter(state.pending_questions.keys()))
    pending = state.pending_questions[question_id]

    assert len(pending.questions) == 10

    # Clean up
    future = state.pending_questions[question_id].future
    future.cancel()
    await task


@pytest.mark.xfail(reason="Object schema handling not yet implemented (Tasks 3-4)")
async def test_single_property_object():
    """Test that single-property object uses existing flow (not multi-question).

    Single-property objects should behave like the current single-question implementation.
    Currently returns decline - will be fixed in Tasks 3-4.
    """
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")

    # Single-property object schema
    schema = {
        "type": "object",
        "properties": {
            "format": {"type": "string", "enum": ["json", "yaml", "toml"]},
        },
    }
    params = types.ElicitRequestFormParams(
        message="Select format",
        requestedSchema=schema,
    )

    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)

    # Should have exactly one pending question with one QuestionInfo
    assert len(state.pending_questions) == 1
    question_id = next(iter(state.pending_questions.keys()))
    pending = state.pending_questions[question_id]

    # Single property should result in single question
    assert len(pending.questions) == 1

    # Answer and verify
    provider.resolve_question(question_id, [["json"]])
    result = await task

    assert isinstance(result, types.ElicitResult)
    assert result.action == "accept"


@pytest.mark.parametrize(
    "property_schema,expected_multiple,expected_option_count",
    [
        # Enum property -> single-select
        pytest.param(
            {"type": "string", "enum": ["A", "B", "C"]},
            None,
            3,
            id="enum-single-select",
        ),
        # Array with enum items -> multi-select
        pytest.param(
            {"type": "array", "items": {"enum": ["X", "Y", "Z"]}},
            True,
            3,
            id="array-multi-select",
        ),
        # Plain string -> text input (no options)
        pytest.param(
            {"type": "string", "title": "Name"},
            None,
            0,
            id="string-text-input",
        ),
        # oneOf with const/title -> single-select with descriptions
        pytest.param(
            {
                "oneOf": [
                    {"const": "opt1", "title": "Option 1 Description"},
                    {"const": "opt2", "title": "Option 2 Description"},
                ],
            },
            None,
            2,
            id="oneof-with-descriptions",
        ),
    ],
)
@pytest.mark.xfail(reason="Multi-question property type conversion not yet implemented")
async def test_property_to_question_types(
    property_schema: dict,
    expected_multiple: bool | None,
    expected_option_count: int,
):
    """Test conversion of different property types to QuestionInfo structures.

    Verifies that various JSON schema property types are correctly converted:
    - enum -> single-select with options
    - array+enum -> multi-select with options
    - string -> text input (empty options)
    - oneOf -> single-select with descriptions
    """
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="test_session")

    # Wrap property in object schema
    schema = {
        "type": "object",
        "properties": {"test_field": property_schema},
    }
    params = types.ElicitRequestFormParams(
        message="Test question types",
        requestedSchema=schema,
    )

    task = asyncio.create_task(provider.get_elicitation(params))
    await asyncio.sleep(0.1)

    question_id = next(iter(state.pending_questions.keys()))
    pending = state.pending_questions[question_id]

    # Verify single question was created
    assert len(pending.questions) == 1
    question_info = pending.questions[0]

    # Verify multiple flag
    assert question_info.multiple == expected_multiple

    # Verify option count
    assert len(question_info.options) == expected_option_count

    # For oneOf, verify descriptions are populated
    if "oneOf" in property_schema:
        assert question_info.options[0].description == "Option 1 Description"
        assert question_info.options[1].description == "Option 2 Description"

    # Clean up
    future = state.pending_questions[question_id].future
    future.cancel()
    await task


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
