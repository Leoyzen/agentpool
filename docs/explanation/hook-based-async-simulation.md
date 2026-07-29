# Hook-Based Async Simulation Architecture

## Overview

Event-driven simulation where Target Agent notifies Sim Agent via hooks when it completes or needs input.

## Core Flow

```
Step 1: Sim Agent initiates
┌─────────────────┐    talk_to_target(msg)     ┌──────────────────┐
│                 │ ─────────────────────────► │                  │
│   Sim Agent     │                            │   Simulation     │
│                 │ ◄───────────────────────── │   Orchestrator   │
│                 │    task_id: "task-123"     │                  │
└─────────────────┘                            └────────┬─────────┘
                                                        │
                                                        │ create background task
                                                        ▼
                                              ┌──────────────────┐
                                              │  Target Agent    │
                                              │  (executing)     │
                                              └──────────────────┘

Step 2: Parallel execution (cooperative)
┌─────────────────┐                            ┌──────────────────┐
│   Sim Agent     │  ┌────────────────┐        │   Target Agent   │
│                 │  │ doing work     │        │                  │
│   (continues)   │  │ - search docs  │        │   (executing)    │
│                 │  │ - query KB     │        │                  │
│                 │  └────────────────┘        │                  │
└─────────────────┘                            └──────────────────┘

Step 3: Target completes or elicits
┌─────────────────┐                            ┌──────────────────┐
│                 │ ◄─── post_run_hook() ───── │                  │
│   Sim Agent     │      or                    │   Target Agent   │
│   receives      │      post_tool_use()       │   calls question │
│   notification  │                            │   tool           │
│                 │    Message:                │                  │
│                 │    "Task task-123 ready"   │                  │
└─────────────────┘                            └──────────────────┘

Step 4: Sim Agent retrieves and responds
┌─────────────────┐    get_background_task()   ┌──────────────────┐
│                 │ ─────────────────────────► │                  │
│   Sim Agent     │                            │   Orchestrator   │
│                 │ ◄───────────────────────── │                  │
│                 │    result: {               │                  │
│                 │      status: "elicitation",│                  │
│                 │      questions: [...]      │                  │
│                 │    }                       │                  │
└─────────────────┘                            └──────────────────┘
                                                        │
                                                        ▼
                                              ┌──────────────────┐
│                 │    answer_elicitation()  │                  │
│   Sim Agent     │ ─────────────────────────► │   Orchestrator   │
│                 │    (answer)                │   forwards       │
│                 │                            │   to Target      │
└─────────────────┘                            └──────────────────┘
                                                        │
                                                        ▼
                                              ┌──────────────────┐
                                              │  Target Agent    │
                                              │  (resumes with   │
                                              │   answer)        │
                                              └──────────────────┘
```

## Key Components

### 1. Task Registry

```python
class SimulationTaskRegistry:
    """
    Central registry for background simulation tasks
    """
    
    def __init__(self):
        self._tasks: dict[str, SimulationTask] = {}
        self._notification_callbacks: dict[str, Callable] = {}
    
    def create_task(
        self,
        target: Agent,
        sim_agent: Agent,  # Who to notify
        message: str,
    ) -> str:
        task_id = str(uuid4())
        task = SimulationTask(
            task_id=task_id,
            target=target,
            sim_agent=sim_agent,
            status="running",
            message=message,
            result=None,
        )
        self._tasks[task_id] = task
        return task_id
    
    def register_callback(self, task_id: str, callback: Callable):
        """Register callback to notify Sim Agent"""
        self._notification_callbacks[task_id] = callback
    
    def complete_task(self, task_id: str, result: TaskResult):
        """Called by hook when Target completes or elicits"""
        task = self._tasks[task_id]
        task.status = result.status
        task.result = result
        
        # Notify Sim Agent
        if callback := self._notification_callbacks.get(task_id):
            asyncio.create_task(callback(task_id, result))
    
    def get_task(self, task_id: str) -> SimulationTask:
        return self._tasks[task_id]
```

### 2. Notification Hook

```python
class SimulationNotificationHook(Hook):
    """
    Hook that notifies Sim Agent when Target Agent completes or elicits
    """
    
    def __init__(self, registry: SimulationTaskRegistry):
        super().__init__(event="post_run")  # or "post_tool_use"
        self.registry = registry
    
    async def execute(self, input_data: HookInput, env=None) -> HookResult:
        """
        Called when Target Agent completes a run or tool use
        """
        task_id = self._extract_task_id(input_data)
        
        # Check if this was a simulation task
        if task_id and task_id in self.registry._tasks:
            # Create result
            result = TaskResult(
                status="completed" if input_data["event"] == "post_run" else "elicitation",
                response=input_data.get("result"),
            )
            
            # Notify!
            self.registry.complete_task(task_id, result)
        
        return HookResult(decision="allow")
```

### 3. Orchestrator

```python
class SimulationOrchestrator:
    """
    Central coordinator between Sim Agent and Target Agent
    """
    
    def __init__(self):
        self.registry = SimulationTaskRegistry()
        self._pending_answers: dict[str, asyncio.Future] = {}
    
    async def create_task(
        self,
        target: Agent,
        sim_agent: Agent,
        message: str,
    ) -> str:
        """
        Create a background simulation task
        """
        task_id = self.registry.create_task(target, sim_agent, message)
        
        # Register callback to notify Sim Agent
        self.registry.register_callback(
            task_id,
            self._on_task_complete,
        )
        
        # Start Target Agent with hooks
        asyncio.create_task(
            self._run_target_agent(task_id, target, message)
        )
        
        return task_id
    
    async def _run_target_agent(
        self,
        task_id: str,
        target: Agent,
        message: str,
    ):
        """
        Run Target Agent with notification hooks installed
        """
        # Install hooks before running
        hook = SimulationNotificationHook(self.registry)
        target.hooks.post_run.append(hook)
        target.hooks.post_tool_use.append(hook)
        
        # Run with InputProvider for elicitation
        result = await target.run(
            message,
            input_provider=SimulationOrchestratorInputProvider(self, task_id),
        )
        
        # Notify completion
        self.registry.complete_task(
            task_id,
            TaskResult(status="completed", response=result),
        )
    
    async def _on_task_complete(self, task_id: str, result: TaskResult):
        """
        Callback: Notify Sim Agent that task is ready
        """
        task = self.registry.get_task(task_id)
        
        # Send message to Sim Agent's conversation
        # This will be the "notification" that task is ready
        await task.sim_agent.inject_message(
            f"[Background task {task_id} completed]\n"
            f"Status: {result.status}\n"
            f"Use get_background_task('{task_id}') to retrieve details."
        )
    
    def get_task_result(self, task_id: str) -> TaskResult:
        """Get task result (called by Sim Agent)"""
        task = self.registry.get_task(task_id)
        return task.result
    
    async def provide_answer(self, task_id: str, answer: dict):
        """Provide answer to pending elicitation"""
        if future := self._pending_answers.get(task_id):
            future.set_result(answer)
```

### 4. InputProvider for Elicitation

```python
class SimulationOrchestratorInputProvider(InputProvider):
    """
    InputProvider that blocks Target Agent until Sim Agent provides answer
    """
    
    def __init__(self, orchestrator: SimulationOrchestrator, task_id: str):
        self.orchestrator = orchestrator
        self.task_id = task_id
    
    async def get_elicitation(self, params: ElicitRequestParams) -> ElicitResult:
        """
        Target Agent calls this when it needs input
        
        Strategy:
        1. Mark task as "elicitation"
        2. Notify Sim Agent via hook
        3. Block waiting for answer
        4. Return answer when provided
        """
        # Create future for answer
        future = asyncio.Future()
        self.orchestrator._pending_answers[self.task_id] = future
        
        # Notify Sim Agent via hook mechanism
        task = self.orchestrator.registry.get_task(self.task_id)
        await self.orchestrator._on_task_complete(
            self.task_id,
            TaskResult(
                status="elicitation",
                questions=params,
            ),
        )
        
        # Block until Sim Agent provides answer
        try:
            answer = await asyncio.wait_for(future, timeout=300.0)
            return ElicitResult(action="accept", content=answer)
        except asyncio.TimeoutError:
            return ElicitResult(action="decline")
```

### 5. Tools for Sim Agent

```python
class SimulationToolProvider(ResourceProvider):
    """Tools for Sim Agent to use"""
    
    def __init__(self, orchestrator: SimulationOrchestrator, target: Agent):
        self.orchestrator = orchestrator
        self.target = target
    
    @tool
    async def talk_to_target(
        self,
        ctx: AgentContext,
        message: str,
    ) -> dict:
        """
        Start a background conversation with Target Agent
        
        Returns immediately with task_id
        """
        task_id = await self.orchestrator.create_task(
            target=self.target,
            sim_agent=ctx.agent,  # Current agent (Sim)
            message=message,
        )
        
        return {
            "task_id": task_id,
            "status": "started",
            "message": f"Background task {task_id} started. You'll be notified when ready.",
        }
    
    @tool
    async def get_background_task(
        self,
        ctx: AgentContext,
        task_id: str,
    ) -> dict:
        """
        Get result of a background task
        
        Call this after receiving notification
        """
        result = self.orchestrator.get_task_result(task_id)
        
        return {
            "task_id": task_id,
            "status": result.status,  # "completed" | "elicitation"
            "response": result.response,
            "questions": result.questions if result.status == "elicitation" else None,
        }
    
    @tool
    async def answer_elicitation(
        self,
        ctx: AgentContext,
        task_id: str,
        answers: dict,
    ) -> dict:
        """
        Answer Target Agent's elicitation
        """
        await self.orchestrator.provide_answer(task_id, answers)
        
        return {
            "status": "submitted",
            "message": "Answer submitted. Target Agent will resume.",
        }
```

## Sim Agent Usage Example

```python
class SimAgent:
    """
    Example Sim Agent using hook-based async simulation
    """
    
    async def diagnose(self, scenario):
        # Step 1: Start conversation (returns immediately)
        start_result = self.tools.talk_to_target(
            f"Device {scenario.device_id} error: {scenario.error_code}"
        )
        task_id = start_result["task_id"]
        
        # Step 2: Do other work while Target processes
        docs = await self.search_docs(scenario.device_id)
        context = await self.query_history(scenario.device_id)
        
        # Step 3: Wait for notification (Sim Agent processes normally)
        # In hook-based design, notification comes as message injection
        # Sim Agent just continues its normal flow
        
        # Step 4: Get result (this might be called after notification)
        result = self.tools.get_background_task(task_id)
        
        while result["status"] == "elicitation":
            # Step 5: Decide and answer
            answers = self.decide(
                questions=result["questions"],
                docs=docs,
                context=context,
            )
            
            self.tools.answer_elicitation(task_id, answers)
            
            # Step 6: Continue working while Target processes answer
            await self.update_notes(f"Answered: {answers}")
            
            # Step 7: Get next result (notification will come)
            result = self.tools.get_background_task(task_id)
        
        # Completed
        return result["response"]
```

## System Prompt for Sim Agent

```yaml
agents:
  engineer_sim:
    type: native
    model: openai:gpt-4o
    
    system_prompt: |
      You are a repair engineer diagnosing equipment issues.
      
      Workflow:
      1. talk_to_target(description) → Returns task_id
      2. While waiting, research: search_docs(), query_history()
      3. You'll receive notification when Target Agent is ready
      4. get_background_task(task_id) → Check status
         - If "completed": Done
         - If "elicitation": Target is asking questions
      5. If elicitation: decide what to reveal, then answer_elicitation()
      6. Continue workflow until complete
      
      Strategy:
      - Don't reveal all information at once
      - Use your research to decide what to share
      - Continue working while waiting for Target
```

## Key Differences from Other Approaches

| Aspect | Tool Detection | Cooperative Stream | **Hook-Based** |
|--------|---------------|-------------------|----------------|
| **Sim Agent blocks?** | Yes | No (observes) | **No (works independently)** |
| **Target Agent** | Restarted each turn | Continuous stream | **Continuous with hooks** |
| **Notification** | Immediate return | Stream events | **Hook-based message** |
| **Intervention** | At tool call | Any time via stream | **Via answer_elicitation()** |
| **Architecture** | Simple | Complex stream | **Event-driven** |

## Hook Configuration

```yaml
# In Target Agent config
agents:
  diagnosis_agent:
    type: native
    model: claude-sonnet-4
    
    hooks:
      # These are auto-installed by SimulationOrchestrator
      post_run:
        - type: simulation_notification
      post_tool_use:
        - type: simulation_notification
          matcher: "question|confirm|select"  # Only for elicitation tools
```

## Implementation Notes

1. **Hook Order**: Hooks run in parallel, so notification is fast
2. **Message Injection**: Sim Agent needs `inject_message()` capability
3. **Task Lifetime**: Tasks stored in registry until explicitly cleaned up
4. **Error Handling**: Target errors also trigger notification with error status

## Open Questions

1. Should Sim Agent poll or truly wait for notification?
2. How to handle multiple concurrent tasks per Sim Agent?
3. Task cleanup strategy?
4. How does Sim Agent's "normal flow" know when to check for results?
