"""Base command class with node-type filtering support.

Re-exports from ``agentpool.commands.base`` for backward compatibility.
New code should import from ``agentpool.commands.base`` directly.
"""

from __future__ import annotations

from agentpool.commands.base import AgentCommand, NodeCommand


__all__ = ["AgentCommand", "NodeCommand"]
