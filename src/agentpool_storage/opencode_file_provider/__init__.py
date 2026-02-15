"""OpenCode file-based storage provider.

This package implements the storage backend compatible with OpenCode's
normalized JSON file format (pre-SQLite migration).

See ARCHITECTURE.md for detailed documentation of the storage format and
design decisions.
"""

from __future__ import annotations

from agentpool_storage.opencode_file_provider.provider import (
    OpenCodeFileStorageProvider,
    OpenCodeSessionMetadata,
)

__all__ = [
    "OpenCodeFileStorageProvider",
    "OpenCodeSessionMetadata",
]
