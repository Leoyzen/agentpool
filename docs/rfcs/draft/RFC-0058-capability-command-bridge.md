---
rfc_id: RFC-0058
title: "Capability-Command Bridge: Connecting CommandResource Capabilities to Protocol Servers"
status: REVIEW
author: Sisyphus
reviewers:
  - name: Oracle
    status: completed
  - name: Metis
    status: completed
  - name: Momus
    status: completed
created: 2026-07-22
last_updated: 2026-07-22
decision_date:
related_rfcs:
  - RFC-0016 (Unified Skill-to-Slash Command Architecture)
  - RFC-0032 (ACP Slash Commands Protocol Compliance)
  - RFC-0051 (Extension Source Architecture)
related_openspec:
  - openspec/changes/capability-command-bridge/
---

# RFC-0058: Capability-Command Bridge

## Overview

AgentPool's v1 capability architecture introduced `CommandResource` as a mixin protocol for capabilities to expose slash commands, and `ExtensionRegistry.get_command_resources()` to query them. However, protocol servers (ACP, OpenCode) bypass this infrastructure entirely — they read commands directly from `SkillsRegistry` and YAML manifest config. Additionally, `CommandResource` is discovery-only: `CommandEntry` carries metadata but no execution handler, so even if discovered, a custom capability's commands cannot be invoked.

This RFC proposes a `CommandBridge` component that connects `ExtensionRegistry` command resources to protocol server command stores, providing both discovery and execution paths. The change is additive — existing skill commands and MCP prompt commands continue to work unchanged, while any capability implementing `CommandResource` gains automatic command registration across all command-capable protocol servers.

## Table of Contents

- [Background & Context](#background--context)
- [Problem Statement](#problem-statement)
- [Goals & Non-Goals](#goals--non-goals)
- [Evaluation Criteria](#evaluation-criteria)
- [Options Analysis](#options-analysis)
- [Recommendation](#recommendation)
- [Technical Design](#technical-design)
- [Security Considerations](#security-considerations)
- [Implementation Plan](#implementation-plan)
- [Open Questions](#open-questions)
- [Decision Record](#decision-record)
- [References](#references)

---

## Background & Context

### Current State

AgentPool's command system has three layers that are partially connected:

**Layer 1 — Capability Protocol (`src/agentpool/capabilities/resource_protocols.py`):**
- `CommandResource` is a `@runtime_checkable` Protocol with `list_commands() -> Sequence[CommandEntry]` and `get_command(name) -> CommandEntry | None`
- `CommandEntry` is a dataclass: `name`, `description`, `skill_uri`, `source`
- Two capabilities implement `CommandResource`: `SkillManagerCap` (maps skills to commands) and `McpServerCap` (maps MCP prompts to commands)

**Layer 2 — Registry (`src/agentpool/capabilities/extension_registry.py`):**
- `ExtensionRegistry` manages capabilities at 4 scope levels: POOL → SESSION → AGENT → TURN
- `get_command_resources(scope)` returns all visible capabilities implementing `CommandResource`
- This method has **no callers outside of tests**

**Layer 3 — Protocol Servers:**
- **ACP server** (`session_agent_mgmt.py`): `_register_skill_commands()` reads from `host_context.skills_registry.list_skills()` directly, creates `SkillCommand` objects, feeds them to `ACPSkillBridge`. `_register_manifest_commands()` reads YAML config. Neither calls `get_command_resources()`.
- **OpenCode server** (`skill_bridge.py`): `OpenCodeSkillBridge` wraps `SkillCommand` → `SlashedCommand` via `create_skill_command()`. `GET /command` endpoint lists from MCP prompts + `skill_bridge`. Does not call `get_command_resources()`.
- **AG-UI server**: Treats skills as tools (not slash commands) via `AGUISkillBridge`. Orthogonal to this RFC.
- **MCP, OpenAI API, A2A servers**: No slash command support. Out of scope.

### Historical Context

- **RFC-0016** established the skill-to-slash-command architecture, creating `SkillCommand`, `create_skill_command()`, and per-protocol bridges (`ACPSkillBridge`, `OpenCodeSkillBridge`).
- **RFC-0032** aligned ACP slash command advertisement with the ACP spec (moved from `initialize` to `session/update`).
- **M3 capability migration** replaced `ResourceProvider` hierarchy with `AbstractCapability` and mixin protocols (`CommandResource`, `SkillResource`, `McpResource`). `ExtensionRegistry` was introduced with `get_command_resources()`, but protocol servers were never rewired to use it.
- The gap was identified during investigation of why a custom capability implementing `CommandResource` has its commands invisible to all protocol servers.

### Glossary

| Term | Definition |
|------|------------|
| `CommandResource` | `@runtime_checkable` Protocol for capabilities that produce slash commands |
| `CommandEntry` | Dataclass carrying command metadata (`name`, `description`, `skill_uri`, `source`) |
| `ExtensionRegistry` | 4-level scope registry for capabilities (POOL → SESSION → AGENT → TURN) |
| `CommandBridge` | Proposed new class connecting `ExtensionRegistry` to protocol server `CommandStore`s |
| `CommandStore` | Per-session store of registered slash commands in protocol servers |
| `SlashedCommand` | Protocol-agnostic command abstraction in `src/agentpool/commands/base.py` |
| `SkillCommand` | `SlashedCommand` subclass wrapping a `Skill` (defined in `skills/command.py`) |
| `ACPSkillBridge` | ACP-specific bridge converting `SkillCommand` → ACP `AvailableCommand` |
| `OpenCodeSkillBridge` | OpenCode-specific bridge converting `SkillCommand` → `SlashedCommand` |
| `create_skill_command()` | Shared function in `skill_bridge.py` creating `SlashedCommand` from `SkillCommand` |
| `ChangeEvent` | Dataclass emitted by capabilities to signal resource changes (`kind: str`) |

---

## Problem Statement

### The Problem

A custom capability implementing `CommandResource` has its commands invisible to all protocol servers. Two specific gaps cause this:

1. **Discovery gap**: Protocol servers never call `ExtensionRegistry.get_command_resources()`. They read from `SkillsRegistry` and YAML manifest config only.
2. **Execution gap**: `CommandResource` is discovery-only. `CommandEntry` carries metadata but no execution handler. Even if discovered, there is no way to invoke a command back to the producing capability without protocol-specific wiring per capability type.

### Evidence

- `ExtensionRegistry.get_command_resources()` exists (line ~340 in `extension_registry.py`) but has zero callers in production code paths (only in tests).
- `_register_skill_commands()` in `session_agent_mgmt.py` calls `host_context.skills_registry.list_skills()` directly, bypassing the capability system.
- `OpenCodeSkillBridge` in `skill_bridge.py` wraps `SkillCommand` objects only — no mechanism for non-skill `CommandEntry` objects.
- `CommandEntry` in `resource_protocols.py` has fields `name`, `description`, `skill_uri`, `source` — no `handler` or callable field.

### Impact of Inaction

- **Cost**: Every new capability type that wants to expose slash commands requires manual wiring in each protocol server (ACP, OpenCode). This is O(servers × capability types) wiring effort.
- **Risk**: The `CommandResource` protocol and `ExtensionRegistry.get_command_resources()` are dead code — they exist but are never exercised in production, creating a false impression that the system supports capability-driven commands.
- **Opportunity**: Custom capabilities (e.g., a hypothetical `JiraCapability` exposing `/create-ticket`) cannot participate in the slash command system without bypassing the capability architecture entirely.

---

## Goals & Non-Goals

### Goals (In Scope)

1. Enable any capability implementing `CommandResource` to have its commands automatically discovered and registered by all command-capable protocol servers (ACP, OpenCode)
2. Provide an execution path from command invocation back to the producing capability, without protocol-specific wiring per capability type
3. Maintain backward compatibility — existing skill commands and MCP prompt commands continue to work unchanged
4. Keep the change minimal — connect existing pipes, don't create new abstractions

### Non-Goals (Out of Scope)

1. Adding command support to protocols that don't have commands (MCP, OpenAI API, A2A)
2. AG-UI protocol integration — AG-UI treats skills as tools (not slash commands) via `AGUISkillBridge`; the `CommandBridge` pattern is for slash command protocols only
3. Modifying `AbstractCapability` or `AbstractToolset` upstream in pydantic-ai
4. Unifying all command types into a single class hierarchy — static YAML commands, skill commands, and capability commands coexist
5. Modifying `create_skill_command()` in `skill_bridge.py` — this function is shared between ACP and OpenCode servers and MUST NOT be modified

### Success Criteria

- [ ] A custom capability implementing `CommandResource` (not `SkillManagerCap`, not `McpServerCap`) has its commands visible in ACP `AvailableCommandsUpdate`
- [ ] A custom capability's commands can be invoked by an ACP client via `CommandBridge.execute()`
- [ ] A custom capability's commands appear in the OpenCode `/command` endpoint response
- [ ] Existing skill commands and MCP prompt commands continue to work without behavior change
- [ ] `ExtensionRegistry.get_command_resources()` is called in production code paths (not just tests)
- [ ] No breaking changes — `CommandEntry` consumers that ignore the new `handler` field are unaffected

---

## Evaluation Criteria

The following criteria are used to objectively evaluate each option:

| Criterion | Weight | Description | Minimum Threshold |
|-----------|--------|-------------|-------------------|
| Discovery completeness | High | All `CommandResource` capabilities are discovered, not just skills | Must cover custom capabilities |
| Execution path | High | Commands can be invoked back to the producing capability | Must work without per-capability wiring |
| Backward compatibility | High | Existing skill/MCP prompt commands work unchanged | Zero behavior change for existing users |
| Implementation effort | Medium | LOC changed, files modified, new modules | <500 LOC total change |
| Protocol server coupling | Medium | Protocol servers should not duplicate discovery logic | Single integration point |
| Type safety | Medium | No `Any` types, no `@ts-ignore`-equivalent patterns | Full type hints, mypy --strict clean |
| Testability | Medium | New behavior is unit-testable in isolation | Unit tests for all new public methods |

---

## Options Analysis

### Option 1: `CommandBridge` with `handler` on `CommandEntry`

**Description**

Add an optional `handler: Callable[[str, AgentContext], Awaitable[str]] | None` field to `CommandEntry`. Create a new `CommandBridge` class that sits between `ExtensionRegistry` and protocol server `CommandStore`s. `CommandBridge` discovers commands by calling `ExtensionRegistry.get_command_resources(scope)` and aggregating `list_commands()` from all results. Execution routes through `CommandBridge.execute(name, input, ctx)`, which looks up the cached `CommandEntry` and invokes its `handler`. Protocol servers construct a per-session `CommandBridge` and use it for both discovery and execution.

**Advantages**

- Single integration point: protocol servers delegate to `CommandBridge` instead of each having their own discovery logic
- Per-command execution: `handler` on `CommandEntry` allows different commands from the same capability to have different execution paths
- `CommandResource` protocol stays pure: discovery (`list_commands`/`get_command`) is unchanged, execution is added via the `CommandEntry` data model
- Backward compatible: `handler` defaults to `None`; existing `CommandEntry` consumers ignore it
- `compare=False` on `handler` field preserves existing `CommandEntry` equality semantics
- Cached name→entry index provides O(1) execution lookup after initial discovery

**Disadvantages**

- `CommandEntry` gains a callable field, making it a hybrid data/behavior object (though `compare=False` mitigates equality issues)
- `handler` is not serializable — `CommandEntry` cannot be sent over the wire with `handler` attached (protocol servers must convert to their own serializable types before transmission)
- Protocol servers need to be rewired to construct `CommandBridge` and route execution through it
- Per-session `CommandBridge` lifecycle adds a small memory overhead (one `CommandBridge` instance per active session)

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Discovery completeness | Excellent | Queries all `CommandResource` capabilities via `ExtensionRegistry` |
| Execution path | Excellent | `handler` on `CommandEntry` provides direct execution path |
| Backward compatibility | Excellent | Additive only — `handler` defaults to `None`, no existing behavior change |
| Implementation effort | Good | ~200-300 LOC new code, ~100 LOC refactored across 4-5 files |
| Protocol server coupling | Excellent | Single `CommandBridge` integration point per protocol server |
| Type safety | Good | `Callable` type with full signature, `AgentContext` typed, no `Any` |
| Testability | Excellent | `CommandBridge` is a standalone class with clear inputs/outputs |

**Effort Estimate**

- Complexity: Medium
- Resources: 1 engineer, 2-3 days
- Dependencies: None — all building blocks (`ExtensionRegistry`, `CommandResource`, `CommandEntry`) already exist

**Risk Assessment**

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Handler closures from destroyed scopes | Low | Medium | `CommandBridge` is per-session, discarded on session close; next `discover_commands()` rebuilds index |
| Command name collisions across capabilities | Medium | Low | First-wins in scope-priority order (TURN → AGENT → SESSION → POOL), log warning |
| `handler` not serializable | Low | Low | `handler` stays in-process; protocol servers convert to serializable types before transmission |
| `McpServerCap` emits `"prompts_changed"` not `"commands_changed"` | Medium | Medium | `CommandBridge.watch_changes()` forwards all three event kinds: `"commands_changed"`, `"skills_changed"`, `"prompts_changed"` |

---

### Option 2: Add `execute_command()` to `CommandResource` Protocol

**Description**

Instead of adding `handler` to `CommandEntry`, add a new `execute_command(name: str, input: str, ctx: AgentContext) -> str` method to the `CommandResource` protocol. Protocol servers call `get_command_resources(scope)`, then for each `CommandResource` capability, call `list_commands()` for discovery and `execute_command(name, input, ctx)` for execution.

**Advantages**

- `CommandEntry` remains a pure data class — no callable field
- Execution is per-capability, matching the discovery pattern (`list_commands` / `get_command` on the capability, `execute_command` also on the capability)
- No new class needed — protocol servers call `CommandResource` methods directly

**Disadvantages**

- Couples discovery and execution: all commands from a single capability share one `execute_command` method, even if different commands need different execution paths
- Forces all `CommandResource` implementations to add `execute_command`, even those that are discovery-only (e.g., a capability that lists commands for display but doesn't support invocation)
- Protocol servers still need to iterate all `CommandResource` capabilities and match commands to capabilities for execution — no O(1) lookup without building a separate index
- No central deduplication or collision resolution — each protocol server must implement its own scope-priority logic
- Still requires rewiring all protocol servers, but without a centralized component to handle the wiring

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Discovery completeness | Good | Queries all `CommandResource` capabilities |
| Execution path | Fair | Per-capability, not per-command; forces all implementors to add `execute_command` |
| Backward compatibility | Fair | Adding method to Protocol is a breaking change for existing implementations |
| Implementation effort | Fair | No new class, but more per-server wiring code |
| Protocol server coupling | Fair | Each server duplicates discovery + execution routing logic |
| Type safety | Good | Full type hints possible |
| Testability | Fair | No standalone component to unit test; logic spread across servers |

**Effort Estimate**

- Complexity: Medium
- Resources: 1 engineer, 3-4 days (more per-server wiring)
- Dependencies: None

**Risk Assessment**

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Breaking existing `CommandResource` implementations | High | Medium | All implementors must add `execute_command` |
| Discovery-only capabilities forced to implement execution | Medium | Low | Can raise `NotImplementedError`, but violates interface contract |
| No centralized collision resolution | Medium | Low | Each server must implement its own |

---

### Option 3: Protocol Servers Call `get_command_resources()` Directly

**Description**

No new components. Each protocol server (ACP, OpenCode) is modified to call `ExtensionRegistry.get_command_resources(scope)`, iterate all `CommandResource` capabilities, call `list_commands()` on each, and register the resulting `CommandEntry` objects. For execution, each protocol server maintains a mapping from command name to producing capability and calls `get_command(name)` on the capability to retrieve the entry, then has protocol-specific execution logic.

**Advantages**

- No new classes or fields — minimal new abstractions
- Each protocol server has full control over its command registration flow
- `CommandEntry` remains unchanged

**Disadvantages**

- Duplicates discovery + execution routing logic across 3+ protocol servers
- No execution path: `CommandEntry` still has no `handler`, so execution requires protocol-specific wiring per capability type (skill → load skill, MCP prompt → call `get_prompt`, custom → ???)
- Does not solve the execution gap at all — only partially solves the discovery gap
- Each server must independently handle collision resolution, scope priority, and change watching
- Violates DRY principle — same logic repeated in each server

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Discovery completeness | Good | Queries all `CommandResource` capabilities |
| Execution path | Poor | No execution path added; requires per-capability-type wiring per server |
| Backward compatibility | Good | No changes to `CommandEntry` or `CommandResource` |
| Implementation effort | Fair | No new class, but more duplicated code per server |
| Protocol server coupling | Poor | Each server duplicates all logic |
| Type safety | Good | No new types needed |
| Testability | Poor | Logic spread across servers, no centralized component |

**Effort Estimate**

- Complexity: Medium-High (duplication across servers)
- Resources: 1 engineer, 4-5 days
- Dependencies: None

**Risk Assessment**

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Execution gap not solved | High | High | Would require a follow-up RFC for execution |
| Logic duplication across servers | High | Medium | Extracted helpers, but still duplicated |
| Inconsistent behavior across protocols | Medium | Medium | Each server implements its own collision resolution |

---

### Options Comparison Summary

| Criterion | Option 1: CommandBridge + handler | Option 2: execute_command on Protocol | Option 3: Direct get_command_resources() |
|-----------|-----------------------------------|---------------------------------------|------------------------------------------|
| Discovery completeness | Excellent | Good | Good |
| Execution path | Excellent | Fair | Poor |
| Backward compatibility | Excellent | Fair | Good |
| Implementation effort | Good (2-3 days) | Fair (3-4 days) | Fair (4-5 days) |
| Protocol server coupling | Excellent | Fair | Poor |
| Type safety | Good | Good | Good |
| Testability | Excellent | Fair | Poor |
| **Overall** | **Recommended** | **Not recommended** | **Not recommended** |

---

## Recommendation

### Recommended Option

**Option 1: `CommandBridge` with `handler` on `CommandEntry`**

### Justification

Option 1 scores highest across all evaluation criteria. It is the only option that fully solves both the discovery gap and the execution gap while maintaining backward compatibility. The `CommandBridge` provides a single, testable integration point that protocol servers delegate to, eliminating the duplicated discovery logic that Options 2 and 3 would perpetuate.

The `handler` field on `CommandEntry` keeps execution per-command rather than per-capability, allowing a single capability to produce commands with different execution paths (e.g., `SkillManagerCap` produces both local skill commands and pass-through MCP prompt commands with different handlers). This is not possible with Option 2's per-capability `execute_command` method.

Option 3 is ruled out because it does not solve the execution gap at all — it only partially addresses discovery and would require a follow-up RFC for execution, adding unnecessary latency.

### Accepted Trade-offs

1. `CommandEntry` becomes a hybrid data/behavior object: Acceptable because `compare=False` on `handler` preserves equality semantics, and `handler` is never serialized (protocol servers convert to their own types before transmission).
2. Per-session `CommandBridge` memory overhead: Acceptable because one additional lightweight object per active session is negligible.
3. Protocol servers must be rewired: Acceptable because the rewiring is additive (existing skill command paths remain as fallback), and the `CommandBridge` API is simpler than the current direct-access pattern.

### Conditions

- `create_skill_command()` in `skill_bridge.py` MUST NOT be modified — a separate `entry_to_slashed_command()` converter handles `CommandEntry`-based commands
- `CommandBridge` is per-session (Scope(SESSION)), not pool-level — this ensures handler closures reference valid session state
- AG-UI is explicitly excluded — its tool-based approach is orthogonal to slash commands

---

## Technical Design

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ExtensionRegistry                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
│  │ SkillMgr │  │ McpServer│  │ Custom   │  │  ...     │            │
│  │ Cap      │  │ Cap      │  │ Cap      │  │          │            │
│  │ (CmdRes) │  │ (CmdRes) │  │ (CmdRes) │  │          │            │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────────┘            │
│       │              │              │                                │
│       └──────────────┴──────────────┘                                │
│                      │ get_command_resources(scope)                 │
└──────────────────────┼──────────────────────────────────────────────┘
                       │
              ┌────────▼────────┐
              │  CommandBridge  │  (per-session, Scope(SESSION))
              │                 │
              │  discover_cmds()│──► list[CommandEntry] (with handler)
              │  execute()      │──► str (calls entry.handler)
              │  watch_changes()│──► AsyncIterator[ChangeEvent]
              │  entry_to_slash │──► SlashedCommand | None
              └────────┬────────┘
                       │
          ┌────────────┼────────────┐
          │            │            │
  ┌───────▼──────┐ ┌──▼─────────┐ ┌▼──────────────┐
  │ ACP Server   │ │ OpenCode   │ │ Other servers │
  │              │ │ Server     │ │ (future)      │
  │ ACPSession   │ │ OpenCode   │ │               │
  │   .bridge    │ │ CmdBridge  │ │               │
  └──────────────┘ └────────────┘ └───────────────┘
```

### Key Components

#### `CommandEntry` (modified)

```python
@dataclass(frozen=True)
class CommandEntry:
    name: str
    description: str
    skill_uri: str | None
    source: str
    handler: Callable[[str, AgentContext], Awaitable[str]] | None = field(
        default=None, compare=False
    )
```

- `handler`: Optional async callable taking `(input: str, ctx: AgentContext) -> str`
- `compare=False`: Excludes `handler` from equality/hash comparisons, preserving existing `CommandEntry` comparison semantics
- `AgentContext`: The frozen dataclass defined in `src/agentpool/capabilities/agent_context.py`

#### `CommandBridge` (new)

```python
class CommandBridge:
    def __init__(self, extension_registry: ExtensionRegistry, scope: Scope) -> None: ...

    def discover_commands(self) -> list[CommandEntry]:
        """Query all CommandResource capabilities, aggregate commands,
        de-duplicate by name (TURN → AGENT → SESSION → POOL priority),
        build and cache a dict[str, CommandEntry] index for O(1) lookup."""

    async def execute(self, name: str, input: str, ctx: AgentContext) -> str:
        """Look up CommandEntry from cached index, invoke entry.handler.
        Raises CommandNotFoundError if not found.
        Raises CommandNotExecutableError if handler is None.
        Exceptions from handler() propagate without wrapping."""

    async def watch_changes(self) -> AsyncIterator[ChangeEvent]:
        """Wrap extension_registry.merge_change_streams(), filtering for
        'commands_changed', 'skills_changed', 'prompts_changed' events.
        Returns empty iterator when merge_change_streams returns None."""

    @staticmethod
    def entry_to_slashed_command(entry: CommandEntry) -> SlashedCommand | None:
        """Convert CommandEntry to SlashedCommand. Returns None for
        display-only entries (handler is None). MUST NOT modify
        create_skill_command() in skill_bridge.py."""
```

#### `CommandNotExecutableError`, `CommandNotFoundError` (new)

```python
class CommandNotFoundError(Exception):
    """Raised when CommandBridge.execute() cannot find a command by name."""

class CommandNotExecutableError(Exception):
    """Raised when a CommandEntry has no handler (display-only command)."""
```

#### Protocol Server Integration

**ACP Server:**
- `ACPSession` constructs a per-session `CommandBridge` from the session's `ExtensionRegistry` with `Scope(level=ScopeLevel.SESSION, session_id=...)`
- `_register_skill_commands()` uses `CommandBridge.discover_commands()` instead of reading `skills_registry.list_skills()` directly
- `_watch_skill_changes()` consumes `CommandBridge.watch_changes()` for all three event kinds
- Command execution routes through `CommandBridge.execute()` with fallback to existing manifest command path on `CommandNotFoundError`
- `ACPSkillBridge` retains `SkillCommand` → `SlashedCommand` conversion via unchanged `create_skill_command()`, delegates discovery to `CommandBridge`

**OpenCode Server:**
- New `OpenCodeCommandBridge` class wraps `CommandBridge` for OpenCode-specific command conversion (NOT modifying `OpenCodeSkillBridge`)
- `GET /command` endpoint includes commands from `CommandBridge.discover_commands()` alongside existing commands
- Execution routes through `CommandBridge.execute()` with fallback to existing skill command path on `CommandNotFoundError`
- Change watcher consumes `CommandBridge.watch_changes()` for command list rebuilds

### Data Model

```python
# CommandEntry with handler (modified)
@dataclass(frozen=True)
class CommandEntry:
    name: str
    description: str
    skill_uri: str | None
    source: str
    handler: Callable[[str, AgentContext], Awaitable[str]] | None = field(
        default=None, compare=False
    )

# New exceptions
class CommandNotFoundError(Exception): ...
class CommandNotExecutableError(Exception): ...

# ChangeEvent kind additions
# "commands_changed" added to ChangeKind Literal (kind field is already str)
```

### Change Event Flow

```
SkillManagerCap  ──► ChangeEvent(kind="skills_changed")
McpServerCap     ──► ChangeEvent(kind="prompts_changed")
CustomCapability ──► ChangeEvent(kind="commands_changed")
                        │
                        ▼
              ExtensionRegistry.merge_change_streams(scope)
                        │
                        ▼
              CommandBridge.watch_changes()
              (filters for 3 event kinds)
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
     ACP server rebuilds   OpenCode server rebuilds
     command list           /command response
```

### Collision Resolution

When multiple capabilities produce commands with the same name, `CommandBridge.discover_commands()` resolves duplicates by keeping the first occurrence in scope-priority order:

```
TURN (most specific) → AGENT → SESSION → POOL (least specific)
```

A warning is logged for each duplicate encountered. This ensures that session-scoped capabilities override pool-scoped ones, and turn-scoped capabilities override all others.

---

## Security Considerations

### Threat Analysis

| Threat | Impact | Likelihood | Mitigation |
|--------|--------|------------|------------|
| Malicious capability registers harmful slash commands | Medium | Low | Capabilities are registered via `ExtensionRegistry` which is controlled by pool configuration; untrusted capabilities cannot self-register |
| Handler callable executes with elevated privileges | Medium | Low | `handler` receives `AgentContext` which carries the same permissions as the agent; no privilege escalation |
| Command name spoofing (custom capability overrides existing command) | Low | Medium | Scope-priority resolution ensures most specific scope wins; warning logged on collision |
| Stale handler references after session destruction | Low | Low | `CommandBridge` is per-session and discarded on session close; next `discover_commands()` rebuilds index |

### Security Measures

- [x] `CommandBridge` is constructed with a specific `Scope` — capabilities outside the scope are not visible
- [x] `handler` receives `AgentContext` — no direct access to `AgentPool` or `ExtensionRegistry`
- [x] Collision resolution logs warnings — operators can detect name spoofing attempts
- [x] Protocol servers convert `CommandEntry` to their own serializable types before transmission — `handler` never leaves the process

### Compliance

No regulatory or compliance requirements are affected. All changes are internal to the agent orchestration framework.

---

## Implementation Plan

### Phases

#### Phase 1: Core Data Model Changes

- **Scope**: Add `handler` field to `CommandEntry`, new exceptions, `ChangeKind` update
- **Deliverables**: Modified `resource_protocols.py`, new `command_bridge.py` (exceptions only), updated `change_event.py`
- **Dependencies**: None

#### Phase 2: CommandBridge Implementation

- **Scope**: Full `CommandBridge` class with `discover_commands()`, `execute()`, `watch_changes()`, `entry_to_slashed_command()`
- **Deliverables**: Complete `command_bridge.py` (~100-150 LOC), unit tests
- **Dependencies**: Phase 1

#### Phase 3: Update Existing CommandResource Implementations

- **Scope**: `SkillManagerCap.list_commands()` and `McpServerCap.list_commands()` populate `handler`
- **Deliverables**: Modified `skill_manager_cap.py`, `mcp_server_cap.py`, integration tests
- **Dependencies**: Phase 1

#### Phase 4: ACP Server Integration

- **Scope**: Rewire ACP server to use `CommandBridge` for discovery, execution, and change watching
- **Deliverables**: Modified `session_agent_mgmt.py`, `ACPSkillBridge` updated, integration tests
- **Dependencies**: Phases 2, 3

#### Phase 5: OpenCode Server Integration

- **Scope**: Create `OpenCodeCommandBridge`, update `GET /command` endpoint, execution routing, change watcher
- **Deliverables**: New `OpenCodeCommandBridge` class, modified `agent_routes.py`, `server.py`, integration tests
- **Dependencies**: Phases 2, 3

#### Phase 6: Documentation and Validation

- **Scope**: Docstrings, example capability, `AGENTS.md` update, full test suite, lint, type check
- **Deliverables**: Documentation, example, validation results
- **Dependencies**: Phases 4, 5

### Milestones

| Milestone | Description | Target | Status |
|-----------|-------------|--------|--------|
| M1: Core types ready | `CommandEntry.handler`, exceptions, `ChangeKind` | Day 1 | Not Started |
| M2: CommandBridge complete | Full class with unit tests | Day 2 | Not Started |
| M3: Existing capabilities updated | `SkillManagerCap`, `McpServerCap` | Day 2 | Not Started |
| M4: ACP server integrated | End-to-end ACP tests pass | Day 3 | Not Started |
| M5: OpenCode server integrated | End-to-end OpenCode tests pass | Day 3 | Not Started |
| M6: Validation complete | Full suite + lint + type check | Day 3 | Not Started |

### Rollback Strategy

All changes are additive. Rollback procedure:

1. Revert protocol server wiring (ACP: `session_agent_mgmt.py`, OpenCode: `agent_routes.py`, `server.py`) to direct `SkillsRegistry` access
2. Remove `CommandBridge` class and `command_bridge.py`
3. Remove `handler` field from `CommandEntry` (or leave as unused `None` default — no breaking change)
4. Revert `SkillManagerCap` and `McpServerCap` `list_commands()` to not populate `handler`

No data migration or state cleanup is required — `CommandBridge` is stateless (rebuilt on each `discover_commands()` call).

---

## Open Questions

1. **Should `ExtensionRegistry.get_command_resources(scope)` guarantee deterministic ordering by scope specificity?**

   - Context: `CommandBridge.discover_commands()` relies on scope-priority ordering for collision resolution. If `get_command_resources()` returns capabilities in non-deterministic order, the collision resolution behavior is undefined.
   - Owner: Implementation engineer (verify during Phase 2, Task 7.5)
   - Status: Open — needs code verification of current `ExtensionRegistry` ordering behavior

2. **Should `entry_to_slashed_command()` be a static method on `CommandBridge` or a standalone function?**

   - Context: It's currently specified as a static method, but a standalone function in `command_bridge.py` would be equally valid and easier to mock in tests.
   - Owner: Implementation engineer
   - Status: Open — defer to implementation preference

3. **Should the `"commands_changed"` event be emitted by `SkillManagerCap` in addition to `"skills_changed"`?**

   - Context: `SkillManagerCap` currently emits `"skills_changed"`. Adding `"commands_changed"` would be redundant since `CommandBridge.watch_changes()` already forwards `"skills_changed"`. But it would make the event model more consistent.
   - Owner: Implementation engineer
   - Status: Open — leaning towards NOT emitting (avoid redundancy, `CommandBridge` handles the mapping)

---

## Decision Record

> Complete this section after RFC review is concluded.

### Decision

**Status**: PENDING REVIEW

**Date**: TBD

**Approvers**:
- [Reviewer 1]
- [Reviewer 2]

### Decision Summary

[To be completed after review]

### Key Discussion Points

1. The `handler` field on `CommandEntry` vs. `execute_command` on `CommandResource` — the per-command execution model was preferred over per-capability
2. Creating `OpenCodeCommandBridge` vs. generalizing `OpenCodeSkillBridge` — a new class was chosen to avoid breaking the shared `create_skill_command()` function
3. AG-UI exclusion — AG-UI's tool-based approach is orthogonal to slash commands and does not benefit from `CommandBridge`

### Conditions of Approval

- All existing tests pass without modification
- `create_skill_command()` is not modified
- A custom capability end-to-end test demonstrates the full discovery → execution flow

### Dissenting Opinions

None recorded. Oracle, Metis, and Momus reviews converged on the same recommendation after the review-revise loop (3 rounds, 46 tasks refined).

---

## References

### Related Documents

- [OpenSpec Change: capability-command-bridge](../../../openspec/changes/capability-command-bridge/)
  - `proposal.md` — What and why
  - `design.md` — 5 design decisions, 6 risk entries
  - `tasks.md` — 46 tasks across 7 sections
  - `specs/` — 7 spec files (6 modified + 1 new `opencode-server`)
- [RFC-0016: Unified Skill-to-Slash Command Architecture](./RFC-0016-skill-slash-commands.md)
- [RFC-0032: ACP Slash Commands Protocol Compliance](./RFC-0032-acp-slash-commands-session-update.md)
- [RFC-0051: Extension Source Architecture](./RFC-0051-extension-source-architecture.md)

### External Resources

- [Agent Skills Spec](https://github.com/agentskills/agentskills)
- [Agent Client Protocol (ACP) Specification](https://agentclientprotocol.com/)

### Appendix

#### Review History

This RFC was developed through an OpenSpec change that underwent 3 rounds of Oracle + Metis + Momus review:

| Round | Reviewers | Findings | Outcome |
|-------|-----------|----------|---------|
| 1 | Oracle, Metis, Momus | 35 total (7 CRITICAL, 13 MAJOR, 15 MINOR) | NEEDS REVISION |
| 2 | — (fixes applied) | 21 fixes across 10 files | Fixes applied |
| 3 | Oracle (verification) | 4 remaining issues | NOT VERIFIED → fixes applied |
| 4 | Oracle (re-verification) | 0 issues | VERIFIED |
| 5 | Oracle (final check) | 0 issues | VERIFIED (3rd pass) |

Final spec state: 7 spec files, 5 design decisions, 6 risk entries, 46 tasks across 7 sections.

#### File Impact Summary

| File | Change Type | Est. LOC |
|------|-------------|----------|
| `src/agentpool/capabilities/resource_protocols.py` | Modified (add `handler` to `CommandEntry`) | +15 |
| `src/agentpool/capabilities/command_bridge.py` | New (full `CommandBridge` class) | +150 |
| `src/agentpool/capabilities/change_event.py` | Modified (add `ChangeKind` literal) | +3 |
| `src/agentpool/capabilities/skill_manager_cap.py` | Modified (populate `handler` in `list_commands()`) | +20 |
| `src/agentpool/capabilities/mcp_server_cap.py` | Modified (populate `handler` in `list_commands()`) | +15 |
| `src/agentpool_server/acp_server/session_agent_mgmt.py` | Modified (use `CommandBridge`) | +30, -15 |
| `src/agentpool_server/opencode_server/` (new `OpenCodeCommandBridge`) | New + Modified | +40 |
| Tests (new + modified) | New | +200 |
| **Total** | | **~460 LOC** |
