"""File edit AI tools with streaming I/O support.

This module provides file editing capabilities with:
- Async non-blocking I/O via aiofiles
- Atomic writes (temp file + rename pattern)
- Real-time progress events via ToolCallProgressEvent
- Timeout protection for all operations
"""

# Legacy synchronous API
from .file_edit import edit_file_tool, edit_tool

# New streaming async API
from .streaming_file_edit import (
    FileOperationProgress,
    StreamingFileEditor,
    StreamingWriteTool,
    streaming_edit_file,
    streaming_write_file,
)


__all__ = [
    # Legacy API
    "edit_file_tool",
    "edit_tool",
    # New streaming API
    "FileOperationProgress",
    "StreamingFileEditor",
    "StreamingWriteTool",
    "streaming_edit_file",
    "streaming_write_file",
]
