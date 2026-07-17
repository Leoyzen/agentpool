# 01: Vision and Philosophy

This document defines AgentPool's design philosophy, its long-term role, and
the boundaries that keep it from becoming either too narrow or too ambitious.

## What AgentPool is

AgentPool is an **agent orchestration harness**. It provides the primitives,
protocols, and constraints that let LLM agents work together across multiple
interfaces (ACP, MCP, AG-UI, OpenCode), while leaving the high-level
orchestration decisions to the LLM or the user configuration.

This is a deliberate middle ground between two extremes:

| Extreme | Description | Example |
|---|---|---|
| **Thin SDK** | A wrapper around a single LLM provider with no orchestration | Raw `pydantic-ai` usage |
| **Heavy framework** | Users write orchestration logic in a proprietary DSL or API | CrewAI, AutoGen-style task graphs |
| **AgentPool (target)** | Harness: provides primitives, lets the LLM drive | LLM creates teams, sends messages, manages tasks via tools |

## Core philosophy: Harness, not Framework

### 1. Primitives over Policies

AgentPool exposes primitives such as sessions, runs, messages, tools, and
capabilities. It does not impose a single policy for how agents must coordinate.
Static `graph:` and `teams:` are valid policies, but they are built on the same
primitives as dynamic `team_mode:`.

### 2. LLM-Driven Coordination

Where possible, coordination decisions are made by the LLM through visible
tools rather than by opaque framework code. This makes the system behavior
inspectable, debuggable, and adaptable to new domains without framework changes.

### 3. Protocol Neutrality

AgentPool is not an ACP-only, MCP-only, or OpenCode-only project. The core
abstractions (`Session`, `Agent`, `RunHandle`, `Capability`) must be
implementable across protocols without leaking one protocol's assumptions into
shared APIs.

### 4. Infrastructure Reuse

New features should be built from existing primitives before introducing new
architectural layers. If a feature can be implemented by adding a new capability
or configuration option, it should not require a new base class or service.

### 5. Explicit Lifecycle and Persistence

Sessions, runs, and teams have explicit lifecycles. State that must survive a
process restart is persisted through a small, well-defined set of persistence
patterns (e.g., file-based inboxes, snapshot stores, journals), not through
implicit in-memory caches.

## Scope boundaries

### In scope

- Multi-protocol agent serving (ACP, MCP, AG-UI, OpenCode).
- Session management, message routing, and run lifecycle.
- Static and dynamic team composition.
- Capability injection and tool management.
- Observable, debuggable execution.

### Out of scope

- **Provider-specific model features** must not leak into generic APIs.
- **Built-in business logic** for specific domains (e.g., industrial diagnosis,
  sales). These belong in user agents or higher-level packages.
- **Production deployment infrastructure** such as Kubernetes operators,
  load balancers, or observability backends. AgentPool integrates with them but
  does not provide them.
- **A UI framework** for visualizing agents. AgentPool may expose events that a
  UI consumes, but it is not a UI.

## Long-term vision

AgentPool should be the smallest set of primitives that can express:

- A single agent run.
- A static DAG of agents.
- A dynamically created team of agents.
- A hybrid where some coordination is program-defined and some is LLM-driven.

If a new coordination pattern appears in the multi-agent research community, the
goal is to be able to express it by composing existing primitives rather than
rewriting AgentPool.

## Non-goals that keep us honest

| Temptation | Why it is not a goal |
|---|---|
| "Make AgentPool the one framework for all multi-agent use cases" | It would collapse under the weight of competing domain requirements. We remain a harness. |
| "Hide all complexity from the LLM" | The LLM must see the tools it uses to coordinate. Opaque magic makes the system fragile. |
| "Add a new abstraction for every new feature" | Each new abstraction increases the surface area and must be justified by reuse. |
| "Support every protocol feature natively" | We support protocol-neutral primitives and map protocol features to them. |

## Relationship to other documents

- [RFC-0001: Workers, Teams, Session Management](../rfcs/implemented/RFC-0001-workers-teams-session-management.md) defines the original static team model.
- [RFC-0055: Dynamic Team Mode](../team-mode/RFC-0055-dynamic-team-mode.md) extends the harness with LLM-driven team creation.
- [06-rfc-roadmap](./06-rfc-roadmap.md) places these documents in the broader roadmap.
