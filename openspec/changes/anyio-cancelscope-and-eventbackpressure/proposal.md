# Proposal: AnyIO CancelScope Nesting and EventBus Backpressure

## Why

The current anyio structured concurrency implementation has two critical gaps that affect system reliability and cancellation propagation:

1. **Subagent cancellation hierarchy is broken**: When subagents are spawned, their CancelScopes are not properly nested under parent agent scopes. This means that cancelling a parent agent does not reliably propagate to its children, leading to orphaned tasks and resource leaks.

2. **EventBus lacks production-grade controls**: The EventBus memory stream implementation is missing critical features needed for production use:
   - No backpressure mechanism to prevent overwhelming slow consumers
   - No support for parallel publishing from multiple producers
   - Missing memory object stream safeguards against queue overflow

These issues became evident during the migration to anyio 4.13.0 structured concurrency API, where proper scope management is essential for predictable cleanup.

## What Changes

### Core Architecture Changes

- **BREAKING**: Redefine subagent lifecycle to enforce parent-child CancelScope nesting
- Implement EventBus producer-side backpressure with bounded queues
- Add parallel publishing support with synchronization primitives
- Strengthen memory object stream overflow protection

### Specific Components Affected

1. **AgentPool delegation module**: Update subagent spawning to inherit parent CancelScope
2. **EventBus core**: Add bounded channel support and producer backpressure
3. **Session orchestration**: Ensure CancelScope propagation through run hierarchy
4. **Protocol servers**: Update event publishing to respect backpressure signals

## Capabilities

### New Capabilities

**cancelscope-nesting**: Enforce hierarchical cancellation propagation
- Ensures parent agent cancellation propagates to all child subagents
- Provides predictable cleanup and resource release
- Prevents orphaned background tasks

**eventbus-backpressure**: Producer-controlled flow for event streams  
- Bounded memory channels prevent queue overflow
- Producers wait when consumer is at capacity
- Configurable queue sizes per event type

**eventbus-parallel-publish**: Multi-producer safe publishing
- Thread-safe publishing from multiple sources
- Backpressure-aware enqueue operations
- Graceful degradation under heavy load

### Modified Capabilities

None - these are net-new capabilities without breaking changes to existing specs.

## Impact

- **Code Changes**: Core orchestration layer, EventBus implementation, session management
- **API Impact**: New configuration options for queue sizes and backpressure behavior
- **Performance**: Improved reliability under load, predictable cleanup on cancellation
- **Testing**: Extensive unit tests for cancellation hierarchy and backpressure scenarios