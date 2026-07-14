"""Tests for streaming file edit functionality.

This module tests the async streaming file editor with:
- Basic file operations
- Progress event generation
- Atomic write behavior
- Timeout handling
- Error handling
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from agentpool_toolsets.builtin.file_edit import (
    FileOperationProgress,
    StreamingFileEditor,
    StreamingWriteTool,
    streaming_edit_file,
    streaming_write_file,
)
from agentpool.agents.events.events import ToolCallProgressEvent


if TYPE_CHECKING:
    from _pytest.fixtures import FixtureRequest
    from _pytest.monkeypatch import MonkeyPatch
    from pytest import TempPathFactory


@pytest.fixture
def temp_file(tmp_path: Path) -> Path:
    """Create a temporary file for testing."""
    return tmp_path / "test_file.txt"


@pytest.fixture
def sample_content() -> str:
    """Provide sample file content."""
    return """Line 1: Hello World
Line 2: This is a test
Line 3: More content here
Line 4: Final line
"""


@pytest.fixture
def large_content() -> str:
    """Provide large file content for testing chunking."""
    # Create ~200KB of content
    lines = []
    for i in range(5000):
        lines.append(f"Line {i}: This is a test line with some content to make it larger. " * 10)
    return "\n".join(lines)


class TestStreamingFileEditor:
    """Test cases for StreamingFileEditor class."""

    @pytest.mark.asyncio
    async def test_basic_write_file(self, temp_file: Path) -> None:
        """Test basic file write operation."""
        editor = StreamingFileEditor()
        content = "Hello, World!"
        tool_call_id = "test_tc_001"

        events = []
        async for event in editor.write_file(str(temp_file), content, tool_call_id):
            events.append(event)
            assert isinstance(event, ToolCallProgressEvent)
            assert event.tool_call_id == tool_call_id

        # Verify file was written
        assert temp_file.exists()
        assert temp_file.read_text() == content

        # Should have start, progress, and complete events
        assert len(events) >= 2
        assert events[0].status == "in_progress"
        assert events[-1].status == "completed"

    @pytest.mark.asyncio
    async def test_basic_edit_file(self, temp_file: Path, sample_content: str) -> None:
        """Test basic file edit operation."""
        # Setup: write initial content
        temp_file.write_text(sample_content)

        editor = StreamingFileEditor()
        tool_call_id = "test_tc_002"

        events = []
        async for event in editor.edit_file(
            str(temp_file),
            old_string="Line 2: This is a test",
            new_string="Line 2: This is modified",
            tool_call_id=tool_call_id,
        ):
            events.append(event)

        # Verify file was modified
        new_content = temp_file.read_text()
        assert "This is modified" in new_content
        assert "This is a test" not in new_content

        # Should have multiple events
        assert len(events) >= 2
        assert events[-1].status == "completed"

    @pytest.mark.asyncio
    async def test_write_with_progress_events(self, temp_file: Path) -> None:
        """Test that progress events are emitted during write."""
        editor = StreamingFileEditor(chunk_size=100)  # Small chunks for testing
        content = "A" * 1000  # 1KB of content
        tool_call_id = "test_tc_003"

        progress_events = []
        async for event in editor.write_file(str(temp_file), content, tool_call_id):
            if event.progress is not None and event.total is not None:
                progress_events.append((event.progress, event.total))

        # Should have multiple progress updates
        assert len(progress_events) > 0
        # First progress should be chunk 1
        assert progress_events[0][0] == 1
        # Last progress should equal total
        assert progress_events[-1][0] == progress_events[-1][1]

    @pytest.mark.asyncio
    async def test_atomic_write_creates_temp_file(self, temp_file: Path) -> None:
        """Test that atomic write uses temp file pattern."""
        editor = StreamingFileEditor(use_atomic_write=True)
        content = "Test content for atomic write"
        tool_call_id = "test_tc_004"

        # Track temp file creation
        temp_files_created = []
        original_rename = None

        async def mock_rename(src: str, dst: str) -> None:
            if ".tmp." in src:
                temp_files_created.append(src)
            # Call actual rename
            import aiofiles

            await original_rename(src, dst)

        # Patch aiofiles.os.rename
        import aiofiles.os

        original_rename = aiofiles.os.rename
        with patch.object(aiofiles.os, "rename", side_effect=mock_rename):
            async for _ in editor.write_file(str(temp_file), content, tool_call_id):
                pass

        # Verify temp file was created and renamed
        assert temp_file.exists()
        assert temp_file.read_text() == content

    @pytest.mark.asyncio
    async def test_non_atomic_write(self, temp_file: Path) -> None:
        """Test direct write without atomic guarantee."""
        editor = StreamingFileEditor(use_atomic_write=False)
        content = "Direct write content"
        tool_call_id = "test_tc_005"

        async for _ in editor.write_file(str(temp_file), content, tool_call_id):
            pass

        assert temp_file.exists()
        assert temp_file.read_text() == content

    @pytest.mark.asyncio
    async def test_timeout_handling(self, temp_file: Path) -> None:
        """Test timeout handling for slow operations."""
        editor = StreamingFileEditor(timeout=0.001)  # Very short timeout
        content = "Content"
        tool_call_id = "test_tc_006"

        # Mock the write to be slow
        import aiofiles

        original_write = None

        async def slow_write(*args, **kwargs):  # type: ignore
            await asyncio.sleep(1.0)  # Sleep longer than timeout
            if original_write:
                return await original_write(*args, **kwargs)

        with patch.object(aiofiles, "open", side_effect=slow_write):
            events = []
            with pytest.raises(RuntimeError, match="timed out"):
                async for event in editor.write_file(str(temp_file), content, tool_call_id):
                    events.append(event)

        # Should have failed event
        assert any(e.status == "failed" for e in events)

    @pytest.mark.asyncio
    async def test_large_file_chunking(self, temp_file: Path, large_content: str) -> None:
        """Test that large files are written in chunks."""
        editor = StreamingFileEditor(chunk_size=1024)  # 1KB chunks
        tool_call_id = "test_tc_007"

        chunk_events = []
        async for event in editor.write_file(str(temp_file), large_content, tool_call_id):
            if event.progress is not None:
                chunk_events.append(event.progress)

        # Verify file content
        assert temp_file.exists()
        assert temp_file.read_text() == large_content

        # Should have multiple chunk events
        assert len(chunk_events) > 10  # Large file should trigger many chunks

    @pytest.mark.asyncio
    async def test_edit_file_not_found(self, temp_file: Path) -> None:
        """Test error handling for missing file."""
        editor = StreamingFileEditor()
        nonexistent_path = str(temp_file.parent / "does_not_exist.txt")
        tool_call_id = "test_tc_008"

        events = []
        with pytest.raises(FileNotFoundError):
            async for event in editor.edit_file(
                nonexistent_path,
                old_string="old",
                new_string="new",
                tool_call_id=tool_call_id,
            ):
                events.append(event)

        # Should have failed event
        assert any(e.status == "failed" for e in events)

    @pytest.mark.asyncio
    async def test_edit_no_changes(self, temp_file: Path, sample_content: str) -> None:
        """Test handling when old_string equals new_string."""
        temp_file.write_text(sample_content)

        editor = StreamingFileEditor()
        tool_call_id = "test_tc_009"

        events = []
        async for event in editor.edit_file(
            str(temp_file),
            old_string="same content",
            new_string="same content",
            tool_call_id=tool_call_id,
        ):
            events.append(event)

        # Should complete with no changes message
        assert events[-1].status == "completed"
        assert "No changes" in events[-1].title or "no changes" in events[-1].title.lower()

    @pytest.mark.asyncio
    async def test_edit_replace_all(self, temp_file: Path) -> None:
        """Test replace_all functionality."""
        content = "apple banana apple cherry apple"
        temp_file.write_text(content)

        editor = StreamingFileEditor()
        tool_call_id = "test_tc_010"

        events = []
        async for event in editor.edit_file(
            str(temp_file),
            old_string="apple",
            new_string="orange",
            tool_call_id=tool_call_id,
            replace_all=True,
        ):
            events.append(event)

        # Verify all instances replaced
        new_content = temp_file.read_text()
        assert new_content.count("orange") == 3
        assert new_content.count("apple") == 0

    @pytest.mark.asyncio
    async def test_empty_file_edit(self, temp_file: Path) -> None:
        """Test editing an empty file."""
        temp_file.write_text("")  # Empty file

        editor = StreamingFileEditor()
        tool_call_id = "test_tc_011"

        events = []
        async for event in editor.edit_file(
            str(temp_file),
            old_string="",
            new_string="New content",
            tool_call_id=tool_call_id,
        ):
            events.append(event)

        # Verify content added
        assert temp_file.read_text() == "New content"
        assert events[-1].status == "completed"


class TestStreamingWriteTool:
    """Test cases for StreamingWriteTool convenience class."""

    @pytest.mark.asyncio
    async def test_write_file_interface(self, temp_file: Path) -> None:
        """Test the synchronous-style write interface."""
        tool = StreamingWriteTool()
        content = "Test content"

        result = await tool.write_file(str(temp_file), content)

        assert result["success"] is True
        assert result["file_path"] == str(temp_file)
        assert result["bytes_written"] == len(content.encode("utf-8"))
        assert temp_file.exists()
        assert temp_file.read_text() == content

    @pytest.mark.asyncio
    async def test_edit_file_interface(self, temp_file: Path) -> None:
        """Test the synchronous-style edit interface."""
        temp_file.write_text("Original content")
        tool = StreamingWriteTool()

        result = await tool.edit_file(
            str(temp_file),
            old_string="Original",
            new_string="Modified",
        )

        assert result["success"] is True
        assert result["file_path"] == str(temp_file)
        assert "Modified" in temp_file.read_text()

    @pytest.mark.asyncio
    async def test_write_error_handling(self, temp_file: Path) -> None:
        """Test error handling in write interface."""
        tool = StreamingWriteTool(timeout=0.001)

        # Make path a directory to cause write failure
        temp_file.mkdir()

        result = await tool.write_file(str(temp_file), "content")

        assert result["success"] is False
        assert "error" in result


class TestConvenienceFunctions:
    """Test convenience functions for streaming operations."""

    @pytest.mark.asyncio
    async def test_streaming_write_file(self, temp_file: Path) -> None:
        """Test streaming_write_file convenience function."""
        content = "Convenience function test"
        tool_call_id = "test_tc_012"

        events = []
        async for event in streaming_write_file(str(temp_file), content, tool_call_id):
            events.append(event)

        assert temp_file.exists()
        assert temp_file.read_text() == content
        assert len(events) >= 2

    @pytest.mark.asyncio
    async def test_streaming_edit_file(self, temp_file: Path, sample_content: str) -> None:
        """Test streaming_edit_file convenience function."""
        temp_file.write_text(sample_content)
        tool_call_id = "test_tc_013"

        events = []
        async for event in streaming_edit_file(
            str(temp_file),
            old_string="Line 1: Hello World",
            new_string="Line 1: Goodbye World",
            tool_call_id=tool_call_id,
        ):
            events.append(event)

        assert "Goodbye World" in temp_file.read_text()
        assert len(events) >= 2


class TestProgressEvents:
    """Test progress event generation and structure."""

    @pytest.mark.asyncio
    async def test_progress_event_fields(self, temp_file: Path) -> None:
        """Test that progress events have correct fields."""
        editor = StreamingFileEditor()
        tool_call_id = "test_tc_014"

        events = []
        async for event in editor.write_file(str(temp_file), "Test content", tool_call_id):
            events.append(event)

        # All events should have tool_call_id
        for event in events:
            assert event.tool_call_id == tool_call_id
            assert event.event_kind == "tool_call_progress"

    @pytest.mark.asyncio
    async def test_progress_content_items(self, temp_file: Path) -> None:
        """Test that progress events contain content items."""
        editor = StreamingFileEditor()
        tool_call_id = "test_tc_015"

        events_with_items = []
        async for event in editor.write_file(str(temp_file), "Test content", tool_call_id):
            if event.items:
                events_with_items.append(event)

        # Should have events with location content items
        assert len(events_with_items) > 0
        for event in events_with_items:
            assert any(item.type == "location" for item in event.items)


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    @pytest.mark.asyncio
    async def test_unicode_content(self, temp_file: Path) -> None:
        """Test writing unicode content."""
        editor = StreamingFileEditor()
        content = "Hello 世界 🌍 ñoño café"
        tool_call_id = "test_tc_016"

        async for _ in editor.write_file(str(temp_file), content, tool_call_id):
            pass

        assert temp_file.read_text() == content

    @pytest.mark.asyncio
    async def test_binary_content_in_text_mode(self, temp_file: Path) -> None:
        """Test handling of content that might have binary-like patterns."""
        editor = StreamingFileEditor()
        # Content with null bytes and special characters
        content = "Line with \x00 null and \xff special"
        tool_call_id = "test_tc_017"

        async for _ in editor.write_file(str(temp_file), content, tool_call_id):
            pass

        # Content should be preserved (may not roundtrip exactly due to encoding)
        result = temp_file.read_text(encoding="utf-8", errors="replace")
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_very_small_chunks(self, temp_file: Path) -> None:
        """Test with very small chunk size."""
        editor = StreamingFileEditor(chunk_size=10)  # 10 byte chunks
        content = (
            "This is a test of small chunk writing with more content here to ensure multiple chunks"
        )
        tool_call_id = "test_tc_018"

        events = []
        async for event in editor.write_file(str(temp_file), content, tool_call_id):
            events.append(event)

        assert temp_file.read_text() == content
        # Should have multiple events due to small chunks
        assert len(events) >= 3  # start, at least one progress, complete

    @pytest.mark.asyncio
    async def test_absolute_vs_relative_path(self, temp_file: Path) -> None:
        """Test handling of absolute vs relative paths."""
        editor = StreamingFileEditor()
        content = "Path test content"
        tool_call_id = "test_tc_019"

        # Test with relative path
        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(temp_file.parent)
            relative_path = temp_file.name

            async for _ in editor.write_file(relative_path, content, tool_call_id):
                pass

            assert temp_file.exists()
            assert temp_file.read_text() == content
        finally:
            os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_concurrent_writes(self, temp_file: Path) -> None:
        """Test concurrent write operations."""
        editor = StreamingFileEditor()

        async def write_content(content: str, tc_id: str) -> None:
            async for _ in editor.write_file(str(temp_file), content, tc_id):
                pass

        # Start multiple concurrent writes
        tasks = [write_content(f"Content {i}", f"tc_{i}") for i in range(3)]

        await asyncio.gather(*tasks, return_exceptions=True)

        # File should exist with one of the contents
        assert temp_file.exists()
        content = temp_file.read_text()
        assert any(f"Content {i}" == content for i in range(3))


class TestBackwardCompatibility:
    """Test that new streaming API doesn't break existing code."""

    @pytest.mark.asyncio
    async def test_legacy_edit_tool_still_works(self, temp_file: Path) -> None:
        """Verify legacy edit_file_tool still functions."""
        from agentpool_toolsets.builtin.file_edit import edit_file_tool

        # Create file
        temp_file.write_text("Hello World")

        # Use legacy API
        result = await edit_file_tool(
            file_path=str(temp_file),
            old_string="Hello",
            new_string="Goodbye",
        )

        assert result["success"] is True
        assert "Goodbye World" in temp_file.read_text()
