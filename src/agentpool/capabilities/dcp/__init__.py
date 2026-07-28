"""Dynamic Context Pruning (DCP) capability — model-driven context window management.

Provides ``DynamicContextPruningCapability`` and ``DCPConfig`` for context
management with prune/distill/decompress tools and a 4-level watermark
escalation system.
"""

from __future__ import annotations

from agentpool.capabilities.dcp.capability import DynamicContextPruningCapability
from agentpool.capabilities.dcp.config import DCPConfig
from agentpool.capabilities.dcp.state import (
    CompressionBlock,
    DCPState,
    WatermarkLevel,
)

__all__ = [
    "CompressionBlock",
    "DCPConfig",
    "DCPState",
    "DynamicContextPruningCapability",
    "WatermarkLevel",
]
