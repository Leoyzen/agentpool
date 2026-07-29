# Async Producer-Consumer Simulation Architecture

## Overview

This document describes an async producer-consumer architecture for AgentPool simulation framework, where the Sim Agent can perform parallel work (research, information gathering) while the Target Agent processes requests.

## Core Concept

**Producer-Consumer Pattern with Directives**

```
┌─────────────────────────────────────────────────────────────────┐
│                    Sim Agent (Consumer Thread)                   │
│                                                                  │
│  ┌──────────────┐                                                │
│  │ talk_to_     │───► Creates Slot, launches Target Agent       │
│  │ target(msg)  │      (Non-blocking, returns immediately)       │
│  └──────────────┘                                                │
│                                                                  │
│  ┌──────────────────────────────────────┐                       │
│  │ Parallel Background Tasks             │                       │
│  │ - search_documentation(device)        │                       │
│  │ - gather_context(symptoms)            │                       │
│  │ - query_knowledge_base(history)       │                       │
│  └──────────────────────────────────────┘                       │
│                                                                  │
│  ◄── Directive Queue (from Target Agent)                        │
│       - type: response (text streaming)                         │
│       - type: elicitation (needs answer)                        │
│       - type: completed (done)                                  │
│                                                                  │
│  ┌──────────────┐                                                │
│  │ get_response │───► Fetches result from Slot                  │
│  │ (slot_id)    │      (Blocking with timeout)                   │
│  └──────────────┘                                                │
│                                                                  │
│  Decision Logic:                                                  │
│  - If status == completed: done                                  │
│  - If status == elicitation:                                     │
│      ┌──────────────┐                                            │
│      │ decide()     │───► Answer question? Which info to reveal? │
│      └──────────────┘                                            │
│           │                                                       │
│           ▼                                                       │
│      ┌──────────────┐                                            │
│      │ provide_     │───► Injects answer via InputProvider       │
│      │ answer()     │                                            │
│      └──────────────┘                                            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ Events: ToolCall, Response
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Target Agent (Producer Thread)                │
│                                                                  │
│   ┌──────────────┐                                              │
│   │ run_stream   │                                              │
│   │ (message)    │                                              │
│   └───────┬──────┘                                              │
│           │                                                      │
│    [Processing...]                                               │
│           │                                                      │
│    ┌──────▼──────┐                                               │
│    │ PartDelta   │────► Directive: response                     │
│    └─────────────┘                                               │
│           │                                                      │
│    ┌──────▼─────────────────────┐                                │
│    │ ToolCallStartEvent         │                                │
│    │ "question": "Need info X"  │                                │
│    └─────────┬──────────────────┘                                │
│              │                                                   │
│              ▼                                                   │
│    ┌─────────────────────────────┐                               │
│    │ InputProvider.get_          │                               │
│    │ elicitation(params)         │                               │
│    │                             │                               │
│    │ [BLOCKS HERE]               │                               │
│    │ Waits for Sim Agent to      │                               │
│    │ call provide_answer()       │                               │
│    └───────────┬─────────────────┘                               │
│                │ Answer from Sim Agent                           │
│                ▼                                                 │
│    ┌─────────────────────────────┐                               │
│    │ Continue processing...      │                               │
│    │                             │                               │
│    │ StreamCompleteEvent         │                               │
│    └─────────────────────────────┘                               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. SimulationSlot

Represents a single conversation session between Sim Agent and Target Agent.

```python
@dataclass
class SimulationSlot:
    slot_id: str
    status: SlotStatus
    user_message: str
    target: Agent
    
    # Output accumulation
    response_text: str = ""
    elicitation_params: Optional[ElicitRequestParams] = None
    
    # Synchronization
    response_event: asyncio.Event
    answer_queue: asyncio.Queue
```

**Lifecycle**:
```
pending → responding → [eliciting → responding] → completed
            │              │
            │              └── Sim Agent provides answer
            └── Stream output updated
```

### 2. Directive System

**Purpose**: Notify Sim Agent of Target Agent progress without blocking.

```python
@dataclass
class Directive:
    """Notification from Target Agent to Sim Agent"""
    type: Literal["response", "elicitation", "completed", "error"]
    slot_id: str
    timestamp: datetime
    
    # Type-specific fields
    response_delta: Optional[str] = None      # For type="response"
    questions: Optional[List[Question]] = None # For type="elicitation"
    error_message: Optional[str] = None       # For type="error"
```

**Flow**:
1. Target Agent emits event during `run_stream()`
2. SimulationController translates event to Directive
3. Directive placed in async queue
4. Sim Agent (optionally) consumes from queue
5. Sim Agent calls `get_response()` to fetch full state

### 3. SimulationController

Manages all active slots and directive routing.

```python
class SimulationController:
    def __init__(self):
        self._slots: Dict[str, SimulationSlot] = {}
        self._directive_queue: asyncio.Queue[Directive] = asyncio.Queue()
    
    async def create_slot(self, target: Agent, message: str) -> str:
        """Create slot and launch Target Agent in background"""
        slot_id = uuid4().hex
        slot = SimulationSlot(...)
        self._slots[slot_id] = slot
        
        # Launch background task
        asyncio.create_task(self._run_target(slot))
        return slot_id
    
    async def get_directive(self, timeout: float = 1.0) -> Optional[Directive]:
        """Sim Agent polls for new directives"""
        try:
            return await asyncio.wait_for(
                self._directive_queue.get(), 
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return None
```

### 4. InputProvider Integration

**Critical**: InputProvider runs in Target Agent's context, needs to communicate back to Controller.

```python
class SimulationInputProvider(InputProvider):
    def __init__(self, controller: SimulationController):
        self.controller = controller
        self._pending_answers: Dict[str, asyncio.Future] = {}
    
    async def get_elicitation(self, params: ElicitRequestParams) -> ElicitResult:
        """
        Called when Target Agent needs input.
        Blocks until Sim Agent provides answer via provide_answer() tool.
        """
        # Find current slot (assumes 1v1 for simplicity)
        slot = self.controller.get_active_slot()
        slot.elicitation_params = params
        slot.status = "eliciting"
        
        # Create future for answer
        answer_future = asyncio.Future()
        self._pending_answers[slot.slot_id] = answer_future
        
        # Notify Sim Agent
        await self.controller.notify_elicitation(slot.slot_id, params)
        
        # Block here - this is Target Agent's thread
        try:
            answer = await asyncio.wait_for(answer_future, timeout=60.0)
            return ElicitResult(action="accept", content=answer)
        except asyncio.TimeoutError:
            return ElicitResult(action="decline")
    
    async def receive_answer(self, slot_id: str, answer: Dict) -> None:
        """Called by ToolProvider when Sim Agent provides answer"""
        if slot_id in self._pending_answers:
            future = self._pending_answers.pop(slot_id)
            future.set_result(answer)
```

## Data Flow Examples

### Scenario A: Simple Response (No Elicitation)

```
T+0: Sim Agent calls talk_to_target("Device TB-500 has error E-42")
     → Controller creates Slot-1
     → Returns immediately: {"slot_id": "abc123", "status": "pending"}

T+0.1: Sim Agent starts parallel tasks:
       - search_documentation("TB-500")
       - query_error_code("E-42")

T+1: Target Agent processes, begins response
     → Controller catches PartDeltaEvent
     → Directive: {"type":"response", "slot_id":"abc123", "response_delta":"Based on..."}

T+2: Sim Agent polls get_response("abc123")
     → Returns: {"status":"responding", "partial":"Based on..."}

T+5: Target Agent completes
     → Directive: {"type":"completed", "slot_id":"abc123"}

T+5.1: Sim Agent calls get_response("abc123")
       → Returns: {"status":"completed", "response":"Based on error E-42..."}
```

### Scenario B: With Elicitation

```
T+0: talk_to_target("Machine is making weird noise")
     → Slot-2 created, status="pending"

T+1: Target Agent calls "question" tool
     → SimulationInputProvider.get_elicitation() invoked
     → Blocked waiting for answer
     → Directive: {"type":"elicitation", "questions":[...]}

T+2: Sim Agent receives directive (via poll or callback)
     → Sim Agent reviews questions, decides how much to reveal
     → Sim Agent queries "hidden_info" source

T+5: Sim Agent calls provide_answer("abc123", {"answer": "3 months"})
     → ToolProvider → InputProvider.receive_answer()
     → Unblocks get_elicitation()
     → Target Agent receives answer, continues

T+5.1: (Nested elicitation possible)
       Target asks follow-up → Another elicitation cycle

T+10: Target completes → Directive: completed
```

## State Diagram

```
                    ┌───────────┐
       create_slot  │           │
         ─────────► │  PENDING  │
                    │           │
                    └─────┬─────┘
                          │ run_stream starts
                          ▼
                    ┌───────────┐
       PartDelta    │           │
         ◄──────────┤ RESPONDING│◄─────┐
                    │           │      │ provide_answer
                    └─────┬─────┘      │ completes
                          │            │
                          │ ToolCall   │
                          │ (question) │
                          ▼            │
                    ┌───────────┐      │
      get_response  │           │      │
         ◄──────────┤ ELICITING │──────┘
                    │           │ (InputProvider
                    └───────────┘  unblocked)
                          │
                          │ StreamComplete
                          ▼
                    ┌───────────┐
                    │           │
                    │ COMPLETED │
                    │           │
                    └───────────┘
```

## Tools for Sim Agent

```python
# Core Tools

@tool
def talk_to_target(message: str) -> dict:
    """
    Initiate conversation with Target Agent.
    Non-blocking - returns immediately with slot_id.
    """
    return {
        "slot_id": "uuid",
        "status": "pending",
        "message": "Conversation initiated"
    }

@tool
def get_response(slot_id: str, wait: bool = True, timeout: float = 30.0) -> dict:
    """
    Fetch current result from conversation slot.
    
    Returns status:
    - "responding": Target is generating response (response_text available)
    - "elicitation": Target needs answer (questions available)  
    - "completed": Conversation finished
    - "error": Something went wrong
    """
    return {
        "status": "elicitation",
        "response_text": "...",
        "questions": [...]
    }

@tool  
def provide_answer(slot_id: str, answers: dict) -> dict:
    """
    Provide answer to Target Agent's elicitation.
    This unblocks InputProvider and allows Target to continue.
    """
    return {
        "status": "accepted",
        "message": "Answer provided"
    }

# Parallel Research Tools (examples)

@tool
def search_documentation(query: str) -> dict:
    """Search device documentation in parallel"""
    pass

@tool
def query_knowledge_base(device_id: str) -> dict:
    """Query historical data about device"""
    pass
```

## Configuration

```yaml
# simulation.yml
agents:
  engineer_sim:
    type: native
    model: openai:gpt-4o
    system_prompt: |
      You are a repair engineer using an async diagnostic system.
      
      Workflow:
      1.talk_to_target(description) → get slot_id
      2. [Parallel] Search docs, gather context
      3. get_response(slot_id) → check status
      4. If elicitation: decide how much to reveal
      5. provide_answer(slot_id, answer)
      6. Repeat until completed
    
    tools:
      - type: simulation_async
        target: diagnosis_agent
        elicitation_tools: ["question", "confirm", "select"]
      
      - name: search_documentation
        enabled: true
      
      - name: query_knowledge_base
        enabled: true
```

## Implementation Notes

### Thread Safety

- Each `SimulationSlot` is independent
- `InputProvider` assumes 1v1 (one active slot at a time per provider instance)
- For multi-slot, need `slot_id` in InputProvider context

### Error Handling

```python
# Target Agent crashes
try:
    async for event in target.run_stream(...):
        ...
except Exception as e:
    slot.status = "error"
    slot.error = str(e)
    await controller.notify_error(slot_id, e)
```

### Cleanup

```python
async def cleanup_slot(self, slot_id: str):
    """Remove completed/error slots after TTL"""
    slot = self._slots.pop(slot_id, None)
    if slot and slot.task:
        slot.task.cancel()
```

## Comparison with Other Architectures

| Aspect | Tool Detection (break) | InputProvider (blocking) | **Async Producer-Consumer** |
|--------|----------------------|-------------------------|---------------------------|
| Sim Agent blocked? | Yes | Yes | **No** |
| Parallel work? | No | No | **Yes** |
| Real elicitation? | No (interrupted) | Yes | **Yes** |
| Complexity | Low | Medium | **Higher** |
| Use case | Simple 1v1 | Accurate simulation | **Realistic multi-tasking** |

## Open Questions

1. **Multi-slot support**: One Sim Agent testing multiple Target Agents simultaneously?
2. **Directive delivery**: Poll vs Callback vs WebSocket?
3. **Response streaming**: Sim Agent sees streaming output or only final result?
4. **Nested elicitation depth**: Limit to prevent infinite loops?
5. **Cancel propagation**: How does Sim Agent cancel a long-running Target Agent?
