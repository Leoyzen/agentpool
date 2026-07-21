# Cooperative Multitasking Simulation Pattern

## Overview

This document describes the **cooperative multitasking** pattern for AgentPool simulation framework, inspired by the subagent's `async_mode=False` execution model.

## Design Philosophy

**"Not parallel, not serial, but cooperative"**

- **Not Parallel**: Single event loop, no true concurrency
- **Not Serial**: Sim Agent doesn't just wait; it observes and decides in real-time
- **Cooperative**: Sim and Target yield control through async event stream

## Key Insight from Subagent Pattern

The AgentPool subagent uses **cooperative multitasking** via asyncio:

```python
async for event in subagent.run_stream(prompt):
    # Parent "waits" but receives real-time events
    # Control naturally yields at each await point
    await ctx.events.emit_event(SubAgentEvent(...))
```

This provides **interleaved execution** where parent and child take turns within the same event loop.

## Architecture

### Control Flow Model

```
┌────────────────────────────────────────────────────────────┐
│  Event Loop (Single Thread)                                │
│                                                            │
│  Time ───────────────────────────────────────────────►     │
│                                                            │
│  Sim:  [think]──[act]─────►[observe]──[decide]──[act]      │
│              │                  │                        │
│              │    yield          │   yield                │
│              ▼                  ▼                        │
│  Target:     [process]─────────►[output]──►[ask]────────   │
│                                                            │
│  Note: No parallelism, natural alternation via await       │
└────────────────────────────────────────────────────────────┘
```

### Core Components

#### 1. SimEvent Stream

```python
@dataclass
class SimEvent:
    """Events that Sim Agent observes from Target"""
    type: Literal[
        "text_delta",        # Target generating response
        "thinking_delta",    # Target's reasoning (if exposed)
        "tool_start",        # Target calling non-elicitation tool
        "elicitation_start", # Target asking for input
        "elicitation_end",   # Elicitation answered, continuing
        "complete",          # Target done
        "error",             # Something went wrong
    ]
    timestamp: datetime
    
    # Type-specific fields
    delta: str | None = None
    questions: list[Question] | None = None
    tool_name: str | None = None
    response: str | None = None
    error_message: str | None = None
```

#### 2. Stream-Based Tool Interface

```python
class CooperativeSimulationProvider(ResourceProvider):
    """
    Provides cooperative multitasking simulation
    """
    
    def __init__(self, target: Agent):
        self.target = target
        self._intervention_future: asyncio.Future | None = None
    
    @tool
    async def talk_to_target(
        self,
        ctx: AgentContext,
        message: str,
    ) -> AsyncIterator[SimEvent]:
        """
        Start conversation, return event stream for real-time observation
        
        This is the KEY difference from blocking approach:
        - Returns immediately with AsyncIterator
        - Sim Agent consumes events as Target produces them
        - Sim can intervene at any point
        """
        # Setup InputProvider for injection
        self._input_provider = SimulationInputProvider(self)
        self.target.set_input_provider(self._input_provider)
        
        # Transform Target's stream into SimEvent stream
        target_stream = self.target.run_stream(message)
        
        async for event in self._transform(target_stream):
            yield event
            
            # Check if intervention needed
            if event.type == "elicitation_start":
                # PAUSE: Wait for Sim Agent to decide
                answer = await self._wait_intervention()
                
                # Inject answer and continue
                self._input_provider.inject_answer(answer)
                
                yield SimEvent(
                    type="elicitation_end",
                    timestamp=datetime.now(),
                )
    
    @tool
    async def provide_intervention(
        self,
        ctx: AgentContext,
        answer: dict,
    ) -> dict:
        """
        Sim Agent intervenes during elicitation
        
        This unblocks the _wait_intervention() in talk_to_target
        """
        if self._intervention_future and not self._intervention_future.done():
            self._intervention_future.set_result(answer)
            return {"status": "accepted"}
        return {"status": "error", "message": "No pending intervention"}
    
    async def _wait_intervention(self) -> dict:
        """Cooperatively pause stream until Sim intervenes"""
        self._intervention_future = asyncio.Future()
        try:
            # This await YIELDS control back to event loop
            # Sim Agent can run other code, then call provide_intervention
            return await self._intervention_future
        finally:
            self._intervention_future = None
```

#### 3. InputProvider Bridge

```python
class SimulationInputProvider(InputProvider):
    """
    Receives elicitation requests from Target
    Bridges to cooperative stream
    """
    
    def __init__(self, provider: CooperativeSimulationProvider):
        self.provider = provider
        self._injected_answer: dict | None = None
    
    async def get_elicitation(self, params: ElicitRequestParams) -> ElicitResult:
        """
        Target needs input. This is called during run_stream.
        
        In cooperative mode:
        1. Don't block with Future/Queue
        2. Return immediately with "retry" or use injected answer
        3. The caller (CooperativeSimulationProvider) handles the coordination
        """
        if self._injected_answer:
            answer = self._injected_answer
            self._injected_answer = None
            return ElicitResult(action="accept", content=answer)
        
        # This should not happen in cooperative mode
        # The provider should have injected answer before we get here
        return ElicitResult(action="decline")
    
    def inject_answer(self, answer: dict):
        """Called by provider when Sim intervenes"""
        self._injected_answer = answer
```

### Usage Pattern

```python
class SimAgent:
    """Example Sim Agent using cooperative pattern"""
    
    async def diagnose(self, scenario: Scenario):
        # 1. Start conversation - GET STREAM, not result
        event_stream = self.tools.talk_to_target(
            f"Device {scenario.device_id} reports: {scenario.symptoms}"
        )
        
        # 2. Process events cooperatively
        collected_output = []
        
        async for event in event_stream:
            match event.type:
                case "text_delta":
                    # Observe Target's response in real-time
                    collected_output.append(event.delta)
                    
                    # Sim can do light processing while observing
                    if len(collected_output) > 100:
                        # Monitor for early hints in response
                        self._analyze_partial_response(collected_output)
                
                case "thinking_delta":
                    # If Target exposes thinking, Sim can see reasoning
                    pass
                
                case "tool_start":
                    # Target using non-elicitation tools
                    logger.info(f"Target using tool: {event.tool_name}")
                
                case "elicitation_start":
                    # TARGET IS ASKING! Time to decide
                    logger.info(f"Target asks: {event.questions}")
                    
                    # Sim Agent's decision logic
                    decision = await self._decide_what_to_reveal(
                        questions=event.questions,
                        collected_output=collected_output,
                        hidden_info=scenario.hidden_info,
                        strategy=self.current_strategy,
                    )
                    
                    # INTERVENE with answer
                    await self.tools.provide_intervention(decision.answers)
                
                case "elicitation_end":
                    # Target received answer, continuing
                    logger.info("Answer accepted, Target continues")
                
                case "complete":
                    # Conversation finished
                    return DiagnosticResult(
                        target_response=event.response,
                        revealed_info=self._get_revealed_info(),
                        turn_count=self._get_turn_count(),
                    )
                
                case "error":
                    raise SimulationError(event.error_message)
```

## Comparison with Other Patterns

| Pattern | Execution | Sim Agent State | Intervene | Complexity |
|---------|-----------|-----------------|-----------|------------|
| **Tool Detection** | Interrupt/restart | Blocked then restart | Tool only | Low |
| **InputProvider Blocking** | True blocking | Fully blocked | Any point | Medium |
| **Cooperative Stream** | **Interleaved** | **Observing/Deciding** | **Any point** | **Medium** |

## Key Benefits

1. **Real-time Observation**: Sim sees Target's response as it's generated
2. **Strategic Intervention**: Sim can analyze partial output before deciding
3. **Natural Flow**: AsyncIterator feels natural in Python async code
4. **Cooperative Yield**: `await` points provide natural control handoff

## Implementation Notes

### Challenge: InputProvider Timing

The tricky part: `InputProvider.get_elicitation()` is called synchronously from pydantic-ai's tool execution, but we want cooperative control.

**Solution**: 
- InputProvider returns "decline" or uses pre-injected answer
- The real coordination happens via `provide_intervention` tool
- Provider manages the bridge between streaming and InputProvider

### State Management

```python
class CooperativeSimulationProvider:
    def __init__(self):
        self._state = "idle"  # idle | streaming | waiting_intervention
        self._current_stream: AsyncIterator | None = None
```

### Error Handling

```python
async for event in self._transform(target_stream):
    try:
        yield event
    except Exception as e:
        yield SimEvent(type="error", error_message=str(e))
        return
```

## Configuration

```yaml
agents:
  engineer_sim:
    type: native
    model: openai:gpt-4o
    
    toolsets:
      - type: cooperative_simulation
        target: diagnosis_agent
        
        # Configure when Sim can intervene
        intervention_points:
          - elicitation_start    # Main point: when Target asks
          - tool_start          # Optional: observe tool usage
        
        # Whether to expose Target's thinking
        expose_target_reasoning: false
```

## Relation to Subagent Pattern

This pattern **reuses subagent's cooperative execution semantics**:

```python
# Subagent pattern (built-in)
async for event in ctx.tools.task(agent="other", prompt="..."):
    # Parent sees child's events in real-time
    pass

# Our pattern (simulation-specific)
async for event in ctx.tools.talk_to_target(message="..."):
    # Sim sees Target's events in real-time
    # Plus can intervene on elicitation
    pass
```

The difference: subagent wraps events in `SubAgentEvent`, our pattern uses `SimEvent` with simulation-specific semantics.

## Open Questions

1. Can/should Sim intervene at non-elicitation points?
2. How to handle nested elicitation (question within question)?
3. Should we expose Target's tool calls to Sim?
4. How to integrate with trajectory recording?
