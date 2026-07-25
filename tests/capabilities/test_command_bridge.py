"""Tests for the capability-command-bridge feature.

Covers:
  1. CommandEntry handler field (task 1.4)
  2. CommandBridge.discover_commands() (task 2.6)
  3. CommandBridge.execute() (task 2.7)
  4. CommandBridge.watch_changes() (task 2.8)
  5. entry_to_slashed_command() (task 2.9)
  6. SkillManagerCap handler tests (task 3.3)
  7. McpServerCap handler tests (task 3.4)
  8. SkillManagerCap pass-through test (task 3.5)
  9. Backward compatibility test (task 3.6)
"""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Self, cast
from unittest.mock import MagicMock

import pytest

from agentpool.capabilities.agent_context import AgentContext
from agentpool.capabilities.change_event import ChangeEvent
from agentpool.capabilities.command_bridge import (
    CommandBridge,
    CommandNotExecutableError,
    CommandNotFoundError,
)
from agentpool.capabilities.extension_registry import (
    ExtensionRegistry,
    Scope,
    ScopeLevel,
)
from agentpool.capabilities.resource_protocols import (
    CommandEntry,
)
from agentpool.capabilities.skill_manager_cap import SkillManagerCap
from agentpool.skills.skill import Skill


if TYPE_CHECKING:
    from agentpool.mcp_server.client import MCPClient


# ---- Shared fixtures and helpers ----


def _make_agent_context() -> AgentContext:
    """Create a minimal AgentContext with MagicMock fields for testing."""
    return AgentContext(
        agent_registry=MagicMock(),
        delegation=MagicMock(),
        session=MagicMock(),
        scope=MagicMock(),
        host=MagicMock(),
        extension_registry=None,
    )


class FakeCommandResource:
    """Fake capability implementing CommandResource for testing."""

    def __init__(
        self,
        name: str = "fake-cmd",
        commands: list[CommandEntry] | None = None,
    ) -> None:
        self._name = name
        self._commands = commands or []

    def get_serialization_name(self) -> str:
        return self._name

    async def list_commands(self) -> list[CommandEntry]:
        return list(self._commands)

    async def get_command(self, name: str) -> CommandEntry | None:
        return next((c for c in self._commands if c.name == name), None)


class FakeChangeObservable:
    """Fake capability implementing ChangeObservable for testing."""

    def __init__(
        self,
        name: str = "fake-obs",
        events: list[ChangeEvent] | None = None,
    ) -> None:
        self._name = name
        self._events = events or []

    def get_serialization_name(self) -> str:
        return self._name

    def on_change(self) -> asyncio.Queue[ChangeEvent] | None:
        if not self._events:
            return None
        queue: asyncio.Queue[ChangeEvent | None] = asyncio.Queue()

        async def _gen() -> Any:
            for ev in self._events:
                await queue.put(ev)
            await queue.put(None)  # sentinel

        # Push events into queue immediately via a task wrapper.
        # We return a simple async generator instead.
        async def _iterator() -> Any:
            for ev in self._events:
                yield ev

        return _iterator()


# =====================================================================
# 1. CommandEntry handler field tests (task 1.4)
# =====================================================================


@pytest.mark.unit
async def test_command_entry_with_handler_populated() -> None:
    """CommandEntry with handler is not None and is callable."""

    async def handler(input_text: str, ctx: AgentContext) -> str:
        return f"result: {input_text}"

    entry = CommandEntry(
        name="test-cmd",
        description="A test command",
        handler=handler,
    )
    assert entry.handler is not None
    assert callable(entry.handler)


@pytest.mark.unit
async def test_command_entry_with_handler_none_default() -> None:
    """CommandEntry handler defaults to None."""
    entry = CommandEntry(name="display-only")
    assert entry.handler is None


@pytest.mark.unit
async def test_command_entry_handler_returns_expected_result() -> None:
    """Handler is callable and returns expected result when invoked."""

    async def handler(input_text: str, ctx: AgentContext) -> str:
        return f"processed({input_text})"

    entry = CommandEntry(name="echo", handler=handler)
    ctx = _make_agent_context()
    assert entry.handler is not None
    result = await entry.handler("hello", ctx)
    assert result == "processed(hello)"


@pytest.mark.unit
async def test_command_entry_compare_false_handler() -> None:
    """Two CommandEntry with same fields but different handlers are equal."""

    async def handler_a(input_text: str, ctx: AgentContext) -> str:
        return "a"

    async def handler_b(input_text: str, ctx: AgentContext) -> str:
        return "b"

    entry_a = CommandEntry(
        name="cmd",
        description="desc",
        skill_uri="skill://cmd",
        source="local",
        handler=handler_a,
    )
    entry_b = CommandEntry(
        name="cmd",
        description="desc",
        skill_uri="skill://cmd",
        source="local",
        handler=handler_b,
    )
    # compare=False means handler is excluded from equality.
    assert entry_a == entry_b
    # But handlers are different objects.
    assert entry_a.handler is not entry_b.handler


# =====================================================================
# 2. CommandBridge.discover_commands() tests (task 2.6)
# =====================================================================


@pytest.mark.unit
async def test_discover_commands_multiple_capabilities() -> None:
    """Multiple capabilities each return commands — all discovered."""

    async def handler_a(input_text: str, ctx: AgentContext) -> str:
        return "a"

    async def handler_b(input_text: str, ctx: AgentContext) -> str:
        return "b"

    cap_a = FakeCommandResource(
        "cap-a",
        [CommandEntry(name="cmd-a", description="A", handler=handler_a)],
    )
    cap_b = FakeCommandResource(
        "cap-b",
        [CommandEntry(name="cmd-b", description="B", handler=handler_b)],
    )
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    registry.register(cap_a, scope)  # type: ignore[arg-type]
    registry.register(cap_b, scope)  # type: ignore[arg-type]

    bridge = CommandBridge(registry, scope)
    commands = await bridge.discover_commands()
    names = [c.name for c in commands]
    assert "cmd-a" in names
    assert "cmd-b" in names
    assert len(commands) == 2


@pytest.mark.unit
async def test_discover_commands_duplicate_at_different_scopes_turn_wins() -> None:
    """Same command name at TURN and POOL — TURN wins (most specific first)."""

    async def turn_handler(input_text: str, ctx: AgentContext) -> str:
        return "turn"

    async def pool_handler(input_text: str, ctx: AgentContext) -> str:
        return "pool"

    turn_cap = FakeCommandResource(
        "turn-cap",
        [CommandEntry(name="dup", description="turn", handler=turn_handler)],
    )
    pool_cap = FakeCommandResource(
        "pool-cap",
        [CommandEntry(name="dup", description="pool", handler=pool_handler)],
    )
    registry = ExtensionRegistry()
    pool_scope = Scope(level=ScopeLevel.POOL)
    turn_scope = Scope(
        level=ScopeLevel.TURN,
        session_id="s1",
        agent_name="a1",
        turn_id="t1",
    )
    registry.register(pool_cap, pool_scope)  # type: ignore[arg-type]
    registry.register(turn_cap, turn_scope)  # type: ignore[arg-type]

    bridge = CommandBridge(registry, turn_scope)
    commands = await bridge.discover_commands()
    assert len(commands) == 1
    assert commands[0].name == "dup"
    assert commands[0].description == "turn"


@pytest.mark.unit
async def test_discover_commands_empty_registry() -> None:
    """No capabilities → empty list."""
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    bridge = CommandBridge(registry, scope)
    commands = await bridge.discover_commands()
    assert commands == []


@pytest.mark.unit
async def test_discover_commands_builds_index_for_execute() -> None:
    """After discover_commands(), execute() can find commands by name."""

    async def handler(input_text: str, ctx: AgentContext) -> str:
        return "ok"

    cap = FakeCommandResource(
        "cap",
        [CommandEntry(name="indexed", description="test", handler=handler)],
    )
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    registry.register(cap, scope)  # type: ignore[arg-type]

    bridge = CommandBridge(registry, scope)
    await bridge.discover_commands()
    ctx = _make_agent_context()
    result = await bridge.execute("indexed", "input", ctx)
    assert result == "ok"


# =====================================================================
# 3. CommandBridge.execute() tests (task 2.7)
# =====================================================================


@pytest.mark.unit
async def test_execute_handler_found_returns_result() -> None:
    """execute(name, input, ctx) returns handler output."""

    async def handler(input_text: str, ctx: AgentContext) -> str:
        return f"handled:{input_text}"

    cap = FakeCommandResource(
        "cap",
        [CommandEntry(name="my-cmd", description="test", handler=handler)],
    )
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    registry.register(cap, scope)  # type: ignore[arg-type]

    bridge = CommandBridge(registry, scope)
    await bridge.discover_commands()
    ctx = _make_agent_context()
    result = await bridge.execute("my-cmd", "hello", ctx)
    assert result == "handled:hello"


@pytest.mark.unit
async def test_execute_handler_none_raises_not_executable() -> None:
    """Handler is None → raises CommandNotExecutableError."""
    cap = FakeCommandResource(
        "cap",
        [CommandEntry(name="display-only", description="no handler")],
    )
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    registry.register(cap, scope)  # type: ignore[arg-type]

    bridge = CommandBridge(registry, scope)
    await bridge.discover_commands()
    ctx = _make_agent_context()
    with pytest.raises(CommandNotExecutableError) as exc_info:
        await bridge.execute("display-only", "input", ctx)
    assert exc_info.value.name == "display-only"


@pytest.mark.unit
async def test_execute_command_not_found_raises() -> None:
    """Command not found → raises CommandNotFoundError."""
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    bridge = CommandBridge(registry, scope)
    await bridge.discover_commands()
    ctx = _make_agent_context()
    with pytest.raises(CommandNotFoundError) as exc_info:
        await bridge.execute("nonexistent", "input", ctx)
    assert exc_info.value.name == "nonexistent"


@pytest.mark.unit
async def test_execute_handler_exception_propagates_unwrapped() -> None:
    """If handler raises ValueError, it propagates as ValueError (not wrapped)."""

    async def handler(input_text: str, ctx: AgentContext) -> str:
        msg = "boom"
        raise ValueError(msg)

    cap = FakeCommandResource(
        "cap",
        [CommandEntry(name="error-cmd", description="test", handler=handler)],
    )
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    registry.register(cap, scope)  # type: ignore[arg-type]

    bridge = CommandBridge(registry, scope)
    await bridge.discover_commands()
    ctx = _make_agent_context()
    with pytest.raises(ValueError, match="boom"):
        await bridge.execute("error-cmd", "input", ctx)


# =====================================================================
# 4. CommandBridge.watch_changes() tests (task 2.8)
# =====================================================================


@pytest.mark.unit
async def test_watch_changes_commands_changed_forwarded() -> None:
    """'commands_changed' event is forwarded by watch_changes()."""
    event = ChangeEvent(
        capability_name="cap",
        kind="commands_changed",
        source_uri="skill://cap",
    )
    obs = FakeChangeObservable("obs", [event])
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    registry.register(obs, scope)  # type: ignore[arg-type]

    bridge = CommandBridge(registry, scope)
    results: list[ChangeEvent] = []
    async for ev in bridge.watch_changes():
        results.append(ev)
        break  # Only one event expected
    assert len(results) == 1
    assert results[0].kind == "commands_changed"


@pytest.mark.unit
async def test_watch_changes_skills_changed_forwarded() -> None:
    """'skills_changed' event is forwarded by watch_changes()."""
    event = ChangeEvent(
        capability_name="cap",
        kind="skills_changed",
        source_uri="skill://cap",
    )
    obs = FakeChangeObservable("obs", [event])
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    registry.register(obs, scope)  # type: ignore[arg-type]

    bridge = CommandBridge(registry, scope)
    results: list[ChangeEvent] = []
    async for ev in bridge.watch_changes():
        results.append(ev)
        break
    assert len(results) == 1
    assert results[0].kind == "skills_changed"


@pytest.mark.unit
async def test_watch_changes_prompts_changed_forwarded() -> None:
    """'prompts_changed' event is forwarded by watch_changes()."""
    event = ChangeEvent(
        capability_name="cap",
        kind="prompts_changed",
        source_uri="mcp://cap",
    )
    obs = FakeChangeObservable("obs", [event])
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    registry.register(obs, scope)  # type: ignore[arg-type]

    bridge = CommandBridge(registry, scope)
    results: list[ChangeEvent] = []
    async for ev in bridge.watch_changes():
        results.append(ev)
        break
    assert len(results) == 1
    assert results[0].kind == "prompts_changed"


@pytest.mark.unit
async def test_watch_changes_tools_changed_filtered_out() -> None:
    """'tools_changed' event is filtered out (not forwarded)."""
    tool_event = ChangeEvent(
        capability_name="cap",
        kind="tools_changed",
        source_uri="mcp://cap",
    )
    cmd_event = ChangeEvent(
        capability_name="cap",
        kind="commands_changed",
        source_uri="skill://cap",
    )
    obs = FakeChangeObservable("obs", [tool_event, cmd_event])
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    registry.register(obs, scope)  # type: ignore[arg-type]

    bridge = CommandBridge(registry, scope)
    results = [ev async for ev in bridge.watch_changes()]
    # tools_changed should be filtered, only commands_changed forwarded.
    assert len(results) == 1
    assert results[0].kind == "commands_changed"


@pytest.mark.unit
async def test_watch_changes_merge_returns_none_empty_iterator() -> None:
    """merge_change_streams returns None → empty async iterator (no items yielded)."""
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    bridge = CommandBridge(registry, scope)
    results = [ev async for ev in bridge.watch_changes()]
    assert results == []


# =====================================================================
# 5. entry_to_slashed_command() tests (task 2.9)
# =====================================================================


@pytest.mark.unit
async def test_entry_to_slashed_command_with_handler() -> None:
    """Entry with handler → returns SlashedCommand (not None)."""
    from slashed import Command as SlashedCommand

    async def handler(input_text: str, ctx: AgentContext) -> str:
        return "ok"

    entry = CommandEntry(
        name="my-cmd",
        description="A command",
        handler=handler,
    )
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    bridge = CommandBridge(registry, scope)

    result = CommandBridge.entry_to_slashed_command(entry, bridge)
    assert result is not None
    assert isinstance(result, SlashedCommand)
    assert result.name == "my-cmd"


@pytest.mark.unit
async def test_entry_to_slashed_command_without_handler_returns_none() -> None:
    """Entry without handler (handler=None) → returns None."""
    entry = CommandEntry(
        name="display-only",
        description="No handler",
    )
    registry = ExtensionRegistry()
    scope = Scope(level=ScopeLevel.POOL)
    bridge = CommandBridge(registry, scope)

    result = CommandBridge.entry_to_slashed_command(entry, bridge)
    assert result is None


# =====================================================================
# 6. SkillManagerCap handler tests (task 3.3)
# =====================================================================


@pytest.mark.unit
async def test_skill_manager_cap_list_commands_returns_entries_with_handlers() -> None:
    """SkillManagerCap.list_commands() returns entries with callable handlers."""
    skill = Skill(
        name="my-skill",
        description="A test skill",
        skill_path=PurePosixPath("skill://local/my-skill"),
        instructions="Skill instructions here.",
    )
    cap = SkillManagerCap(local_skills={"my-skill": skill})
    commands = await cap.list_commands()
    assert len(commands) == 1
    entry = commands[0]
    assert entry.name == "my-skill"
    assert entry.description == "A test skill"
    assert entry.source == "local"
    assert entry.skill_uri == "skill://my-skill"
    assert entry.handler is not None
    assert callable(entry.handler)


@pytest.mark.unit
async def test_skill_manager_cap_handler_loads_skill_and_concatenates_input() -> None:
    """Handler loads skill content and concatenates with input when invoked."""
    skill = Skill(
        name="concat-skill",
        description="Concatenation test",
        skill_path=PurePosixPath("skill://local/concat-skill"),
        instructions="INSTRUCTIONS_CONTENT",
    )
    cap = SkillManagerCap(local_skills={"concat-skill": skill})
    commands = await cap.list_commands()
    assert len(commands) == 1
    entry = commands[0]
    assert entry.handler is not None
    ctx = _make_agent_context()
    result = await entry.handler("USER_INPUT", ctx)  # type: ignore[misc]
    assert "INSTRUCTIONS_CONTENT" in result
    assert "USER_INPUT" in result
    # Instructions come first, then user input.
    assert result.index("INSTRUCTIONS_CONTENT") < result.index("USER_INPUT")


# =====================================================================
# 7. McpServerCap handler tests (task 3.4)
# =====================================================================


class MockMcpClient:
    """Mock MCPClient for testing McpServerCap without real connections."""

    def __init__(
        self,
        prompts: list[Any] | None = None,
        prompt_results: dict[str, Any] | None = None,
    ) -> None:
        self._prompts = prompts or []
        self._prompt_results = prompt_results or {}

    async def list_prompts(self) -> list[Any]:
        return list(self._prompts)

    async def get_prompt(self, name: str, arguments: dict[str, str] | None) -> Any:
        result = self._prompt_results.get(name)
        if result is None:
            # Return empty messages
            return MagicMock(messages=[])
        return result

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


def _make_mcp_config(client_id: str = "test-server") -> MagicMock:
    """Create a fake MCP server config for McpServerCap."""
    config = MagicMock()
    config.client_id = client_id
    return config


@pytest.mark.unit
async def test_mcp_server_cap_list_commands_returns_entries_with_handlers() -> None:
    """McpServerCap.list_commands() returns entries with callable handlers."""
    from agentpool.capabilities.mcp_server_cap import McpServerCap

    # Create a mock prompt object.
    mock_prompt = MagicMock()
    mock_prompt.name = "greet"
    mock_prompt.description = "A greeting prompt"

    # Create a mock prompt result.
    mock_content = MagicMock()
    mock_content.text = "Hello from MCP!"
    mock_message = MagicMock()
    mock_message.content = mock_content
    mock_result = MagicMock()
    mock_result.messages = [mock_message]

    mock_client = MockMcpClient(
        prompts=[mock_prompt],
        prompt_results={"greet": mock_result},
    )

    cap = McpServerCap(config=_make_mcp_config(), client=cast("MCPClient", mock_client))

    commands = await cap.list_commands()
    assert len(commands) == 1
    entry = commands[0]
    assert entry.name == "greet"
    assert entry.description == "A greeting prompt"
    assert entry.source == "remote"
    assert entry.handler is not None
    assert callable(entry.handler)


@pytest.mark.unit
async def test_mcp_server_cap_handler_calls_get_prompt() -> None:
    """Handler calls get_prompt(name, arguments) when invoked."""
    from agentpool.capabilities.mcp_server_cap import McpServerCap

    mock_prompt = MagicMock()
    mock_prompt.name = "summarize"
    mock_prompt.description = "Summarize text"

    mock_content = MagicMock()
    mock_content.text = "Summary result"
    mock_message = MagicMock()
    mock_message.content = mock_content
    mock_result = MagicMock()
    mock_result.messages = [mock_message]

    mock_client = MockMcpClient(
        prompts=[mock_prompt],
        prompt_results={"summarize": mock_result},
    )

    cap = McpServerCap(config=_make_mcp_config(), client=cast("MCPClient", mock_client))

    commands = await cap.list_commands()
    assert len(commands) == 1
    entry = commands[0]
    assert entry.handler is not None
    ctx = _make_agent_context()
    result = await entry.handler("some text", ctx)  # type: ignore[misc]
    assert result == "Summary result"


# =====================================================================
# 8. SkillManagerCap pass-through test (task 3.5)
# =====================================================================


class MockCommandResourceForPassthrough:
    """Mock McpServerCap-like child that implements CommandResource."""

    def __init__(self) -> None:
        self._name = "mock-mcp"

        async def _remote_handler(input_text: str, ctx: AgentContext) -> str:
            return "remote-result"

        self._commands = [
            CommandEntry(
                name="remote-cmd",
                description="Remote command",
                skill_uri="skill://mock-mcp/remote-cmd",
                source="remote",
                handler=_remote_handler,
            )
        ]

    def get_serialization_name(self) -> str:
        return self._name

    def get_toolset(self) -> Any:
        return None

    def get_instructions(self) -> str | None:
        return None

    async def list_commands(self) -> list[CommandEntry]:
        return list(self._commands)

    async def get_command(self, name: str) -> CommandEntry | None:
        return next((c for c in self._commands if c.name == name), None)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


@pytest.mark.unit
async def test_skill_manager_cap_passes_through_mcp_commands_unchanged() -> None:
    """SkillManagerCap passes through McpServerCap commands unchanged."""
    child = MockCommandResourceForPassthrough()
    # The child's original handler object.
    original_handler = child._commands[0].handler

    cap = SkillManagerCap(
        local_skills={},
        children=[child],  # type: ignore[list-item]
    )
    commands = await cap.list_commands()
    # Should have the remote command passed through.
    assert len(commands) == 1
    entry = commands[0]
    assert entry.name == "remote-cmd"
    assert entry.source == "remote"
    # The handler should be the same object, not re-wrapped.
    assert entry.handler is original_handler


# =====================================================================
# 9. Backward compatibility test (task 3.6)
# =====================================================================


@pytest.mark.unit
async def test_command_entry_without_handler_still_works() -> None:
    """Existing code that creates CommandEntry without handler still works."""
    entry = CommandEntry(
        name="legacy-cmd",
        description="A legacy display-only command",
        skill_uri="skill://legacy",
        source="local",
    )
    assert entry.handler is None
    assert entry.name == "legacy-cmd"
    assert entry.description == "A legacy display-only command"
    assert entry.skill_uri == "skill://legacy"
    assert entry.source == "local"


@pytest.mark.unit
async def test_command_entry_equality_without_handler() -> None:
    """CommandEntry equality without handler works as before."""
    entry_a = CommandEntry(
        name="cmd",
        description="desc",
        skill_uri="skill://cmd",
        source="local",
    )
    entry_b = CommandEntry(
        name="cmd",
        description="desc",
        skill_uri="skill://cmd",
        source="local",
    )
    assert entry_a == entry_b

    # Different name → not equal.
    entry_c = CommandEntry(name="other", description="desc")
    assert entry_a != entry_c
