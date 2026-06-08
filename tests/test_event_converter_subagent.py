"""Tests for ACPEventConverter subagent emission in tool_box and inline modes.

TDD tests for T6/T7/T8: ACPEventConverter subagent emission and state management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import pytest

from acp.schema import AgentMessageChunk, ContentToolCallContent, SubagentRunInfo, ToolCallProgress, ToolCallStart
from agentpool.agents.events import (
    RunErrorEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
    SubAgentEvent,
)
from agentpool_server.acp_server.event_converter import ACPEventConverter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.agents.events import RichAgentStreamEvent


async def _collect_updates(
    converter: ACPEventConverter,
    event: RichAgentStreamEvent,
) -> list:
    """Collect all updates from converter.convert()."""
    return [u async for u in converter.convert(event)]


class TestToolBoxModeSubagent:
    """Test tool_box mode SpawnSessionStart emission (T6)."""

    @pytest.fixture
    def tool_box_converter(self, monkeypatch: pytest.MonkeyPatch) -> ACPEventConverter:
        """Converter configured for tool_box mode."""
        monkeypatch.setenv("ACP_SUBAGENT_DISPLAY_MODE", "tool_box")
        return ACPEventConverter()

    @pytest.mark.anyio
    async def test_spawn_session_start_emits_tool_call_start(
        self,
        tool_box_converter: ACPEventConverter,
    ) -> None:
        """tool_box mode: SpawnSessionStart emits ToolCallStart with kind='subagent'."""
        event = SpawnSessionStart(
            child_session_id="child_001",
            parent_session_id="parent_001",
            tool_call_id="tc_001",
            spawn_mechanism="spawn",
            source_name="coder_agent",
            source_type="agent",
            description="Spawning coder_agent for code review",
            run_mode="foreground",
        )

        updates = await _collect_updates(tool_box_converter, event)

        assert len(updates) == 1
        assert isinstance(updates[0], ToolCallStart)
        assert updates[0].tool_call_id == "tc_001"
        assert updates[0].kind == "subagent"
        assert updates[0].status == "in_progress"
        assert updates[0].title == "⚡ coder_agent"
        assert updates[0].subagent is not None
        assert updates[0].subagent.child_session_id == "child_001"
        assert updates[0].subagent.subagent_id == "coder_agent"
        assert updates[0].subagent.name == "coder_agent"
        assert updates[0].subagent.run_mode == "foreground"

    @pytest.mark.anyio
    async def test_spawn_session_start_fallback_tool_call_id(
        self,
        tool_box_converter: ACPEventConverter,
    ) -> None:
        """tool_box mode: when tool_call_id is None, fallback to 'subagent:{child_id}'."""
        event = SpawnSessionStart(
            child_session_id="child_004",
            parent_session_id="parent_001",
            tool_call_id=None,
            spawn_mechanism="spawn",
            source_name="analyzer_agent",
            source_type="agent",
            description="Spawning analyzer_agent",
            run_mode="foreground",
        )

        updates = await _collect_updates(tool_box_converter, event)

        assert len(updates) == 1
        assert isinstance(updates[0], ToolCallStart)
        assert updates[0].tool_call_id == "subagent:child_004"
        assert updates[0].subagent is not None
        assert updates[0].subagent.child_session_id == "child_004"

    @pytest.mark.anyio
    async def test_subagent_tool_map_tracks_mapping(
        self,
        tool_box_converter: ACPEventConverter,
    ) -> None:
        """_subagent_tool_map tracks child_session_id → tool_call_id."""
        event = SpawnSessionStart(
            child_session_id="child_005",
            parent_session_id="parent_001",
            tool_call_id="tc_005",
            spawn_mechanism="spawn",
            source_name="test_agent",
            source_type="agent",
            description="Test",
            run_mode="foreground",
        )

        await _collect_updates(tool_box_converter, event)

        assert "child_005" in tool_box_converter._subagent_tool_map
        assert tool_box_converter._subagent_tool_map["child_005"] == "tc_005"

    @pytest.mark.anyio
    async def test_subagent_tool_map_fallback_tracks_mapping(
        self,
        tool_box_converter: ACPEventConverter,
    ) -> None:
        """_subagent_tool_map tracks fallback tool_call_id when tc_id is None."""
        event = SpawnSessionStart(
            child_session_id="child_006",
            parent_session_id="parent_001",
            tool_call_id=None,
            spawn_mechanism="task",
            source_name="test_agent",
            source_type="agent",
            description="Test",
            run_mode="background",
        )

        await _collect_updates(tool_box_converter, event)

        assert "child_006" in tool_box_converter._subagent_tool_map
        assert tool_box_converter._subagent_tool_map["child_006"] == "subagent:child_006"

    @pytest.mark.anyio
    async def test_foreground_children_tracked(
        self,
        tool_box_converter: ACPEventConverter,
    ) -> None:
        """_foreground_children contains child_id when run_mode='foreground'."""
        event = SpawnSessionStart(
            child_session_id="child_fg",
            parent_session_id="parent_001",
            tool_call_id="tc_fg",
            spawn_mechanism="spawn",
            source_name="fg_agent",
            source_type="agent",
            description="Test",
            run_mode="foreground",
        )

        await _collect_updates(tool_box_converter, event)

        assert "child_fg" in tool_box_converter._foreground_children

    @pytest.mark.anyio
    async def test_background_children_not_tracked(
        self,
        tool_box_converter: ACPEventConverter,
    ) -> None:
        """_foreground_children does not contain child_id when run_mode='background'."""
        event = SpawnSessionStart(
            child_session_id="child_bg",
            parent_session_id="parent_001",
            tool_call_id="tc_bg",
            spawn_mechanism="task",
            source_name="bg_agent",
            source_type="agent",
            description="Test",
            run_mode="background",
        )

        await _collect_updates(tool_box_converter, event)

        assert "child_bg" not in tool_box_converter._foreground_children

    @pytest.mark.anyio
    async def test_spawn_session_start_task_mechanism_icon(
        self,
        tool_box_converter: ACPEventConverter,
    ) -> None:
        """task mechanism uses 🚀 icon, spawn uses ⚡ icon."""
        event_task = SpawnSessionStart(
            child_session_id="child_task",
            parent_session_id="parent_001",
            tool_call_id="tc_task",
            spawn_mechanism="task",
            source_name="task_agent",
            source_type="agent",
            description="Task",
            run_mode="foreground",
        )
        event_spawn = SpawnSessionStart(
            child_session_id="child_spawn",
            parent_session_id="parent_001",
            tool_call_id="tc_spawn",
            spawn_mechanism="spawn",
            source_name="spawn_agent",
            source_type="agent",
            description="Spawn",
            run_mode="foreground",
        )

        updates_task = await _collect_updates(tool_box_converter, event_task)
        updates_spawn = await _collect_updates(tool_box_converter, event_spawn)

        assert "🚀" in updates_task[0].title
        assert "⚡" in updates_spawn[0].title

    @pytest.mark.anyio
    async def test_reset_clears_subagent_state(
        self,
        tool_box_converter: ACPEventConverter,
    ) -> None:
        """reset() clears _subagent_tool_map and _foreground_children."""
        event = SpawnSessionStart(
            child_session_id="child_007",
            parent_session_id="parent_001",
            tool_call_id="tc_007",
            spawn_mechanism="spawn",
            source_name="test_agent",
            source_type="agent",
            description="Test",
            run_mode="foreground",
        )

        await _collect_updates(tool_box_converter, event)
        assert len(tool_box_converter._subagent_tool_map) > 0
        assert len(tool_box_converter._foreground_children) > 0

        tool_box_converter.reset()

        assert len(tool_box_converter._subagent_tool_map) == 0
        assert len(tool_box_converter._foreground_children) == 0


class TestInlineModeSubagent:
    """Test inline mode SpawnSessionStart emission (T6/T7)."""

    @pytest.fixture
    def inline_converter(self, monkeypatch: pytest.MonkeyPatch) -> ACPEventConverter:
        """Converter configured for inline mode."""
        monkeypatch.setenv("ACP_SUBAGENT_DISPLAY_MODE", "inline")
        return ACPEventConverter()

    @pytest.mark.anyio
    async def test_spawn_session_start_emits_tool_call_start(
        self,
        inline_converter: ACPEventConverter,
    ) -> None:
        """inline mode: SpawnSessionStart emits ToolCallStart with kind='subagent'."""
        event = SpawnSessionStart(
            child_session_id="child_002",
            parent_session_id="parent_001",
            tool_call_id="tc_002",
            spawn_mechanism="task",
            source_name="reviewer_agent",
            source_type="agent",
            description="Task reviewer_agent for review",
            run_mode="background",
        )

        updates = await _collect_updates(inline_converter, event)

        assert len(updates) == 1
        assert isinstance(updates[0], ToolCallStart)
        assert updates[0].tool_call_id == "tc_002"
        assert updates[0].kind == "subagent"
        assert updates[0].status == "in_progress"
        assert updates[0].title == "🚀 reviewer_agent"
        assert updates[0].subagent is not None
        assert updates[0].subagent.child_session_id == "child_002"
        assert updates[0].subagent.subagent_id == "reviewer_agent"
        assert updates[0].subagent.run_mode == "background"

    @pytest.mark.anyio
    async def test_spawn_session_start_tool_call_id_unique_from_internals(
        self,
        inline_converter: ACPEventConverter,
    ) -> None:
        """Canonical ToolCallStart ID is unique from internal SubAgentEvent tool calls."""
        from pydantic_ai import FunctionToolCallEvent, ToolCallPart

        from agentpool.agents.events import SubAgentEvent

        spawn_event = SpawnSessionStart(
            child_session_id="child-009",
            parent_session_id="parent_001",
            tool_call_id=None,
            spawn_mechanism="spawn",
            source_name="coder",
            source_type="agent",
            description="Write code",
        )
        spawn_updates = await _collect_updates(inline_converter, spawn_event)
        canonical_id = spawn_updates[0].tool_call_id

        inner_event = FunctionToolCallEvent(
            part=ToolCallPart(
                tool_call_id="tc-1",
                tool_name="bash",
                args={"command": "ls"},
            ),
        )
        subagent_event = SubAgentEvent(
            source_name="coder",
            source_type="agent",
            event=inner_event,
            depth=1,
        )
        inner_updates = await _collect_updates(inline_converter, subagent_event)
        internal_ids = {
            u.tool_call_id for u in inner_updates if isinstance(u, ToolCallStart)
        }

        assert canonical_id not in internal_ids
        assert canonical_id == "subagent:child-009"

    @pytest.mark.anyio
    async def test_subagent_event_internal_tool_call_retains_original_kind(
        self,
        inline_converter: ACPEventConverter,
    ) -> None:
        """Internal SubAgentEvent tool calls keep their original inferred kind."""
        from pydantic_ai import FunctionToolCallEvent, ToolCallPart

        from agentpool.agents.events import SubAgentEvent

        inner_event = FunctionToolCallEvent(
            part=ToolCallPart(
                tool_call_id="tc-1",
                tool_name="bash",
                args={"command": "ls"},
            ),
        )
        event = SubAgentEvent(
            source_name="coder",
            source_type="agent",
            event=inner_event,
            depth=1,
        )

        updates = await _collect_updates(inline_converter, event)

        tool_starts = [u for u in updates if isinstance(u, ToolCallStart)]
        assert len(tool_starts) == 1
        assert tool_starts[0].kind == "execute"

    @pytest.mark.anyio
    async def test_subagent_event_text_output_retains_other_kind(
        self,
        inline_converter: ACPEventConverter,
    ) -> None:
        """Internal text output retains kind='other', not 'subagent'."""
        from pydantic_ai import PartStartEvent, TextPart

        from agentpool.agents.events import SubAgentEvent

        inner_event = PartStartEvent(part=TextPart(content="Hello"), index=0)
        event = SubAgentEvent(
            source_name="coder",
            source_type="agent",
            event=inner_event,
            depth=1,
        )

        updates = await _collect_updates(inline_converter, event)

        tool_starts = [u for u in updates if isinstance(u, ToolCallStart)]
        assert len(tool_starts) == 1
        assert tool_starts[0].kind == "other"
        assert "coder" in tool_starts[0].title


class TestLegacyModeSubagent:
    """Test legacy mode subagent emission remains unchanged."""

    @pytest.fixture
    def legacy_converter(self, monkeypatch: pytest.MonkeyPatch) -> ACPEventConverter:
        """Converter configured for legacy mode."""
        monkeypatch.setenv("ACP_SUBAGENT_DISPLAY_MODE", "legacy")
        return ACPEventConverter()

    @pytest.mark.anyio
    async def test_spawn_session_start_yields_agent_message_chunk(
        self,
        legacy_converter: ACPEventConverter,
    ) -> None:
        """legacy mode: SpawnSessionStart still emits AgentMessageChunk (unchanged)."""
        event = SpawnSessionStart(
            child_session_id="child_003",
            parent_session_id="parent_001",
            tool_call_id="tc_003",
            spawn_mechanism="spawn",
            source_name="coder_agent",
            source_type="agent",
            description="Spawning coder_agent",
        )

        updates = await _collect_updates(legacy_converter, event)

        assert len(updates) == 1
        assert isinstance(updates[0], AgentMessageChunk)
        assert "coder_agent" in updates[0].content.text
        assert "⚡" in updates[0].content.text


class TestSubagentStateManagement:
    """Test T8: subagent state management and cleanup in ACPEventConverter."""

    @pytest.fixture
    def converter(self, monkeypatch: pytest.MonkeyPatch) -> ACPEventConverter:
        """Converter configured for tool_box mode."""
        monkeypatch.setenv("ACP_SUBAGENT_DISPLAY_MODE", "tool_box")
        return ACPEventConverter()

    @pytest.mark.anyio
    async def test_subagent_event_stream_complete_emits_completed_and_cleans_up(
        self,
        converter: ACPEventConverter,
    ) -> None:
        """SubAgentEvent with StreamCompleteEvent emits status='completed' and cleans state."""
        # Seed state via SpawnSessionStart
        spawn = SpawnSessionStart(
            child_session_id="child_complete",
            parent_session_id="parent_001",
            tool_call_id="tc_complete",
            spawn_mechanism="spawn",
            source_name="coder",
            source_type="agent",
            description="Code review",
            run_mode="foreground",
        )
        await _collect_updates(converter, spawn)
        assert "child_complete" in converter._subagent_tool_map
        assert "child_complete" in converter._foreground_children

        # Emit completion
        complete_event = SubAgentEvent(
            child_session_id="child_complete",
            source_name="coder",
            source_type="agent",
            event=StreamCompleteEvent(message=None),  # type: ignore[arg-type]
            depth=1,
        )
        updates = await _collect_updates(converter, complete_event)

        # Should emit ToolCallProgress with completed status
        assert len(updates) == 1
        assert isinstance(updates[0], ToolCallProgress)
        assert updates[0].tool_call_id == "tc_complete"
        assert updates[0].status == "completed"
        assert updates[0].subagent is not None
        assert updates[0].subagent.child_session_id == "child_complete"

        # State should be cleaned up
        assert "child_complete" not in converter._subagent_tool_map
        assert "child_complete" not in converter._foreground_children

    @pytest.mark.anyio
    async def test_subagent_event_run_error_emits_failed_and_cleans_up(
        self,
        converter: ACPEventConverter,
    ) -> None:
        """SubAgentEvent with RunErrorEvent emits status='failed' with error content and cleans state."""
        # Seed state
        spawn = SpawnSessionStart(
            child_session_id="child_err",
            parent_session_id="parent_001",
            tool_call_id="tc_err",
            spawn_mechanism="task",
            source_name="analyzer",
            source_type="agent",
            description="Analyze code",
            run_mode="foreground",
        )
        await _collect_updates(converter, spawn)
        assert "child_err" in converter._subagent_tool_map
        assert "child_err" in converter._foreground_children

        # Emit error
        error_event = SubAgentEvent(
            child_session_id="child_err",
            source_name="analyzer",
            source_type="agent",
            event=RunErrorEvent(message="Connection timeout"),
            depth=1,
        )
        updates = await _collect_updates(converter, error_event)

        # Should emit ToolCallProgress with failed status
        assert len(updates) == 1
        assert isinstance(updates[0], ToolCallProgress)
        assert updates[0].tool_call_id == "tc_err"
        assert updates[0].status == "failed"
        assert updates[0].subagent is not None
        assert updates[0].subagent.child_session_id == "child_err"

        # Should include error content
        assert updates[0].content is not None
        assert len(updates[0].content) == 1
        assert isinstance(updates[0].content[0], ContentToolCallContent)
        assert "Error: Connection timeout" in updates[0].content[0].content.text

        # State should be cleaned up
        assert "child_err" not in converter._subagent_tool_map
        assert "child_err" not in converter._foreground_children

    @pytest.mark.anyio
    async def test_subagent_event_stream_complete_without_tool_map_still_cleans_foreground(
        self,
        converter: ACPEventConverter,
    ) -> None:
        """StreamCompleteEvent for unknown child_session_id still discards from foreground set."""
        # Manually add to foreground without tool_map entry
        converter._foreground_children.add("orphan_child")

        complete_event = SubAgentEvent(
            child_session_id="orphan_child",
            source_name="coder",
            source_type="agent",
            event=StreamCompleteEvent(message=None),  # type: ignore[arg-type]
            depth=1,
        )
        updates = await _collect_updates(converter, complete_event)

        # No ToolCallProgress since no mapping
        assert len(updates) == 0
        # But foreground child should be cleaned up
        assert "orphan_child" not in converter._foreground_children

    @pytest.mark.anyio
    async def test_subagent_event_run_error_without_tool_map_still_cleans_foreground(
        self,
        converter: ACPEventConverter,
    ) -> None:
        """RunErrorEvent for unknown child_session_id still discards from foreground set."""
        converter._foreground_children.add("orphan_err")

        error_event = SubAgentEvent(
            child_session_id="orphan_err",
            source_name="coder",
            source_type="agent",
            event=RunErrorEvent(message="Unknown error"),
            depth=1,
        )
        updates = await _collect_updates(converter, error_event)

        assert len(updates) == 0
        assert "orphan_err" not in converter._foreground_children

    @pytest.mark.anyio
    async def test_cleanup_clears_all_tracked_state(self, converter: ACPEventConverter) -> None:
        """cleanup() removes all entries from _subagent_tool_map and _foreground_children."""
        # Seed multiple children
        converter._subagent_tool_map["child_a"] = "tc_a"
        converter._subagent_tool_map["child_b"] = "tc_b"
        converter._foreground_children.add("child_a")
        converter._foreground_children.add("child_b")
        converter._foreground_children.add("child_c")

        converter.cleanup()

        assert len(converter._subagent_tool_map) == 0
        assert len(converter._foreground_children) == 0

    @pytest.mark.anyio
    async def test_multiple_children_independent_cleanup(
        self,
        converter: ACPEventConverter,
    ) -> None:
        """Completing one child does not affect state of other tracked children."""
        # Seed two children
        for child_id, tc_id in [("child_1", "tc_1"), ("child_2", "tc_2")]:
            spawn = SpawnSessionStart(
                child_session_id=child_id,
                parent_session_id="parent_001",
                tool_call_id=tc_id,
                spawn_mechanism="spawn",
                source_name="coder",
                source_type="agent",
                description="Task",
                run_mode="foreground",
            )
            await _collect_updates(converter, spawn)

        assert len(converter._subagent_tool_map) == 2
        assert len(converter._foreground_children) == 2

        # Complete only child_1
        complete_event = SubAgentEvent(
            child_session_id="child_1",
            source_name="coder",
            source_type="agent",
            event=StreamCompleteEvent(message=None),  # type: ignore[arg-type]
            depth=1,
        )
        await _collect_updates(converter, complete_event)

        # child_1 cleaned up, child_2 remains
        assert "child_1" not in converter._subagent_tool_map
        assert "child_1" not in converter._foreground_children
        assert "child_2" in converter._subagent_tool_map
        assert "child_2" in converter._foreground_children

    @pytest.mark.anyio
    async def test_subagent_event_stream_complete_fallback_tool_call_id(
        self,
        converter: ACPEventConverter,
    ) -> None:
        """StreamCompleteEvent uses fallback tool_call_id when tc_id was None at spawn."""
        spawn = SpawnSessionStart(
            child_session_id="child_fb",
            parent_session_id="parent_001",
            tool_call_id=None,
            spawn_mechanism="spawn",
            source_name="coder",
            source_type="agent",
            description="Task",
            run_mode="foreground",
        )
        await _collect_updates(converter, spawn)
        assert converter._subagent_tool_map["child_fb"] == "subagent:child_fb"

        complete_event = SubAgentEvent(
            child_session_id="child_fb",
            source_name="coder",
            source_type="agent",
            event=StreamCompleteEvent(message=None),  # type: ignore[arg-type]
            depth=1,
        )
        updates = await _collect_updates(converter, complete_event)

        assert len(updates) == 1
        assert isinstance(updates[0], ToolCallProgress)
        assert updates[0].tool_call_id == "subagent:child_fb"
        assert updates[0].status == "completed"

        assert "child_fb" not in converter._subagent_tool_map
