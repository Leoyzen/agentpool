# ACP Notification Optimization - Design

## Implementation Strategy

We'll optimize ACP `session/update` notification throughput using a phased approach:

- **Level 1**: Micro-batching (immediate, low risk)
  - Add batching configuration to session state
  - Implement batch collection in replay
  - Use ACP's ext_notification for batch delivery
  - Maintain backward compatibility with graceful degradation

- **Level 2**: EventStream-based sender (higher benefit, medium risk)
  - Create NotificationSender component leveraging anyio memory streams
  - Implement backpressure-aware parallel sending
  - Integrate streaming toggle for ACPNotifications
  - Requires minimal Connection API extensions

- **Level 3**: Full OpenSpec adoption (long-term, highest benefit)
  - Refactor EventBus to use anyio memory streams
  - Adopt CancelScope hierarchy throughout system
  - Migrate all notification paths to EventStream pattern

## Detailed Design

### Phase 1: Micro-Batching

#### Goal
Reduce network roundtrips by grouping multiple SessionUpdate objects into single ext_notification calls.

#### Architecture

```
Current (Sequential):
messages → [update1] → [update2] → [update3] → ... → [updateN]
              ↓              ↓              ↓
            300 roundtrips    (slow)

Proposed (Batched):
messages → collect → batch1 (10 updates) → batch2 (10 updates) → ...
              ↓              ↓              ↓
           ~30 roundtrips     (3x faster)
```

#### Configuration Schema

```python
# acp/schema/agent_responses.py
class ACPSessionConfig(BaseModel):
    notification_batch_size: int = 20
    notification_flush_interval: float = 0.05
    enable_notification_batching: bool = True
```

**Configuration Choices**:

| Config | Value | Effect |
|--------|-------|---------|
| batch_size | 10 | Minimal batching, good for slow clients |
| batch_size | 20 | Recommended balance |
| batch_size | 50 | Maximum batching, may overwhelm slow clients |
| flush_interval | 0.02s | Very aggressive (20ms) |
| flush_interval | 0.05s | Balanced (50ms) |
| flush_interval | 0.1s | Conservative (100ms) |

#### Batch Collection Logic

```python
async def replay(self, messages: Sequence[ModelRequest | ModelResponse]) -> None:
    """Replay with batching support."""
    
    all_updates = []
    
    # Convert each message to multiple SessionUpdate objects
    for message in messages:
        match message:
            case ModelRequest():
                all_updates.extend(self._convert_request(message))
            case ModelResponse():
                all_updates.extend(self._convert_response(message))
    
    # Send in batches
    for i in range(0, len(all_updates), self.notification_batch_size):
        batch = all_updates[i:i+self.notification_batch_size]
        
        # Flush periodically to avoid overwhelming remote
        if i > 0 and i % 10 == 0:
            await asyncio.sleep(self.notification_flush_interval)
        
        await self.send_batch(batch)
```

#### Ext Notification Protocol Extension

**New RPC Method**:
```python
# acp/schema/client_requests.py
class BatchSessionUpdatesRequest(BaseModel):
    method: Literal["_batch/session_updates"]
    params: BatchSessionUpdate
    
class BatchSessionUpdate(BaseModel):
    session_id: str
    updates: list[dict]

class BatchSessionUpdatesResponse(BaseModel):
    # Standard response, no content needed
    pass
```

**Server-Side Handler**:
```python
# src/acp_server/acp_agent.py
class AgentPoolACPAgent(ACPAgent):
    async def _batch_session_updates_handler(
        self,
        session_id: str,
        updates: list[dict]
    ) -> None:
        """Handle batched session updates from client."""
        
        session = self.session_manager.get_session(session_id)
        if not session:
            logger.warning("Session not found", session_id=session_id)
            return
        
        # Replay each update using existing logic
        for update_dict in updates:
            try:
                update = SessionUpdate(**update_dict)
                await session.notifications.replay_single_update(update)
            except Exception as e:
                logger.exception("Failed to process batched update", error=str(e))
        
        logger.info(
            "Processed batched updates",
            session_id=session_id,
            count=len(updates)
        )
```

#### Backward Compatibility

If client doesn't support `_batch_session_updates`:

1. Detect capability during initialize:
```python
capabilities = init_response.agent_capabilities

has_batching = getattr(capabilities, "ext_methods", {}).get("_batch_session_updates") is not None
```

2. Gracefully degrade:
```python
if has_batching:
    await self.send_batch(batch)
else:
    # Sequential sending (current behavior)
    for update in batch:
        await self.send_update(update)
```

#### Metrics Tracking

Add observability to measure effectiveness:

```python
# src/acp/agent/notifications.py
class ACPOptimizations:
    total_notifications_sent: int = 0
    batches_sent: int = 0
    last_flush_time: float = 0.0
    
    async def _track_sent(self, count: int) -> None:
        self.total_notifications_sent += count
        self.batches_sent += 1
        logger.debug(
            "Notifications sent",
            total=self.total_notifications_sent,
            batch=self.batches_sent,
            rate=count / (time.monotonic() - self.last_flush_time)
        )
```

---

### Phase 2: EventStream-Based Sender

#### Goal
Leverage anyio's memory streams and backpressure mechanism for parallel, backpressure-aware sending.

#### Architecture

```
ACPNotifications (Stream Sender)
┌─────────────────────────────────────┐
│                                    │
│  MemoryObjectSendStream          │
│  ┌──────┬───────────────────┐ │
│  │                     │           │
│  │  Send & Consumer Tasks  │
│  │                     │           │
└─┴───────────────────────────┘      │
              ↓                    ↓
         Concurrent publish       │
         (anyio.TaskGroup)       │
              ↓                    ↓
     Subscribers (ACP clients) │
```

#### NotificationSender Component

```python
# src/acp/agent/notification_sender.py
from __future__ import annotations
import anyio
from collections import deque
import logging
from dataclasses import dataclass

@dataclass
class NotificationSender:
    """Event stream-based notification sender with backpressure."""
    
    queue: deque[SessionNotification]
    lock: anyio.Lock
    stream: anyio.MemoryObjectSendStream[dict]
    consumer_task: anyio.Task | None = None
    task_group: anyio.TaskGroup | None = None
    consumer_stopped: bool = False
    
    max_queue_size: int = 1000
    dropped_count: int = 0
    sent_count: int = 0
    
    def __init__(
        self,
        stream: anyio.MemoryObjectSendStream[dict],
        max_queue_size: int = 1000
    ):
        self.stream = stream
        self.queue = deque(maxlen=max_queue_size)
        self.lock = anyio.Lock()
        self.task_group = anyio.create_task_group()
    
    async def start(self) -> None:
        """Start background consumer that drains queue."""
        if self.consumer_stopped:
            return
        
        self.consumer_stopped = False
        
        self.consumer_task = anyio.create_task(self._consumer_loop())
    
    async def _consumer_loop(self) -> None:
        """Background worker that drains queue with backpressure."""
        while not self.consumer_stopped:
            if not self.queue:
                await anyio.sleep(0.01)
                continue
            
            notification = self.queue.popleft()
            
            # Key: use SendStream's built-in backpressure
            try:
                await self.stream.send({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": notification.dict()
                })
                self.sent_count += 1
            except anyio.WouldBlock:
                # Buffer full: wait briefly before retry
                logger.warning("Stream buffer full, waiting...")
                await anyio.sleep(0.05)
                self.queue.appendleft(notification)
            except anyio.ClosedResourceError:
                # Subscriber disconnected
                break
            except Exception as e:
                logger.exception("Send failed", error=str(e))
    
    async def enqueue(self, notification: SessionNotification) -> None:
        """Enqueue notification with backpressure handling."""
        async with self.lock:
            if len(self.queue) >= self.max_queue_size:
                logger.warning(
                    "Notification queue full, dropping oldest",
                    size=len(self.queue)
                )
                self.dropped_count += 1
                self.queue.popleft()  # Drop oldest
                # Don't append
            
            self.queue.append(notification)
    
    async def stop(self) -> None:
        """Stop consumer gracefully."""
        self.consumer_stopped = True
        
        async with self.lock:
            remaining = list(self.queue)
            for notification in remaining:
                try:
                    await self.stream.send({
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": notification.dict()
                    })
                except Exception:
                    pass
            self.queue.clear()
        
        if self.consumer_task:
            self.consumer_task.cancel()
        
        if self.task_group:
            # Wait for consumer to finish flushing
            for task in self.task_group:
                if not task.done():
                    await task
            
            # Cancel remaining tasks
            self.task_group.cancel()
    
    async def get_stats(self) -> dict[str, int]:
        """Get statistics for monitoring."""
        return {
            "queue_size": len(self.queue),
            "dropped_count": self.dropped_count,
            "sent_count": self.sent_count
        }
```

#### Integration into ACPNotifications

```python
# src/acp/agent/notifications.py
class ACPNotifications:
    def __init__(
        self,
        client,
        session_id: str,
        *,
        enable_streaming: bool = False
    ):
        self.client = client
        self.session_id = session_id
        self.enable_streaming = enable_streaming
        
        # New: Attach stream sender when enabled
        if self.enable_streaming and hasattr(self.client, '_notification_stream'):
            from acp.agent.notification_sender import NotificationSender
            self._sender = NotificationSender(stream=self._notification_stream)
        else:
            self._sender = None
    
    async def replay(self, messages: Sequence[ModelRequest | ModelResponse]) -> None:
        """Replay with optional streaming optimization."""
        
        if self._sender:
            # Collect all notifications
            all_updates = []
            for message in messages:
                match message:
                    case ModelRequest():
                        all_updates.extend(self._convert_request(message))
                    case ModelResponse():
                        all_updates.extend(self._convert_response(message))
            
            # Enqueue and wait for drain
            for update in all_updates:
                await self._sender.enqueue(update)
            
            # Start sender if not running
            if self._sender.consumer_task is None:
                await self._sender.start()
            
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
    
    async def stop_streaming(self) -> None:
        """Stop background sender."""
        if self._sender:
            await self._sender.stop()
```

#### Minimal Connection API Extension

```python
# src/acp/agent/connection.py
class AgentSideConnection(Client):
    def __init__(
        self,
        to_agent,
        input_stream,
        output_stream,
        observers,
        *,
        debug_file: str | None,
        notification_stream: anyio.MemoryObjectSendStream[dict] | None = None,  # NEW
    ):
        agent = to_agent(self)
        input_stream = input_stream
        output_stream = output_stream
        observers = list(observers or [])
        debug_file = debug_file
        
        # Pass notification stream to ACPNotifications
        self._notification_stream = notification_stream
    
    async def __aenter__(self) -> Self:
        result = await super().__aenter__()
        
        # Pass stream to agent
        if hasattr(agent, '_notifications') and self._notification_stream:
            agent._notifications.notification_stream = self._notification_stream
        
        return result
```

---

### Testing Strategy

#### Baseline Measurement

Before optimization, establish performance baseline:

```bash
pytest tests/servers/acp_server/test_acp_load.py \
  --benchmark-only-session-load \
  --baseline \
  --save-results baseline.json
```

Metrics to collect:
- Total session/load time
- Number of session/update notifications sent
- Network roundtrip latency distribution
- Memory usage during load
- CPU usage profile

#### Optimization Validation

After each phase implementation:

```bash
pytest tests/servers/acp_server/test_acp_load.py \
  --benchmark-only-session-load \
  --compare baseline.json
```

Expected improvements:
- Phase 1: 70-85% reduction in session/load time
- Phase 2: Additional 10-20% reduction with full streaming
- Combined: 80%+ overall improvement

#### Stress Testing

Test extreme scenarios:

```python
# tests/performance/test_notification_backpressure.py
@pytest.mark.perfomance
def test_concurrent_subscribers_congestion():
    """Test notification queue overflow scenario."""
    ...

def test_slow_subscriber_isolation():
    """Test that one slow subscriber doesn't block others."""
    ...
```

---

## Migration Guide

### When to Use Level 1 vs Level 2

| Scenario | Recommended Approach |
|----------|---------------------|
| Quick win for users | Level 1 (micro-batching) |
| System-wide optimization | Level 2 (EventStream) |
| Both for maximum benefit | Level 1 + Level 2 combined |

### Rollback Plan

Each phase can be independently rolled back:

```bash
# Revert to git commit before Phase N
git revert <commit-hash>

# Apply previous phase only
openspec apply --change acp-notification-optimization --phase 1

# Or skip to next phase directly
openspec apply --change acp-notification-optimization --phase 2
```

---

## Success Criteria

- **Performance**: 80%+ reduction in session/load time for typical sessions (50-100 messages)
- **Correctness**: All notifications delivered, no dropped messages under normal conditions
- **Compatibility**: Graceful degradation when streaming unavailable
- **Observability**: Added metrics tracking for validation
- **Stability**: No increase in test failures or regressions