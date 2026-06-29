# Design: AnyIO CancelScope Nesting and EventBus Backpressure

## Context

AgentPool has migrated to AnyIO structured concurrency API (anyio 4.13.0), but the current implementation has gaps in two critical areas:

**Current State:**
- Subagents are spawned but their CancelScopes operate independently of parent scopes
- EventBus uses MemoryObjectStream but lacks production-grade flow control
- No bounded queue mechanisms to prevent consumer overflow
- Missing producer coordination for parallel event publishing

**Constraints:**
- Must maintain backward compatibility with existing agent/subagent delegation API
- Cannot break current test suite
- Should preserve anyio 4.13.0 structured concurrency patterns
- Minimal performance overhead for normal operation

## Goals / Non-Goals

**Goals:**
1. Enforce hierarchical CancelScope nesting for proper cancellation propagation
2. Implement bounded memory channels with configurable queue sizes
3. Add backpressure signaling from consumers to producers  
4. Enable safe parallel publishing from multiple event sources
5. Provide configuration options for tuning queue behavior
6. Maintain clean anyio patterns (shield, fail_after, timeout)

**Non-Goals:**
- Complete rewrite of EventBus architecture (incremental improvements)
- Changing external protocol server event formats
- Modifying MCP client/server communication patterns
- Adding complex scheduling policies beyond basic backpressure

## Decisions

### 1. CancelScope Hierarchy Strategy

**Decision**: Pass parent CancelScope as context during subagent spawning

```python
async with anyio.create_task_group() as tg:
    # Parent scope is inherited by all tasks in this group
    async with anyio.create_task_group() as child_tg:
        # Child scope is nested under parent
        tg.start_soon(subagent.run, parent_scope=child_tg.cancel_scope)
```

**Rationale**: 
- AnyIO TaskGroups naturally nest CancelScopes
- Cancelling parent TaskGroup automatically cancels all children
- Maintains clean cancellation semantics
- No additional bookkeeping required

**Alternatives Considered:**
- Manual scope tracking: Too error-prone, requires explicit cleanup
- Global scope registry: Doesn't scale with concurrent agents
- Callback-based cancellation: Complex to implement and debug

### 2. Bounded MemoryObjectStream Implementation

**Decision**: Wrap anyio MemoryObjectStream with configurable capacity limits

```python
class BoundedMemoryObjectStream(Generic[T]):
    def __init__(self, max_size: int = 100):
        self._stream = MemoryObjectStream[T]()
        self._max_size = max_size
        self._semaphore = anyio.Semaphore(max_size)
    
    async def send(self, item: T) -> None:
        await self._semaphore.acquire()
        try:
            await self._stream.send(item)
        except:
            self._semaphore.release()
            raise
    
    async def send_nowait(self, item: T) -> bool:
        if self._semaphore.locked():
            return False
        return self._stream.send_nowait(item)
```

**Rationale:**
- Semaphore provides exact count-based backpressure
- Producer blocks when queue is full (natural backpressure)
- Standard anyio primitives, no external dependencies
- Configurable per channel type

**Alternatives Considered:**
- asyncio.Queue with maxsize: Less performant than memory streams
- Discard policy: Loses events, bad for reliability
- Unbounded streams: Risk OOM under load

### 3. Backpressure Signaling

**Decision**: Wait on semaphore sends - implicit backpressure

```python
# Producer side - waits when full
await event_queue.send(event)

# Consumer side - creates capacity
async for event in event_queue.receive():
    # Process event, automatically frees semaphore slot
    pass
```

**Rationale:**
- Implicit backpressure via semaphore blocking is simplest
- No separate notification mechanism needed
- Producers naturally slow down when congested
- Consumer doesn't need explicit "ready" signals

**Alternatives Considered:**
- Explicit backpressure callbacks: More complex, race conditions possible
- Event counters + polling: Inefficient, breaks async flow
- Channel splitting: Requires routing logic, added complexity

### 4. Parallel Publishing Safety

**Decision**: Lock-protected multiple-producer support

```python
class ConcurrentEventPublisher:
    def __init__(self, stream: BoundedMemoryObjectStream):
        self._stream = stream
        self._lock = anyio.Lock()
    
    async def publish(self, event: Event) -> None:
        async with self._lock:
            await self._stream.send(event)
```

**Rationale:**
- Lock prevents concurrent send() calls from interfering
- Maintains FIFO ordering despite parallel producers  
- Single point of synchronization, easy to reason about
- Performance acceptable for typical event rates (<1000 events/sec)

**Alternatives Considered:**
- Separate channels per producer: Increases complexity, requires merge logic
- Atomic send operations: Not natively supported by anyio streams
- Compare-and-swap on head pointer: Too low-level, error-prone

### 5. Configuration Management

**Decision**: Centralize queue configuration in EventBusSettings

```python
@dataclass
class EventBusSettings:
    """Configuration for EventBus behavior."""
    # Queue sizes per channel type
    max_events_queue_size: int = 1000
    max_subscribers_queue_size: int = 100
    
    # Backpressure behavior  
    enable_backpressure: bool = True
    block_on_full: bool = True  # vs raise exception
    
    # Timeout configuration
    send_timeout: float | None = None  # None = wait forever
    receive_timeout: float | None = None
```

**Rationale:**
- Single source of truth for tuning
- Different sizes for different event types
- Enables performance tuning without code changes
- Easy to add monitoring/debugging hooks

**Alternatives Considered:**
- Hardcoded constants: Not flexible for different workloads
- Environment variables only: Harder to document and test
- Per-instance config: Complex to manage, inconsistent behavior

### 6. Integration Points

**Decision**: Update existing delegation patterns transparently

```python
# Delegation module changes
async def spawn_subagent(
    parent_context: AgentContext,
    subagent_name: str,
) -> AsyncIterator[Event]:
    # Parent context carries active CancelScope
    with parent_context.active_cancel_scope:
        return await subagent.run_stream()
```

**Rationale:**
- Leverages anyio's automatic scope inheritance
- Minimal code changes in delegation layer
- Preserves existing agent.run_stream() API
- Transparent to user code

**Alternatives Considered:**
- New spawn_subagent_with_scope API: Breaking change to existing code
- Thread-local scope storage: Doesn't work across async boundaries
- Explicit scope passing: More ceremony, easy to forget

## Risks / Trade-offs

**Risks**

[Risk] Increased memory usage due to semaphore objects
→ Mitigation: Use memory-efficient Semaphore (counts only, not wait queues)
→ Mitigation: Make queue sizes configurable and default conservatively

[Risk] Performance degradation from lock contention in parallel publishing
→ Mitigation: Benchmark lock vs lock-free alternatives before merging
→ Mitigation: Consider lock-free atomic operations if contention high

[Risk] Deadlock if child subagent spawns its own child that captures scope incorrectly
→ Mitigation: Add scope inheritance validation in tests
→ Mitigation: Document scope propagation rules clearly

[Risk] Backpressure causes cascading slowdowns
→ Mitigation: Provide overflow behavior option (drop vs block)
→ Mitigation: Add metrics for monitoring queue pressure

[Risk] Breaking existing integration tests
→ Mitigation: Run full test suite before merging
→ Mitigation: Add integration tests specifically for cancellation scenarios

**Trade-offs**

[Trade-off] Bounded queues may drop events vs unbounded never drops
→ Chose bounded for reliability: Predictable memory usage is more important than never dropping
→ Mitigation: Large default sizes, configurable per workload

[Trade-off] Synchronous block_on_full vs asynchronous retry behavior
→ Chose synchronous blocking for simplicity: Easier to understand, prevents runaway producers
→ Mitigation: Async timeout option to prevent indefinite blocking

[Trade-off] Strong typing complexity in generic bounded stream wrapper
→ Accept for type safety: Prevents runtime errors, worth the verbosity
→ Mitigation: Type aliases for common stream types to reduce verbosity

## Open Questions

1. **Default queue sizes**: What are reasonable defaults for different event types?
   - Events queue (high frequency): 1000? 5000?
   - Subscriber notifications (low frequency): 100? 50?
   - Need to benchmark typical AgentPool workloads

2. **Backpressure propagation**: Should backpressure signal cascade upstream?
   - If EventBus is full, should it slow down agent generation?
   - Or just buffer and let agents run at their pace?

3. **Scope cleanup timing**: When exactly should child scopes be cancelled?
   - Immediately when parent cancels, or allow graceful shutdown period?
   - Shield during critical cleanup phases?

4. **Monitoring hooks**: Should we expose queue depth statistics?
   - For operational visibility into system health
   - Potential feature for later, not MVP