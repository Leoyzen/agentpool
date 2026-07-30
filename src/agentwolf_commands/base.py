"""Base command class with node-type filtering support.

Re-exports from ``agentwolf.commands.base`` for backward compatibility.
New code should import from ``agentwolf.commands.base`` directly.
"""

from __future__ import annotations

from agentwolf.commands.base import AgentCommand, NodeCommand


__all__ = ["AgentCommand", "NodeCommand"]
