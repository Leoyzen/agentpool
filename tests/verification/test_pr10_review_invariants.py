"""Regression tests for PR #10 / gemini-code-assist review fixes.

These assert stable invariants so later merges do not silently revert dialect handling
or ContextVar usage.
"""

from types import SimpleNamespace

import pytest

from agentpool_storage.sql_provider.sql_provider import SQLModelProvider


def test_get_insert_stmt_branches_on_engine_dialect() -> None:
    """Must use engine.dialect.name, not merely whether pg/mysql helpers are importable."""

    def stmt_for(dialect: str):
        provider = SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name=dialect)))
        return SQLModelProvider._get_insert_stmt(provider)  # type: ignore[arg-type]

    mysql = stmt_for("mysql")
    assert hasattr(mysql, "on_duplicate_key_update")

    mariadb = stmt_for("mariadb")
    assert hasattr(mariadb, "on_duplicate_key_update")

    sqlite = stmt_for("sqlite")
    assert hasattr(sqlite, "on_conflict_do_nothing")

    pg = stmt_for("postgresql")
    assert hasattr(pg, "on_conflict_do_nothing")


@pytest.mark.asyncio
async def test_claude_tool_complete_event_resolves_tool_name_from_tool_use() -> None:
    """ToolResultBlock should inherit tool name from preceding ToolUseBlock in same message."""
    from clawd_code_sdk.models.content_blocks import ToolResultBlock, ToolUseBlock

    from agentpool.agents.claude_code_agent.converters import claude_message_to_events
    from agentpool.agents.events import ToolCallCompleteEvent, ToolCallStartEvent

    msg = SimpleNamespace(
        content=[
            ToolUseBlock(id="call-1", name="read_file", input={"path": "/tmp/x"}),
            ToolResultBlock(tool_use_id="call-1", content="ok", is_error=False),
        ]
    )

    events = [e async for e in claude_message_to_events(msg, agent_name="agent")]

    assert len(events) == 2
    assert isinstance(events[0], ToolCallStartEvent)
    assert isinstance(events[1], ToolCallCompleteEvent)
    complete = events[1]
    assert complete.tool_name == "read_file"


@pytest.mark.asyncio
async def test_claude_tool_complete_event_resolves_tool_name_from_external_map() -> None:
    """ToolResultBlock alone should use tool_names_by_id from the conversation."""
    from clawd_code_sdk.models.content_blocks import ToolResultBlock

    from agentpool.agents.claude_code_agent.converters import claude_message_to_events
    from agentpool.agents.events import ToolCallCompleteEvent

    msg = SimpleNamespace(
        content=[ToolResultBlock(tool_use_id="call-1", content="ok", is_error=False)]
    )

    events = [
        e
        async for e in claude_message_to_events(
            msg,
            agent_name="agent",
            tool_names_by_id={"call-1": "read_file"},
        )
    ]

    assert len(events) == 1
    assert isinstance(events[0], ToolCallCompleteEvent)
    assert events[0].tool_name == "read_file"
