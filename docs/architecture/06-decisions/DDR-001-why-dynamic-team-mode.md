# DDR-001: Why Dynamic Team Mode

## Context

AgentPool supports static team composition (`teams:`, `graph:`) and one-shot
delegation (`subagent`). These cover many use cases but fail when the team
composition and coordination strategy depend on the runtime content of the task.

GitHub Discussion #160 (Leoyzen/agentpool) proposed a dynamic team architecture,
and RFC-0055 formalizes it. This decision record captures the rationale for
adding Dynamic Team Mode as a first-class feature.

## Decision

Add a `team_mode:` configuration and a `TeamCommCapability` that enables LLM
agents to create persistent teams, send messages to members, and manage shared
state at runtime.

## Alternatives considered

### Alternative 1: Extend static `teams:` with more features

**Description**: Add more configuration options to the existing `teams:` section
to handle dynamic composition, such as conditional members or templated teams.

**Advantages**:
- No new architectural concept.
- Backward compatible by definition.
- Easier to reason about at review time.

**Disadvantages**:
- Configuration explosion: every dynamic pattern requires a new YAML option.
- The user must still anticipate team structures at write time.
- Cannot handle truly runtime decisions (e.g., "spawn 5 translators because the
  manual has 500 pages").

**Why rejected**: It does not solve the problem. Static configuration cannot
express runtime composition without becoming a poor scripting language.

### Alternative 2: Use `subagent` for everything

**Description**: Improve the `subagent` tool so it can be called multiple times,
with shared state, and non-blocking semantics.

**Advantages**:
- Reuses an existing primitive.
- No new capability needed.

**Disadvantages**:
- `subagent` is designed for one-shot delegation. Stretching it to peer
  communication and persistent teams distorts its semantics.
- Cleanup and lifecycle become implicit and fragile.

**Why rejected**: It would turn `subagent` into a grab-bag of team behaviors,
making both the tool and the codebase harder to understand.

### Alternative 3: Build an external framework on top of AgentPool

**Description**: Do not add dynamic teams to AgentPool. Instead, let users build
their own team framework on top of the existing primitives.

**Advantages**:
- Keeps AgentPool core small.
- Lets different users choose different team models.

**Disadvantages**:
- Fragmentation: every user builds a slightly different team model.
- AgentPool loses the multi-agent harness positioning.
- Common patterns (inbox, blackboard, task board) get reimplemented.

**Why rejected**: The pattern is common enough and general enough to belong in
the harness. The implementation is small because it reuses existing primitives.

### Alternative 4: Add Dynamic Team Mode as a Capability (chosen)

**Description**: Implement Dynamic Team Mode as a new capability plus a
configuration model, reusing `SessionPool`, `RunHandle`, and file-based state.

**Advantages**:
- Minimal core changes.
- Consistent with existing extension model.
- LLM-driven coordination is inspectable and debuggable.
- Static and dynamic teams can coexist.

**Disadvantages**:
- Requires stable session-management APIs.
- Adds a new concept to the system.

**Why chosen**: It solves the problem without introducing a new architectural
layer. It is the smallest change that enables the target workflows.

## Consequences

### Positive

- AgentPool can support runtime team composition.
- Industrial diagnosis, manual translation, and sales assistant scenarios become
  expressible.
- The design aligns with production patterns from Qwen Code and OMO.

### Negative

- Increases the conceptual surface area of AgentPool.
- Requires stable session lifecycle APIs before it can be fully reliable.
- Team protocol prompt engineering becomes a new failure mode.

## Related documents

- [RFC-0055: Dynamic Team Mode](../team-mode/RFC-0055-dynamic-team-mode.md)
- [03: Problem Space](./03-problem-space.md)
- [04: Constraints and Principles](./04-constraints-and-principles.md)
- [05: Framework Comparison](./05-framework-comparison.md)

## Status

Accepted, pending implementation of prerequisites (RFC-0054, issue #170).
