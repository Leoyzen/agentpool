"""Performance benchmarks for skill command registration and bridge conversions.

This module provides performance benchmarks for:
- Skill command registration throughput
- Skill discovery performance
- Protocol bridge conversions (ACP, AG-UI, OpenCode)

Thresholds (adjust based on CI/environment performance):
- Registration: <200ms for 100 commands (typical development environment)
- Discovery: <500ms for 50 skills (includes filesystem I/O)
- Bridge conversion: <100ms for direct conversion of 100 commands
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from upathtools import UPath, to_upath

from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.registry import SkillsRegistry
from agentpool.skills.skill import Skill
from agentpool_server.acp_server.commands.skill_commands import ACPSkillBridge
from agentpool_server.agui_server.skill_tools import AGUISkillBridge
from agentpool_server.opencode_server.skill_bridge import OpenCodeSkillBridge, create_skill_command


# Thresholds (in milliseconds) - adjusted for realistic CI environment performance
REGISTRATION_THRESHOLD_MS = 200.0  # 100 command registrations with handlers
DISCOVERY_THRESHOLD_MS = 500.0  # 50 skills from filesystem (includes I/O)
BRIDGE_CONVERSION_THRESHOLD_MS = 100.0  # 100 command conversions


def _create_mock_skill(name: str) -> MagicMock:
    """Create a mock skill with the given name."""
    skill = MagicMock()
    skill.name = name
    skill.description = f"Description for {name}"
    skill.load_instructions = MagicMock(return_value="Instructions for " + name)
    return skill


def _create_skill_command(name: str) -> SkillCommand:
    """Create a SkillCommand instance with a mock skill."""
    skill = _create_mock_skill(name)
    return SkillCommand(
        name=name,
        description=f"Description for {name}",
        skill=skill,
        input_hint=f"Arguments for {name}",
        category="test",
    )


def _create_real_skill(name: str, base_path: str | UPath) -> Skill:
    """Create a real Skill instance with SKILL.md in a temp directory."""
    # Convert to UPath using to_upath
    base_upath = to_upath(base_path)
    skill_dir = base_upath / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_content = f"""---
name: {name}
description: Description for {name}
---

# {name}

Instructions for {name} skill.
"""
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(skill_content, encoding="utf-8")

    return Skill.from_skill_dir(skill_dir)


# =============================================================================
# Registration Performance Tests
# =============================================================================


def test_registration_100_commands() -> None:
    """Benchmark registering 100 skill commands.

    Verifies that SkillCommandRegistry can register 100 commands efficiently.
    """
    registry = SkillCommandRegistry()
    commands = [_create_skill_command(f"skill-{i}") for i in range(100)]

    start = time.perf_counter()
    for i, cmd in enumerate(commands):
        registry.register(f"skill-{i}", cmd)
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    assert len(registry) == 100
    assert duration_ms < REGISTRATION_THRESHOLD_MS, (
        f"Registration of 100 commands took {duration_ms:.2f}ms, "
        f"expected <{REGISTRATION_THRESHOLD_MS}ms"
    )


def test_registration_100_commands_with_handler() -> None:
    """Benchmark registration with a change handler callback.

    Verifies that registration performance remains acceptable when
    a change handler is attached (simulating real-world usage).
    """
    registry = SkillCommandRegistry()
    bridge = ACPSkillBridge()
    registry.on_command_change(bridge.handle_change)

    commands = [_create_skill_command(f"skill-{i}") for i in range(100)]

    start = time.perf_counter()
    for i, cmd in enumerate(commands):
        registry.register(f"skill-{i}", cmd)
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    assert len(registry) == 100
    assert len(bridge.get_available_commands()) == 100
    # Allow slightly more time with handler attached
    assert duration_ms < REGISTRATION_THRESHOLD_MS * 1.5, (
        f"Registration with handler took {duration_ms:.2f}ms, "
        f"expected <{REGISTRATION_THRESHOLD_MS * 1.5}ms"
    )


# =============================================================================
# Skill Discovery Performance Tests
# =============================================================================


@pytest.mark.asyncio
async def test_skill_discovery_50_skills(tmp_path: str) -> None:
    """Benchmark discovering 50 skills from filesystem.

    Verifies that SkillsRegistry can discover and parse 50 skills
    from the filesystem within reasonable time (includes I/O overhead).
    """
    # Convert tmp_path to UPath
    skills_base_dir = to_upath(tmp_path) / "skills"
    skills_base_dir.mkdir(parents=True, exist_ok=True)

    # Create 50 skill directories with SKILL.md files
    for i in range(50):
        _create_real_skill(f"test-skill-{i}", skills_base_dir)

    registry = SkillsRegistry(skills_dirs=[skills_base_dir])

    start = time.perf_counter()
    await registry.discover_skills()
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    assert len(registry) == 50
    assert duration_ms < DISCOVERY_THRESHOLD_MS, (
        f"Discovery of 50 skills took {duration_ms:.2f}ms, expected <{DISCOVERY_THRESHOLD_MS}ms"
    )


@pytest.mark.asyncio
async def test_skill_discovery_50_skills_with_command_registry(tmp_path: str) -> None:
    """Benchmark discovery with automatic command registration.

    Verifies that skill discovery + command registration for 50 skills
    completes within reasonable time.
    """
    # Convert tmp_path to UPath
    skills_base_dir = to_upath(tmp_path) / "skills"
    skills_base_dir.mkdir(parents=True, exist_ok=True)

    # Create 50 skill directories with SKILL.md files
    for i in range(50):
        _create_real_skill(f"test-skill-{i}", skills_base_dir)

    skills_registry = SkillsRegistry(skills_dirs=[skills_base_dir])
    command_registry = SkillCommandRegistry(skills_registry=skills_registry)

    start = time.perf_counter()
    await skills_registry.discover_skills()
    await command_registry.initialize()
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    assert len(skills_registry) == 50
    assert len(command_registry) == 50
    # Allow more time for full chain (discovery + parsing + command registration)
    assert duration_ms < DISCOVERY_THRESHOLD_MS * 1.5, (
        f"Discovery + command registration took {duration_ms:.2f}ms, "
        f"expected <{DISCOVERY_THRESHOLD_MS * 1.5}ms"
    )


# =============================================================================
# ACP Bridge Conversion Performance Tests
# =============================================================================


def test_acp_bridge_conversion() -> None:
    """Benchmark converting 100 SkillCommand to ACP AvailableCommand.

    Verifies that ACPSkillBridge can convert 100 skill commands
    to ACP format in reasonable time.
    """
    bridge = ACPSkillBridge()
    commands = [_create_skill_command(f"skill-{i}") for i in range(100)]

    start = time.perf_counter()
    for i, cmd in enumerate(commands):
        bridge.handle_change(f"skill-{i}", cmd)
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    assert len(bridge.get_available_commands()) == 100
    assert duration_ms < BRIDGE_CONVERSION_THRESHOLD_MS, (
        f"ACP bridge conversion of 100 commands took {duration_ms:.2f}ms, "
        f"expected <{BRIDGE_CONVERSION_THRESHOLD_MS}ms"
    )


@pytest.mark.asyncio
async def test_acp_bridge_bulk_conversion(tmp_path: str) -> None:
    """Benchmark bulk conversion through registry.

    Tests the performance of converting many skills through
    the full registration chain to ACP format.
    """
    skills_base_dir = to_upath(tmp_path) / "skills"
    skills_base_dir.mkdir(parents=True, exist_ok=True)

    # Create 100 real skills
    for i in range(100):
        _create_real_skill(f"skill-{i}", skills_base_dir)

    skills_registry = SkillsRegistry(skills_dirs=[skills_base_dir])
    command_registry = SkillCommandRegistry(skills_registry=skills_registry)
    bridge = ACPSkillBridge()

    # Subscribe bridge to command registry
    command_registry.on_command_change(bridge.handle_change)

    # Discover skills first
    await skills_registry.discover_skills()

    # Time only the command registry initialization (conversion)
    start = time.perf_counter()
    await command_registry.initialize()
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    assert len(bridge.get_available_commands()) == 100
    assert duration_ms < BRIDGE_CONVERSION_THRESHOLD_MS * 2


# =============================================================================
# AG-UI Bridge Conversion Performance Tests
# =============================================================================


def test_agui_bridge_conversion() -> None:
    """Benchmark converting 100 SkillCommand to AG-UI Tool.

    Verifies that AGUISkillBridge can convert 100 skill commands
    to AG-UI Tool format in reasonable time.
    """
    bridge = AGUISkillBridge()
    commands = [_create_skill_command(f"skill-{i}") for i in range(100)]

    start = time.perf_counter()
    for i, cmd in enumerate(commands):
        bridge.handle_change(f"skill-{i}", cmd)
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    tools = bridge.get_tools()
    assert len(tools) == 100
    assert duration_ms < BRIDGE_CONVERSION_THRESHOLD_MS, (
        f"AG-UI bridge conversion of 100 commands took {duration_ms:.2f}ms, "
        f"expected <{BRIDGE_CONVERSION_THRESHOLD_MS}ms"
    )


@pytest.mark.asyncio
async def test_agui_bridge_bulk_conversion(tmp_path: str) -> None:
    """Benchmark bulk conversion through registry to AG-UI format."""
    skills_base_dir = to_upath(tmp_path) / "skills"
    skills_base_dir.mkdir(parents=True, exist_ok=True)

    # Create 100 real skills
    for i in range(100):
        _create_real_skill(f"skill-{i}", skills_base_dir)

    skills_registry = SkillsRegistry(skills_dirs=[skills_base_dir])
    command_registry = SkillCommandRegistry(skills_registry=skills_registry)
    bridge = AGUISkillBridge()

    command_registry.on_command_change(bridge.handle_change)

    await skills_registry.discover_skills()

    start = time.perf_counter()
    await command_registry.initialize()
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    assert len(bridge.get_tools()) == 100
    assert duration_ms < BRIDGE_CONVERSION_THRESHOLD_MS * 2


# =============================================================================
# OpenCode Bridge Conversion Performance Tests
# =============================================================================


def test_opencode_bridge_conversion() -> None:
    """Benchmark converting 100 SkillCommand to slashed Command.

    Verifies that OpenCodeSkillBridge can convert 100 skill commands
    to slashed Command format in reasonable time.
    """
    bridge = OpenCodeSkillBridge()
    commands = [_create_skill_command(f"skill-{i}") for i in range(100)]

    start = time.perf_counter()
    for i, cmd in enumerate(commands):
        bridge.handle_change(f"skill-{i}", cmd)
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    commands_list = bridge.get_commands()
    assert len(commands_list) == 100
    assert duration_ms < BRIDGE_CONVERSION_THRESHOLD_MS, (
        f"OpenCode bridge conversion of 100 commands took {duration_ms:.2f}ms, "
        f"expected <{BRIDGE_CONVERSION_THRESHOLD_MS}ms"
    )


def test_opencode_create_skill_command_performance() -> None:
    """Benchmark create_skill_command factory function.

    Tests the raw performance of creating slashed commands from
    SkillCommand instances.
    """
    commands = [_create_skill_command(f"skill-{i}") for i in range(100)]

    start = time.perf_counter()
    for cmd in commands:
        create_skill_command(cmd)
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    assert duration_ms < BRIDGE_CONVERSION_THRESHOLD_MS, (
        f"Creating 100 slashed commands took {duration_ms:.2f}ms, "
        f"expected <{BRIDGE_CONVERSION_THRESHOLD_MS}ms"
    )


@pytest.mark.asyncio
async def test_opencode_bridge_bulk_conversion(tmp_path: str) -> None:
    """Benchmark bulk conversion through registry to OpenCode format."""
    skills_base_dir = to_upath(tmp_path) / "skills"
    skills_base_dir.mkdir(parents=True, exist_ok=True)

    # Create 100 real skills
    for i in range(100):
        _create_real_skill(f"skill-{i}", skills_base_dir)

    skills_registry = SkillsRegistry(skills_dirs=[skills_base_dir])
    command_registry = SkillCommandRegistry(skills_registry=skills_registry)
    bridge = OpenCodeSkillBridge()

    command_registry.on_command_change(bridge.handle_change)

    await skills_registry.discover_skills()

    start = time.perf_counter()
    await command_registry.initialize()
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    assert len(bridge.get_commands()) == 100
    assert duration_ms < BRIDGE_CONVERSION_THRESHOLD_MS * 2


# =============================================================================
# Concurrent Protocol Conversion Tests
# =============================================================================


def test_all_bridges_concurrent_conversion() -> None:
    """Benchmark all three bridges converting the same 100 commands.

    Verifies that ACP, AG-UI, and OpenCode bridges can handle
    concurrent conversion efficiently.
    """
    registry = SkillCommandRegistry()
    acp_bridge = ACPSkillBridge()
    agui_bridge = AGUISkillBridge()
    opencode_bridge = OpenCodeSkillBridge()

    # Subscribe all bridges
    registry.on_command_change(acp_bridge.handle_change)
    registry.on_command_change(agui_bridge.handle_change)
    registry.on_command_change(opencode_bridge.handle_change)

    commands = [_create_skill_command(f"skill-{i}") for i in range(100)]

    start = time.perf_counter()
    for i, cmd in enumerate(commands):
        registry.register(f"skill-{i}", cmd)
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    # Verify all bridges have all commands
    assert len(acp_bridge.get_available_commands()) == 100
    assert len(agui_bridge.get_tools()) == 100
    assert len(opencode_bridge.get_commands()) == 100

    # Should complete within reasonable time even with 3 handlers
    assert duration_ms < BRIDGE_CONVERSION_THRESHOLD_MS * 2, (
        f"Concurrent conversion to all 3 protocols took {duration_ms:.2f}ms, "
        f"expected <{BRIDGE_CONVERSION_THRESHOLD_MS * 2}ms"
    )


def test_bridge_conversion_throughput() -> None:
    """Measure conversion throughput (commands per second).

    Provides a baseline metric for bridge conversion performance.
    """
    bridge = ACPSkillBridge()
    num_commands = 1000
    commands = [_create_skill_command(f"skill-{i}") for i in range(num_commands)]

    start = time.perf_counter()
    for i, cmd in enumerate(commands):
        bridge.handle_change(f"skill-{i}", cmd)
    end = time.perf_counter()

    duration_sec = end - start
    commands_per_sec = num_commands / duration_sec

    # Should handle at least 2000 commands per second in typical environment
    assert commands_per_sec > 2000, (
        f"Conversion throughput: {commands_per_sec:.0f} commands/sec, expected >2000 commands/sec"
    )
