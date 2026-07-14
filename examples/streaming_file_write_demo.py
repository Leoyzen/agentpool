#!/usr/bin/env python3
"""Demo script for streaming file write with progress reporting.

This script demonstrates the new streaming file editing capabilities:
- Async non-blocking I/O
- Real-time progress events
- Atomic writes (temp file + rename)
- Timeout protection

Usage:
    python streaming_file_write_demo.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from agentpool_toolsets.builtin.file_edit import StreamingFileEditor
from agentpool.agents.events.events import ToolCallProgressEvent


async def demo_basic_write() -> None:
    """Demonstrate basic file write with progress."""
    print("=" * 60)
    print("Demo 1: Basic File Write with Progress")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        temp_path = f.name

    try:
        editor = StreamingFileEditor(chunk_size=1024)  # 1KB chunks
        content = "Hello, World!\n" * 100  # ~1.3KB of content
        tool_call_id = "demo_tc_001"

        print(f"Writing to: {temp_path}")
        print(f"Content size: {len(content)} bytes")
        print()

        event_count = 0
        async for event in editor.write_file(temp_path, content, tool_call_id):
            event_count += 1
            if event.title:
                print(f"  [{event.status.upper()}] {event.title}")

            # Show progress details
            if event.progress is not None and event.total is not None:
                pct = (event.progress / event.total) * 100
                print(f"    Progress: {event.progress}/{event.total} ({pct:.0f}%)")

            # Show text items
            for item in event.items:
                if hasattr(item, "text") and item.text:
                    print(f"    {item.text}")

        print()
        print(f"✓ Write complete! Total events: {event_count}")

        # Verify content
        result = Path(temp_path).read_text()
        assert result == content, "Content mismatch!"
        print(f"✓ Content verified: {len(result)} bytes")

    finally:
        Path(temp_path).unlink(missing_ok=True)


async def demo_edit_with_diff() -> None:
    """Demonstrate file edit with diff display."""
    print()
    print("=" * 60)
    print("Demo 2: File Edit with Diff")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        temp_path = f.name
        f.write("""def hello():
    print("Hello, World!")
    return 42

if __name__ == "__main__":
    hello()
""")

    try:
        editor = StreamingFileEditor()
        tool_call_id = "demo_tc_002"

        print(f"Editing: {temp_path}")
        print()

        async for event in editor.edit_file(
            temp_path,
            old_string='    print("Hello, World!")',
            new_string='    print("Hello, Streaming World!")',
            tool_call_id=tool_call_id,
        ):
            print(f"  [{event.status.upper()}] {event.title}")

            # Show diff content if available
            for item in event.items:
                if item.type == "diff":
                    print()
                    print("  --- Diff Preview ---")
                    old_preview = item.old_text[:200] if item.old_text else ""
                    new_preview = item.new_text[:200] if item.new_text else ""
                    if old_preview:
                        print(f"  Old: {old_preview}...")
                    print(f"  New: {new_preview}...")
                    print("  -------------------")

        print()
        print("✓ Edit complete!")

        # Show final content
        final_content = Path(temp_path).read_text()
        print("\nFinal file content:")
        print(final_content)

    finally:
        Path(temp_path).unlink(missing_ok=True)


async def demo_large_file() -> None:
    """Demonstrate large file handling with streaming."""
    print()
    print("=" * 60)
    print("Demo 3: Large File Streaming (1MB)")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        temp_path = f.name

    try:
        # Create 1MB of content
        editor = StreamingFileEditor(chunk_size=64 * 1024)  # 64KB chunks
        content = "X" * (1024 * 1024)  # 1MB
        tool_call_id = "demo_tc_003"

        print(f"Writing 1MB file to: {temp_path}")
        print(f"Chunk size: 64KB")
        print()

        start_event = None
        complete_event = None
        progress_events = 0

        async for event in editor.write_file(temp_path, content, tool_call_id):
            if event.status == "in_progress" and start_event is None:
                start_event = event
            if event.status == "completed":
                complete_event = event
            if event.progress is not None:
                progress_events += 1
                # Only print every 10th progress to avoid spam
                if progress_events % 4 == 0:
                    print(f"  Chunk {event.progress}/{event.total}")

        print()
        print(f"✓ Large file write complete!")
        print(f"  Total progress events: {progress_events}")
        print(f"  File size: {Path(temp_path).stat().st_size / 1024 / 1024:.2f} MB")

    finally:
        Path(temp_path).unlink(missing_ok=True)


async def demo_atomic_write_safety() -> None:
    """Demonstrate atomic write safety."""
    print()
    print("=" * 60)
    print("Demo 4: Atomic Write Safety")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        temp_path = f.name
        f.write("Original content")

    try:
        print(f"File: {temp_path}")
        print("Original content: 'Original content'")
        print()

        # Show temp file pattern
        editor = StreamingFileEditor(use_atomic_write=True)
        content = "New atomic content"
        tool_call_id = "demo_tc_004"

        print("Performing atomic write...")
        print("  1. Writing to temp file (.filename.tmp.XXXX)")
        print("  2. Atomic rename to target file")
        print()

        async for event in editor.write_file(temp_path, content, tool_call_id):
            if event.status == "completed":
                print(f"  [{event.status.upper()}] {event.title}")

        result = Path(temp_path).read_text()
        print()
        print(f"✓ Atomic write successful")
        print(f"  Final content: '{result}'")
        print()
        print("Benefits:")
        print("  - Readers never see partial writes")
        print("  - No data corruption on crash")
        print("  - POSIX atomic rename guarantee")

    finally:
        Path(temp_path).unlink(missing_ok=True)


async def demo_timeout_protection() -> None:
    """Demonstrate timeout protection."""
    print()
    print("=" * 60)
    print("Demo 5: Timeout Protection")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        temp_path = f.name

    try:
        # Create editor with very short timeout
        editor = StreamingFileEditor(timeout=0.1)  # 100ms timeout
        content = "Test content"
        tool_call_id = "demo_tc_005"

        print(f"File: {temp_path}")
        print(f"Timeout: 100ms (artificially short for demo)")
        print()

        try:
            async for event in editor.write_file(temp_path, content, tool_call_id):
                print(f"  [{event.status.upper()}] {event.title}")
        except RuntimeError as e:
            print()
            print(f"✓ Timeout protection triggered: {e}")
            print()
            print("Benefits:")
            print("  - Prevents infinite hangs")
            print("  - Frees up event loop")
            print("  - Clear error messages")

    finally:
        Path(temp_path).unlink(missing_ok=True)


async def main() -> None:
    """Run all demos."""
    print("\n" + "=" * 60)
    print("Streaming File Write - Feature Demo")
    print("=" * 60)
    print()
    print("This demo showcases the new streaming file editing")
    print("capabilities with real-time progress reporting.")
    print()

    try:
        await demo_basic_write()
        await demo_edit_with_diff()
        await demo_large_file()
        await demo_atomic_write_safety()
        await demo_timeout_protection()

        print()
        print("=" * 60)
        print("All demos completed successfully!")
        print("=" * 60)
        print()
        print("Key Features Demonstrated:")
        print("  ✓ Async non-blocking I/O (aiofiles)")
        print("  ✓ Real-time progress events (ToolCallProgressEvent)")
        print("  ✓ Atomic writes (temp file + rename)")
        print("  ✓ Large file streaming (chunked I/O)")
        print("  ✓ Timeout protection (asyncio.timeout)")
        print()

    except Exception as e:
        print(f"\n✗ Demo failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
