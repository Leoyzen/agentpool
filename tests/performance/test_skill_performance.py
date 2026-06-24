"""Performance benchmarks for skill command registration and bridge conversions.

This module provides performance benchmarks for:
- Skill command registration throughput
- Skill discovery performance
- Protocol bridge conversions (ACP, AG-UI, OpenCode)
- URI resolution performance (RFC-0020)
- Skill loading performance (RFC-0020)
- Caching effectiveness (RFC-0020)

Thresholds (adjust based on CI/environment performance):
- Registration: <200ms for 100 commands (typical development environment)
- Discovery: <500ms for 50 skills (includes filesystem I/O)
- Bridge conversion: <100ms for direct conversion of 100 commands

RFC-0020 Performance Criteria:
- Skill discovery: <50ms target (<100ms acceptable)
- URI resolution: <10ms target
- Cached skill loading: <5ms target
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from upathtools import UPath, to_upath

from agentpool.resource_providers.aggregating import AggregatingResourceProvider
from agentpool.resource_providers.local import LocalResourceProvider
from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.registry import SkillsRegistry
from agentpool.skills.skill import Skill
from agentpool.skills.uri_resolver import ResolvedSkillURI, SkillURIResolver
from agentpool_server.acp_server.commands.skill_commands import ACPSkillBridge
from agentpool_server.agui_server.skill_tools import AGUISkillBridge
from agentpool_server.opencode_server.skill_bridge import OpenCodeSkillBridge, create_skill_command


# Thresholds (in milliseconds) - adjusted for realistic CI environment performance
REGISTRATION_THRESHOLD_MS = 200.0  # 100 command registrations with handlers
DISCOVERY_THRESHOLD_MS = 500.0  # 50 skills from filesystem (includes I/O)
BRIDGE_CONVERSION_THRESHOLD_MS = 100.0  # 100 command conversions

# RFC-0020 Performance Thresholds
RFC0020_DISCOVERY_THRESHOLD_MS = 50.0  # <50ms target, <100ms acceptable
RFC0020_DISCOVERY_ACCEPTABLE_MS = 100.0  # Acceptable threshold for slower environments
RFC0020_URI_RESOLUTION_THRESHOLD_MS = 10.0  # <10ms target
RFC0020_CACHED_LOAD_THRESHOLD_MS = 5.0  # <5ms for cached skill loading


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
    await command_registry.initialize(wait=True)
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
    await command_registry.initialize(wait=True)
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
    await command_registry.initialize(wait=True)
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
    await command_registry.initialize(wait=True)
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


# =============================================================================
# RFC-0020 URI Resolution Performance Tests
# =============================================================================


def test_uri_parsing_performance() -> None:
    """Benchmark URI parsing performance.

    Verifies that ResolvedSkillURI.parse() can parse skill:// URIs
    within the RFC-0020 target of <10ms per URI.
    """
    uris = [
        "skill://local/python-expert",
        "skill://local/python-expert/references/guide.md",
        "skill://provider-name/my-skill-name",
        "bare-skill-name",
        "skill://local/my-skill/sub/path/to/file.txt",
    ] * 20  # 100 URIs total

    start = time.perf_counter()
    for uri in uris:
        ResolvedSkillURI.parse(uri)
    end = time.perf_counter()

    duration_ms = (end - start) * 1000
    avg_ms_per_uri = duration_ms / len(uris)

    assert avg_ms_per_uri < RFC0020_URI_RESOLUTION_THRESHOLD_MS, (
        f"URI parsing took {avg_ms_per_uri:.3f}ms per URI, "
        f"expected <{RFC0020_URI_RESOLUTION_THRESHOLD_MS}ms"
    )


@pytest.mark.asyncio
async def test_uri_resolution_performance(tmp_path: str) -> None:
    """Benchmark skill URI resolution performance.

    Verifies that SkillURIResolver can resolve skill URIs to Skill instances
    within the RFC-0020 target of <10ms per resolution (with caching).
    """
    # Create test skills
    skills_base_dir = to_upath(tmp_path) / "skills"
    skills_base_dir.mkdir(parents=True, exist_ok=True)

    for i in range(10):
        _create_real_skill(f"test-skill-{i}", skills_base_dir)

    # Set up provider and resolver
    provider = LocalResourceProvider(
        name="local",
        skills_dirs=[skills_base_dir],
        cache_ttl=60.0,
    )
    resolver = SkillURIResolver()

    async with provider:
        resolver.register_provider("local", provider)

        # Pre-populate cache
        await resolver.resolve("skill://local/test-skill-0")

        # Benchmark cached resolution
        start = time.perf_counter()
        for i in range(100):
            await resolver.resolve(f"skill://local/test-skill-{i % 10}")
        end = time.perf_counter()

    duration_ms = (end - start) * 1000
    avg_ms_per_resolution = duration_ms / 100

    assert avg_ms_per_resolution < RFC0020_URI_RESOLUTION_THRESHOLD_MS, (
        f"URI resolution took {avg_ms_per_resolution:.3f}ms per resolution, "
        f"expected <{RFC0020_URI_RESOLUTION_THRESHOLD_MS}ms"
    )


@pytest.mark.asyncio
async def test_uri_resolution_bare_name_performance(tmp_path: str) -> None:
    """Benchmark bare skill name resolution performance.

    Verifies that resolving bare skill names (without explicit provider)
    meets RFC-0020 performance targets.
    """
    # Create test skills
    skills_base_dir = to_upath(tmp_path) / "skills"
    skills_base_dir.mkdir(parents=True, exist_ok=True)

    for i in range(10):
        _create_real_skill(f"test-skill-{i}", skills_base_dir)

    provider = LocalResourceProvider(
        name="local",
        skills_dirs=[skills_base_dir],
        cache_ttl=60.0,
    )
    resolver = SkillURIResolver()

    async with provider:
        resolver.register_provider("local", provider)

        # Pre-populate provider cache
        await provider.get_skills()

        # Benchmark bare name resolution
        start = time.perf_counter()
        for i in range(100):
            await resolver.resolve(f"test-skill-{i % 10}")
        end = time.perf_counter()

    duration_ms = (end - start) * 1000
    avg_ms_per_resolution = duration_ms / 100

    assert avg_ms_per_resolution < RFC0020_URI_RESOLUTION_THRESHOLD_MS * 2, (
        f"Bare name resolution took {avg_ms_per_resolution:.3f}ms per resolution, "
        f"expected <{RFC0020_URI_RESOLUTION_THRESHOLD_MS * 2}ms"
    )


# =============================================================================
# RFC-0020 Skill Discovery Performance Tests
# =============================================================================


@pytest.mark.asyncio
async def test_skill_discovery_10_skills_rfc0020(tmp_path: str) -> None:
    """Benchmark discovering 10 skills (RFC-0020 target).

    Verifies that SkillsRegistry can discover and parse 10 skills
    within the RFC-0020 target of <50ms (<100ms acceptable).
    """
    skills_base_dir = to_upath(tmp_path) / "skills"
    skills_base_dir.mkdir(parents=True, exist_ok=True)

    # Create 10 skill directories with SKILL.md files
    for i in range(10):
        _create_real_skill(f"test-skill-{i}", skills_base_dir)

    registry = SkillsRegistry(skills_dirs=[skills_base_dir])

    start = time.perf_counter()
    await registry.discover_skills()
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    assert len(registry) == 10
    # Use acceptable threshold for CI environments
    assert duration_ms < RFC0020_DISCOVERY_ACCEPTABLE_MS, (
        f"Discovery of 10 skills took {duration_ms:.2f}ms, "
        f"expected <{RFC0020_DISCOVERY_ACCEPTABLE_MS}ms (RFC-0020: <{RFC0020_DISCOVERY_THRESHOLD_MS}ms target)"
    )


@pytest.mark.asyncio
async def test_skill_discovery_50_skills_rfc0020(tmp_path: str) -> None:
    """Benchmark discovering 50 skills (RFC-0020 realistic count).

    Verifies that SkillsRegistry can discover and parse 50 skills
    within reasonable time (includes filesystem I/O).
    """
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
    # For 50 skills with I/O, allow up to 200ms (4x the 10-skill target)
    assert duration_ms < RFC0020_DISCOVERY_ACCEPTABLE_MS * 2, (
        f"Discovery of 50 skills took {duration_ms:.2f}ms, "
        f"expected <{RFC0020_DISCOVERY_ACCEPTABLE_MS * 2}ms"
    )


@pytest.mark.asyncio
async def test_skill_discovery_100_skills_rfc0020(tmp_path: str) -> None:
    """Benchmark discovering 100 skills (RFC-0020 stress test).

    Verifies that SkillsRegistry can handle larger skill counts
    without significant performance degradation.
    """
    skills_base_dir = to_upath(tmp_path) / "skills"
    skills_base_dir.mkdir(parents=True, exist_ok=True)

    # Create 100 skill directories with SKILL.md files
    for i in range(100):
        _create_real_skill(f"test-skill-{i}", skills_base_dir)

    registry = SkillsRegistry(skills_dirs=[skills_base_dir])

    start = time.perf_counter()
    await registry.discover_skills()
    end = time.perf_counter()

    duration_ms = (end - start) * 1000

    assert len(registry) == 100
    # For 100 skills, allow up to 400ms (8x the 10-skill target, linear scaling)
    assert duration_ms < RFC0020_DISCOVERY_ACCEPTABLE_MS * 4, (
        f"Discovery of 100 skills took {duration_ms:.2f}ms, "
        f"expected <{RFC0020_DISCOVERY_ACCEPTABLE_MS * 4}ms"
    )


# =============================================================================
# RFC-0020 Caching Effectiveness Tests
# =============================================================================


@pytest.mark.asyncio
async def test_local_provider_caching_effectiveness(tmp_path: str) -> None:
    """Benchmark caching effectiveness for LocalResourceProvider.

    Verifies that cached skill access is significantly faster than uncached.
    RFC-0020 target: cached access should be <5ms.
    """
    skills_base_dir = to_upath(tmp_path) / "skills"
    skills_base_dir.mkdir(parents=True, exist_ok=True)

    for i in range(20):
        _create_real_skill(f"test-skill-{i}", skills_base_dir)

    provider = LocalResourceProvider(
        name="local",
        skills_dirs=[skills_base_dir],
        cache_ttl=60.0,
    )

    async with provider:
        # First call - populate cache (cold)
        start = time.perf_counter()
        await provider.get_skills()
        cold_duration_ms = (time.perf_counter() - start) * 1000

        # Second call - use cache (warm)
        start = time.perf_counter()
        await provider.get_skills()
        warm_duration_ms = (time.perf_counter() - start) * 1000

        # Benchmark repeated cached access
        start = time.perf_counter()
        for _ in range(100):
            await provider.get_skills()
        cached_100_duration_ms = (time.perf_counter() - start) * 1000
        avg_cached_ms = cached_100_duration_ms / 100

    # Cached should be significantly faster than cold
    speedup = cold_duration_ms / max(warm_duration_ms, 0.001)

    assert warm_duration_ms < cold_duration_ms, (
        f"Cached access ({warm_duration_ms:.3f}ms) should be faster than "
        f"cold access ({cold_duration_ms:.3f}ms)"
    )

    assert avg_cached_ms < RFC0020_CACHED_LOAD_THRESHOLD_MS, (
        f"Average cached access took {avg_cached_ms:.3f}ms, "
        f"expected <{RFC0020_CACHED_LOAD_THRESHOLD_MS}ms"
    )

    # Should have significant speedup (at least 2x)
    assert speedup > 2.0, f"Cache speedup was {speedup:.1f}x, expected >2x improvement"


@pytest.mark.asyncio
async def test_aggregating_provider_caching(tmp_path: str) -> None:
    """Benchmark caching for AggregatingResourceProvider.

    Verifies that aggregation doesn't negate caching benefits.
    """
    skills_base_dir = to_upath(tmp_path) / "skills"
    skills_base_dir.mkdir(parents=True, exist_ok=True)

    for i in range(20):
        _create_real_skill(f"test-skill-{i}", skills_base_dir)

    local_provider = LocalResourceProvider(
        name="local",
        skills_dirs=[skills_base_dir],
        cache_ttl=60.0,
    )

    async with local_provider:
        aggregator = AggregatingResourceProvider(providers=[local_provider])

        # Cold access
        start = time.perf_counter()
        await aggregator.get_skills()
        cold_duration_ms = (time.perf_counter() - start) * 1000

        # Warm access (should use underlying cache)
        start = time.perf_counter()
        await aggregator.get_skills()
        warm_duration_ms = (time.perf_counter() - start) * 1000

    # Cached access should be faster than cold (relaxed: both sub-ms, timing noise dominates)
    assert warm_duration_ms < cold_duration_ms, (
        f"Aggregating provider cached access ({warm_duration_ms:.3f}ms) should be "
        f"faster than cold access ({cold_duration_ms:.3f}ms)"
    )


@pytest.mark.asyncio
async def test_skill_loading_caching(tmp_path: str) -> None:
    """Benchmark skill loading with caching.

    Verifies that loading skill instructions is cached appropriately.
    """
    skills_base_dir = to_upath(tmp_path) / "skills"
    skills_base_dir.mkdir(parents=True, exist_ok=True)

    # Create skill with substantial content
    skill_dir = skills_base_dir / "content-skill"
    skill_dir.mkdir()

    # Create SKILL.md with some content
    skill_content = """---
name: content-skill
description: A skill with content
---

# Content Skill

""" + "\n".join([f"Paragraph {i} with some text content here." for i in range(100)])

    (skill_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")

    provider = LocalResourceProvider(
        name="local",
        skills_dirs=[skills_base_dir],
        cache_ttl=60.0,
    )

    async with provider:
        # First load - parse and cache
        start = time.perf_counter()
        instructions1 = await provider.get_skill_instructions("content-skill")
        cold_duration_ms = (time.perf_counter() - start) * 1000

        # Second load - should use cached Skill object
        start = time.perf_counter()
        instructions2 = await provider.get_skill_instructions("content-skill")
        warm_duration_ms = (time.perf_counter() - start) * 1000

    assert instructions1 == instructions2
    assert warm_duration_ms < cold_duration_ms, (
        f"Cached skill loading ({warm_duration_ms:.3f}ms) should be faster than "
        f"cold loading ({cold_duration_ms:.3f}ms)"
    )


# =============================================================================
# RFC-0020 Multi-Provider Performance Tests
# =============================================================================


@pytest.mark.asyncio
async def test_multiple_providers_resolution(tmp_path: str) -> None:
    """Benchmark resolution with multiple providers.

    Verifies that skill resolution remains performant with multiple providers.
    """
    # Create two skill directories
    dir1 = to_upath(tmp_path) / "skills1"
    dir1.mkdir()
    dir2 = to_upath(tmp_path) / "skills2"
    dir2.mkdir()

    for i in range(10):
        _create_real_skill(f"skill-set1-{i}", dir1)
        _create_real_skill(f"skill-set2-{i}", dir2)

    provider1 = LocalResourceProvider(name="local1", skills_dirs=[dir1])
    provider2 = LocalResourceProvider(name="local2", skills_dirs=[dir2])

    resolver = SkillURIResolver()

    async with provider1, provider2:
        resolver.register_provider("local1", provider1)
        resolver.register_provider("local2", provider2)

        # Pre-populate caches
        await provider1.get_skills()
        await provider2.get_skills()

        # Benchmark resolution across multiple providers
        start = time.perf_counter()
        for i in range(50):
            await resolver.resolve(f"skill-set1-{i % 10}")
            await resolver.resolve(f"skill-set2-{i % 10}")
        end = time.perf_counter()

    duration_ms = (end - start) * 1000
    avg_ms_per_resolution = duration_ms / 100

    assert avg_ms_per_resolution < RFC0020_URI_RESOLUTION_THRESHOLD_MS * 3, (
        f"Multi-provider resolution took {avg_ms_per_resolution:.3f}ms per resolution, "
        f"expected <{RFC0020_URI_RESOLUTION_THRESHOLD_MS * 3}ms"
    )


# =============================================================================
# Performance Characteristics Documentation
# =============================================================================


def test_document_performance_characteristics() -> None:
    """Document expected performance characteristics.

    This test doesn't assert but documents the expected performance
    characteristics of the skill system for reference.
    """
    # These are documentation values, not assertions
    characteristics = {
        "uri_parsing": {
            "target_ms": RFC0020_URI_RESOLUTION_THRESHOLD_MS,
            "description": "Parsing skill:// URIs to ResolvedSkillURI",
            "operations": "per URI parse",
        },
        "uri_resolution_cached": {
            "target_ms": RFC0020_URI_RESOLUTION_THRESHOLD_MS,
            "description": "Resolving skill URI to Skill instance (cached)",
            "operations": "per resolution",
        },
        "skill_discovery_10": {
            "target_ms": RFC0020_DISCOVERY_THRESHOLD_MS,
            "acceptable_ms": RFC0020_DISCOVERY_ACCEPTABLE_MS,
            "description": "Discover 10 skills from filesystem",
            "operations": "per 10 skills",
        },
        "cached_skill_load": {
            "target_ms": RFC0020_CACHED_LOAD_THRESHOLD_MS,
            "description": "Loading skill from cache",
            "operations": "per skill load",
        },
        "command_registration": {
            "target_ms": REGISTRATION_THRESHOLD_MS,
            "description": "Register 100 skill commands",
            "operations": "per 100 commands",
        },
        "bridge_conversion": {
            "target_ms": BRIDGE_CONVERSION_THRESHOLD_MS,
            "description": "Convert 100 skills to protocol format",
            "operations": "per 100 conversions",
        },
    }

    # Print characteristics for documentation
    for name, specs in characteristics.items():
        target = specs.get("target_ms", "N/A")
        acceptable = specs.get("acceptable_ms", target)
        desc = specs["description"]
        ops = specs["operations"]
        print(f"{name}: {desc}")
        print(f"  Target: {target}ms {ops}")
        if acceptable != target:
            print(f"  Acceptable: {acceptable}ms {ops}")

    # Always pass - this is documentation
    assert True
