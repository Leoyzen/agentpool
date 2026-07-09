"""HostContext — frozen dataclass capturing AgentPool infrastructure fields.

This is the Wave 1 foundation for the host layer. It maps existing
AgentPool fields into an immutable context object that downstream
factory and registry layers will consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentpool.host.stubs import CapabilityCache, ModelCache, ModelRegistry


if TYPE_CHECKING:
    from pathlib import Path

    from anyenv import ProcessManager

    from agentpool.delegation.pool import AgentPool
    from agentpool.mcp_server.manager import MCPManager
    from agentpool.models.manifest import AgentsManifest
    from agentpool.orchestrator import SessionPool
    from agentpool.prompts.manager import PromptManager
    from agentpool.resource_providers.skills_instruction import SkillsInstructionProvider
    from agentpool.skills.manager import SkillsManager
    from agentpool.storage import StorageManager
    from agentpool.talk.registry import ConnectionRegistry
    from agentpool.utils.streams import FileOpsTracker
    from agentpool.utils.todos import TodoTracker
    from agentpool.vfs_registry import VFSRegistry
    from agentpool_toolsets.builtin.skills import SkillsTools


@dataclass(frozen=True)
class HostContext:
    """Immutable snapshot of AgentPool infrastructure.

    Captures all shared services from an AgentPool instance so that
    agent factory and registry layers can receive a single typed
    context instead of the full pool object.
    """

    manifest: AgentsManifest
    storage: StorageManager
    vfs_registry: VFSRegistry
    connection_registry: ConnectionRegistry
    mcp: MCPManager
    skills_registry: SkillsManager
    skills_instruction_provider: SkillsInstructionProvider | None
    skills_tools_provider: SkillsTools | None
    prompt_manager: PromptManager
    process_manager: ProcessManager
    file_ops: FileOpsTracker
    todos: TodoTracker
    session_pool: SessionPool | None
    config_file_path: str | Path | None
    config_id: str | None = None
    tenant_id: str | None = None
    capability_cache: CapabilityCache = field(default_factory=CapabilityCache)
    model_registry: ModelRegistry = field(default_factory=ModelRegistry)
    model_cache: ModelCache = field(default_factory=ModelCache)
    pool: AgentPool[Any] | None = None
