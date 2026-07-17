# 03: Problem Space

This document states the problems AgentPool's architecture is solving, with
evidence from the codebase, RFCs, and discussions. The goal is to avoid
prescribing solutions before the problems are explicit.

## The core problem: static composition is not enough

AgentPool started with static teams (`teams:`, `graph:`). These are powerful for
pipelines where the composition and control flow are known at configuration time.
However, they cannot handle tasks where the optimal team composition depends on
the runtime content of the task itself.

### Three real scenarios

| Scenario | Why static composition fails |
|---|---|
| **Industrial diagnosis** | The required specialists (electrical, mechanical, thermal) depend on the fault symptoms. The LLM must decide whom to consult after seeing the initial report. |
| **Manual translation** | The number of translators and reviewers depends on the manual length. Task dependencies (glossary → chapters → consistency check) are task-specific. |
| **Sales assistance** | The sales team composition (researcher, presenter, objection handler) depends on the customer and product. |

In each case, the team composition, coordination strategy, and shared state are
unknown until the LLM has processed the user's request.

## Evidence from the codebase

### 1. Static teams are limited to known DAGs

The current `graph:` mechanism compiles to a pydantic-graph DAG. The graph is
fixed at load time. There is no primitive for an agent to create a new teammate
at runtime.

Relevant document: [RFC-0001: Workers, Teams, Session Management](./rfcs/implemented/RFC-0001-workers-teams-session-management.md).

### 2. Subagent delegation is one-shot and blocking

The `subagent` tool allows an LLM to delegate a task, but:

- It is **blocking**: the lead waits for the result before continuing.
- It is **one-shot**: there is no persistent peer relationship for follow-up
  messages.
- It has **no shared state**: the lead must pass all context in a single prompt
  and receive the result in a single response.

Relevant document: [RFC-0028: Delegation Provider Session Adaptation](./rfcs/draft/RFC-0028-delegation-provider-session-adaptation.md).

### 3. Session management has technical debt that affects team features

GitHub issue #170 (Leoyzen/agentpool) identifies session-management technical debt
that Dynamic Team Mode will depend on:

- `close_session` paths are fragmented across `SessionController` and
  `SessionPool`.
- `SessionData.status` state machine is not unified (`active`, `closed`,
  `idle`).
- Storage providers have dual paths.
- These issues create risk for team lifecycle management (creating, closing,
  and cleaning up member sessions).

Relevant document: [RFC-0055 design notes](../team-mode/RFC-0055-design-notes.md).

### 4. Cross-framework research confirms the pattern

A survey of 8 multi-agent frameworks (see [05-framework-comparison](./05-framework-comparison.md))
shows that production systems (Qwen Code, OMO) are converging on:

- LLM-driven team creation.
- Tool-based communication.
- File-based persistence for shared state.

AgentPool does not have this pattern yet. The risk is not just missing a
feature; it is losing the ability to be a harness for modern multi-agent
orchestration.

## Sub-problems that must be solved

### Problem 1: Bidirectional Lead ↔ Worker communication

Current delegation is top-down. A worker cannot send a message back to the lead
unless it is the final response. Teams need ongoing, multi-turn communication.

### Problem 2: Peer ↔ Peer communication

Workers must be able to talk to each other directly, not just through the lead.
This is essential for parallel workflows and escalation.

### Problem 3: Shared state across agents

A team needs a shared blackboard and task board. Each session cannot keep its
state isolated. The shared state must be:

- Readable and writable by all members.
- Persisted beyond a single turn.
- Inspectable by operators.

### Problem 4: Dynamic team lifecycle

A team must be creatable, runnable, and deletable at runtime. The LLM decides
when each phase happens. The framework must handle the cleanup of member
sessions and file state.

### Problem 5: Avoid framework bloat

The solution must not turn AgentPool into a heavy framework. It should reuse
existing infrastructure (`SessionPool`, `RunHandle`, `Capability`, `EventBus`)
with minimal changes.

## What happens if we do not solve this?

- **Cost**: Users must manually define team composition in YAML for each task
  type, even when the LLM could determine it more efficiently.
- **Risk**: AgentPool falls behind competing frameworks that already support
  dynamic team creation.
- **Complexity**: Users will build ad-hoc dynamic team systems on top of
  AgentPool, fragmenting the ecosystem.
- **Technical debt**: Team features will be built on a session-management
  foundation that is not yet stable.

## Open questions that derive from the problem space

1. Should dynamic teams coexist with static teams forever, or is one intended to
   replace the other eventually?
2. What is the smallest set of primitives that can express both static and
   dynamic teams?
3. How much of the coordination should be visible to the LLM vs. hidden in
   framework code?

These questions are explored in the [decision records](./06-decisions/).
