# ACP Notification Optimization

## Overview

Reduce system resource consumption when sending large volumes of ACP `session/update` notifications, particularly during `session/load` operations where entire conversation histories are replayed.

## Problem Statement

### Current Behavior

When loading a session or replaying conversation history, each ACP notification is sent as a separate request/response cycle:

```python
# Current implementation in src/acp/agent/notifications.py
async def replay(self, messages: Sequence[ModelRequest | ModelResponse]) -> None:
    for message in messages:
        match message:
            case ModelRequest():
                await self._replay_request(message)
            case ModelResponse():
                await self._replay_response(message)
```

```python
# Each notification requires:
await self.send_update(update)  # Network roundtrip per notification
```

**Performance Impact** (example session with 100 messages):
- ~300-600 individual notifications
- Each notification: ~500ms total (serialization + network RTT)
- Total time: **150-300 seconds** for single session/load operation

### Root Causes

1. **Sequential sending**: Notifications sent one-by-one with `await` blocking
2. **No batching**: Each notification constructed and serialized independently
3. **Network roundtrip overhead**: Every notification waits for TCP acknowledgment
4. **No backpressure mechanism**: Can overwhelm slow remote clients

## Goals

1. Reduce IO overhead by **80%+** for notification-heavy operations
2. Maintain message ordering guarantees required by ACP spec
3. Minimize protocol changes to preserve ecosystem compatibility
4. Leverage anyio capabilities introduced in anyio-cancelscope-and-eventbackpressure change

## Proposed Solution

### Level 1: Micro-Batching (Immediate, Low Risk)

**Strategy**: Batch multiple SessionUpdate objects into a single JSON-RPC call using ACP's `ext_notification` extension mechanism.

**Implementation Changes**:

1. Add batching configuration to `ACPSession`:
```python
@dataclass
class ACPSession:
    session_id: str
    notification_batch_size: int = 20  # Default batch size
    notification_flush_interval: float = 0.05  # Flush after 50ms
```

2. Implement batch collection in `ACPNotifications`:
```python
async def replay(self, messages: Sequence[ModelRequest | ModelResponse]) -> None:
    batch = []
    
    # Collect all updates for a message
    for message in messages:
        match message:
            case ModelRequest():
                batch.extend(self._convert_request(message))
            case ModelResponse():
                batch.extend(self._convert_response(message))
    
    # Send in batches
    for i in range(0, len(batch), self.notification_batch_size):
        sub_batch = batch[i:i+self.notification_batch_size]
        
        # Define new batch schema (ext_notification extension)
        await self.client.ext_notification(
            method="_batch_session_updates",
            params={
                "session_id": self.session_id,
                "updates": [update.dict() for update in sub_batch]
            }
        )
        
        # Small delay to avoid overwhelming remote
        if i < len(batch) - 1:
            await asyncio.sleep(self.notification_flush_interval)
```

**New Schema Extension**:
```python
class BatchSessionUpdate(BaseModel):
    session_id: str
    updates: list[dict]
```

3. Client-side handler (optional for backward compatibility):
```python
class ACPProtocolHandler:
    async def _batch_session_updates_handler(
        self,
        session_id: str,
        updates: list[dict]
    ) -> None:
        """Handle batched session updates."""
        for update_dict in updates:
            try:
                update = SessionUpdate(**update_dict)
                
                # Replay each update using existing logic
                self._replay_single_update(update)
                
            except Exception as e:
                logger.exception("Failed to process batched update", error=str(e))
```

**Expected Benefits**:
- ✅ Reduces network roundtrips by **~90%** (10x batching)
- ✅ Maintains message ordering within batches
- ✅ Uses existing ACP extension mechanism (`ext_notification`)
- ✅ Client can implement at their own pace
- ✅ No changes to ACP specification required

**Trade-offs**:
- ⚠️ Requires client support for `_batch_session_updates` method (optional, degrades gracefully)
- ⚠️ Increased memory usage temporarily during batch collection
- ⚠️ Slightly more complex logic in notification conversion

---

### Level 2: EventStream-Based Sender (Higher Benefit, Medium Risk)

**Strategy**: Replace direct `await client.session_update()` calls with an event stream-based sender that leverages anyio's backpressure and parallel publishing.

**Implementation Changes**:

1. Create new `NotificationSender` component:
```python
# src/acp/agent/notification_sender.py
import anyio
from collections import deque
from dataclasses import dataclass

@dataclass
class NotificationSender:
    """Event stream-based notification sender with backpressure."""
    
    queue: deque[SessionNotification]
    lock: anyio.Lock
    stream: anyio.MemoryObjectSendStream[dict] | None = None
    max_queue_size: int = 1000
    consumer_stopped: bool = False
    consumer_task: anyio.Task | None = None
    task_group: anyio.TaskGroup | None = None
    
    def __init__(self):
        self.queue = deque(maxlen=self.max_queue_size)
        self.lock = anyio.Lock()
    
    async def start_consumer(self) -> None:
        """Start background worker."""
        if self.consumer_stopped:
            return
            
        self.task_group = anyio.create_task_group()
        self.consumer_task = anyio.create_task(self._consumer_loop())
        self.consumer_stopped = False
    
    async def _consumer_loop(self) -> None:
        """Background worker that drains queue with backpressure."""
        try:
            while not self.consumer_stopped:
                if not self.queue:
                    await anyio.sleep(0.01)
                    continue
                
                notification = self.queue.popleft()
                
                # Use SendStream's built-in backpressure
                await self.stream.send({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": notification.dict()
                })
                
        except anyio.CancelledError:
            pass
    
    async def stop(self) -> None:
        """Stop consumer gracefully."""
        self.consumer_stopped = True
        await self.stream.aclose()
        
        if self.task_group:
            self.task_group.cancel()
        
        if self.consumer_task:
            await self.consumer_task
    
    async def enqueue(self, notification: SessionNotification) -> None:
        """Enqueue notification with backpressure handling."""
        async with self.lock:
            if len(self.queue) >= self.max_queue_size:
                # Drop oldest notification (backpressure strategy)
                logger.warning("Notification queue full, dropping oldest")
                self.queue.popleft()
            
            self.queue.append(notification)
```

2. Integrate into `ACPNotifications`:
```python
class ACPNotifications:
    def __init__(self, client, session_id: str, *, enable_streaming: bool = False):
        self.client = client
        self.session_id = session_id
        self.enable_streaming = enable_streaming
        
        if self.enable_streaming:
            from acp.agent.notification_sender import NotificationSender
            self._sender = NotificationSender()
        else:
            self._sender = None
    
    async def replay(self, messages: Sequence[ModelRequest | ModelResponse]) -> None:
        if self._sender and self.enable_streaming:
            # Enqueue all messages
            for message in messages:
                match message:
                    case ModelRequest():
                        for update in self._convert_request(message):
                            await self._sender.enqueue(update)
                    case ModelResponse():
                        for update in self._convert_response(message):
                            await self._sender.enqueue(update)
            
            # Start consumer if not running
            if self._sender.consumer_task is None:
                await self._sender.start_consumer()
            
            # Wait for queue to drain
            while self._sender.queue:
                await anyio.sleep(0.05)
        else:
            # Fallback to sequential sending
            for message in messages:
                match message:
                    case ModelRequest():
                        await self._replay_request(message)
                    case ModelResponse():
                        await self._replay_response(message)
```

3. Modify Connection layer (minimal):
```python
# src/acp/agent/connection.py
class AgentSideConnection:
    def __init__(self, ...):
        # Add optional memory stream parameter
        self._notification_stream = kwargs.get('notification_stream')
    
    async def __aenter__(self) -> Self:
        result = await super().__aenter__()
        
        # Pass stream to ACPNotifications if available
        if self._notification_stream and hasattr(self._agent._notifications, '_sender'):
            self._agent._notifications._sender.stream = self._notification_stream
        
        return result
```

**Expected Benefits**:
- ✅ Full anyio backpressure control
- ✅ Parallel send operations via TaskGroup
- ✅ Automatic consumer management
- ✅ Graceful shutdown handling
- ✅ Backpressure handling (queue overflow protection)

**Trade-offs**:
- ⚠️ Requires minimal Connection API extension
- ⚠️ Slightly higher complexity in NotificationSender lifecycle
- ⚠️ Debugging may be more challenging with background tasks

---

## Migration Strategy

### Phase 1: Level 1 Implementation (Immediate)
- [ ] Add `notification_batch_size` configuration to YAML schema
- [ ] Implement batch collection in `ACPNotifications`
- [ ] Add `_batch_session_updates_handler` to `ACPProtocolHandler`
- [ ] Update tests for batching behavior
- [ ] Benchmark against baseline (time measurement)

### Phase 2: Level 2 Integration (Follow-up)
- [ ] Create `NotificationSender` component
- [ ] Integrate streaming toggle into `ACPNotifications`
- [ ] Add Connection API extension
- [ ] Update tests for streaming behavior
- [ ] Compare performance with Level 1

### Phase 3: Full OpenSpec Adoption (Long-term)
- [ ] Refactor EventBus to use anyio memory streams
- [ ] Adopt OpenSpec's CancelScope hierarchy throughout
- [ ] Migrate all notification paths to EventStream pattern
- [ ] Comprehensive performance testing and optimization

## Success Criteria

A successful optimization is measured by:

1. **Performance**: 80%+ reduction in session/load time for typical sessions (50-100 messages)
2. **Correctness**: All notifications delivered, no dropped messages under normal conditions
3. **Compatibility**: Graceful degradation when streaming unavailable
4. **Observability**: Added metrics tracking (notifications sent, batch hit rate, queue overflow events)

## Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-----------|--------|------------|
| Message reordering | Low | Data loss | Preserve ordering within batches; document ordering guarantees |
| Queue overflow | Low | Message loss | Queue size limits + logging |
| Client compatibility | Medium | Feature unavailable | Sequential fallback maintained |
| Memory spike | Low | OOM | Configurable batch sizes + timeout monitoring |

## Open Questions

1. Should we prioritize Level 1 (lower risk) or Level 2 (higher benefit)?
2. What are reasonable defaults for `notification_batch_size` and queue capacity?
3. Should streaming be opt-in per agent or global?

