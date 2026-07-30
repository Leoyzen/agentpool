"""AG-UI server module for agentwolf.

This module provides server implementation for exposing AgentPool agents
via the AG-UI protocol with each agent on its own route.

Supports all agent types (native, ACP, Claude Code, Codex, AG-UI) through
the BaseAgentAGUIAdapter.
"""

from __future__ import annotations

from agentwolf_server.agui_server.base_agent_adapter import BaseAgentAGUIAdapter
from agentwolf_server.agui_server.server import AGUIServer

__all__ = ["AGUIServer", "BaseAgentAGUIAdapter"]
