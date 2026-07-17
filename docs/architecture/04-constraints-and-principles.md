# 04: Constraints and Principles

This document lists the hard constraints and design principles that bound the
architecture. Constraints are not negotiable; principles guide trade-offs when
options are otherwise equivalent.

## Hard constraints

### 1. Backward compatibility

All existing `graph:`, `teams:`, and `subagent` mechanisms must continue to work
unchanged. Dynamic Team Mode is an addition, not a replacement.

*Rationale*: AgentPool has production users. Breaking static team semantics would
force a migration before the new model is proven.

### 2. Minimal changes to existing files

A new feature should add files and capabilities rather than modify core
abstractions. A good rule of thumb: a new Capability should be implementable with
≤ 5 lines of change in existing files.

*Rationale*: This reduces regression risk and keeps the codebase reviewable.

### 3. Protocol neutrality

No protocol-specific assumptions (ACP, MCP, OpenCode) may leak into the shared
AgentPool APIs. A feature must work or degrade gracefully across protocols.

*Rationale*: AgentPool is a multi-protocol harness. A protocol-specific feature
in a generic API creates long-term coupling.

### 4. Reuse existing infrastructure

New features must build on `SessionController`, `SessionPool`, `RunHandle`,
`EventBus`, and `AbstractCapability` before introducing new services or base
classes.

*Rationale*: The harness is designed to be composed from these primitives. New
infrastructure layers must be justified by reuse across multiple features.

### 5. LLM-visible coordination

Coordination decisions that the LLM can reasonably make should be exposed as
tools. Hidden framework-level coordination is acceptable only when correctness
or security requires it.

*Rationale*: Inspectability and debuggability. If the LLM cannot see the tools,
operators cannot understand or override the behavior.

### 6. File-based persistence for LLM-readable state

Shared team state (inbox, blackboard, task board) must be file-based so it is:

- Inspectable without special tools.
- Portable across environments.
- Recoverable after a process crash within the TTL window.

*Rationale*: Databases are opaque. Files match the harness philosophy of
inspectability and minimal dependencies.

### 7. Explicit session lifecycle

Team members are sessions. Creating a team member must create a session; closing
a team must close member sessions. There must be no orphaned sessions.

*Rationale*: Session lifecycle is the core responsibility of AgentPool. Team
Mode must not bypass or complicate it.

## Design principles

### 1. Harness, not framework

AgentPool provides primitives and constraints. It does not prescribe a single
orchestration pattern. Static and dynamic teams are both valid policies built on
the same primitives.

### 2. Capabilities are the extension point

New behavior is added by creating capabilities that inject tools, modify prompts,
or register event handlers. This keeps the core small and the extension model
uniform.

### 3. Configuration over code

Where possible, behavior is configured in YAML rather than Python code. This
includes team bounds, eligible agents, protocol templates, and persistence
paths.

### 4. State is explicit and versioned

State that affects behavior (inbox, blackboard, task board) is persisted and
can be observed. There are no hidden in-memory caches that determine outcomes.

### 5. Fail closed, but not silently

If a team operation cannot complete (e.g., a member session cannot be closed),
the LLM must receive a clear error. Silent failures or partial cleanups are not
acceptable.

### 6. Review the foundation before the feature

Team Mode depends on stable session management. If the foundation has debt,
that debt must be addressed before the feature is declared complete. Issue #170
is a prerequisite, not a separate concern.

## Trade-offs we accept

| Decision | Gain | Cost |
|---|---|---|
| LLM-visible tools instead of program-level channels | Flexibility, debuggability | LLM may misuse tools; requires prompt engineering |
| File-based persistence instead of database | Inspectability, no dependency | Slower for large state; requires cleanup/TTL |
| Spawn-time protocol injection instead of per-turn | Simpler, lower latency | Less dynamic role changes |
| `auto_init` for pre-initialized teams | Faster first-turn response | More complex session startup |
| Static and dynamic teams coexist | No forced migration | More concepts to document and maintain |

## Non-constraints (things that are explicitly not limiting us)

| Topic | Why it is not a constraint |
|---|---|
| Database for team state | We explicitly choose files. |
| Cross-team blackboard | Out of scope for v1. |
| ACP agents as team members | Out of scope for v1. |
| Built-in UI for team visualization | Out of scope entirely. |
| Multi-user authentication inside AgentPool | Handled by front-end/protocol layer. |

## How to challenge a design against this list

When reviewing a new architecture proposal or PR, ask:

1. Does it preserve existing `graph:` and `teams:` behavior?
2. Does it modify existing files more than a few lines?
3. Does it leak protocol-specific assumptions into generic APIs?
4. Could it be implemented as a `Capability` instead of a new service?
5. Are coordination decisions visible to the LLM where appropriate?
6. Is shared state persisted in files or another observable mechanism?
7. Does it create or leave orphaned sessions?

If the answer to any of these is "no," the design needs a constraint waiver or a
change.
