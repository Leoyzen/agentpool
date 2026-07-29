# Native AgentPool Async Simulation (CustomEvent + queue_prompt)

## Core Concept

Use AgentPool's native mechanisms for async simulation:
- **CustomEvent**: Notify Sim Agent when Target completes/elicits
- **queue_prompt**: Send follow-up prompts to Sim Agent
- No external orchestrator, let AgentPool handle the coordination

## Architecture

```
Sim Agent (主控)                          Target Agent
    │                                          │
    │ talk_to_target(msg)                      │
    │─────────────────────────────────────────►│
    │ task_id: "abc123"                        │ 启动后台运行
    │◄─────────────────────────────────────────│
    │                                          │
    │ (Sim Agent 继续执行其他工作)               │ [处理中...]
    │                                          │
    │ (Target 调用 question tool)               │
    │                                          │
    │◄─── ctx.agent.emit_event(CustomEvent) ───│ 发射事件
    │     type: "simulation_elicitation"       │
    │     task_id: "abc123"                    │
    │     questions: [...]                     │
    │                                          │
    │ (Target 通过 InputProvider 阻塞等待)       │ [阻塞]
    │                                          │
    │◄─── ctx.agent.queue_prompt() ────────────│ 可选：添加提示
    │     "[Task abc123 needs answer]"         │
    │                                          │
    │ SimAgent 收到 CustomEvent                │
    │ 或看到 queue_prompt 的消息                │
    │                                          │
    │ answer_elicitation(task_id, answer)      │
    │─────────────────────────────────────────►│ 通过 Provider
    │                                          │ 注入答案
    │                                          │ [恢复执行]
    │...                                       │
    │◄─── CustomEvent: "simulation_complete" ──│ 完成
    │     task_id: "abc123"                    │
    │     response: "..."                      │
```

## Implementation

### 1. Custom Event Definition

```python
@dataclass
class SimulationEvent:
    """仿真专用事件，通过 CustomEvent 包装"""
    task_id: str
    event_type: Literal["started", "elicitation", "complete", "error"]
    payload: dict  # questions, response, or error info

# Usage:
# ctx.agent.emit_event(CustomEvent(
#     event_data=SimulationEvent(...),
#     event_type="simulation",
# ))
```

### 2. Simplified Provider (No Orchestrator)

```python
class NativeAsyncSimulationProvider(ResourceProvider, InputProvider):
    """
    利用 AgentPool 原生机制的异步仿真 Provider
    
    特点：
    - 不需要外部 Orchestrator
    - 使用 AgentPool 的事件系统
    - 使用 queue_prompt 进行通知
    """
    
    def __init__(self, target: Agent):
        self.target = target
        self._tasks: dict[str, TaskState] = {}
    
    @tool
    async def talk_to_target(
        self,
        ctx: AgentContext,
        message: str,
    ) -> dict:
        """
        启动仿真对话
        
        创建 task，启动 Target，立即返回
        """
        task_id = str(uuid4())
        
        # 保存任务状态
        self._tasks[task_id] = TaskState(
            task_id=task_id,
            status="running",
            sim_agent=ctx.agent,  # 引用 Sim Agent 用于后续通知
        )
        
        # 启动 Target Agent（不 await！）
        asyncio.create_task(
            self._run_target(ctx, task_id, message)
        )
        
        return {
            "task_id": task_id,
            "status": "running",
            "message": f"Task {task_id} started. "
                      f"Listen for CustomEvent or check get_task()."
        }
    
    async def _run_target(
        self,
        ctx: AgentContext,  # Sim Agent 的 context
        task_id: str,
        message: str,
    ):
        """在后台运行 Target Agent"""
        try:
            # 安装 InputProvider
            self._setup_input_provider(task_id)
            
            # 发射 "started" 事件
            ctx.agent.emit_event(CustomEvent(
                event_data=SimulationEvent(
                    task_id=task_id,
                    event_type="started",
                    payload={},
                ),
                event_type="simulation",
                source="native_async_simulation",
            ))
            
            # 运行 Target
            result = await self.target.run(message)
            
            # 完成！发射事件
            ctx.agent.emit_event(CustomEvent(
                event_data=SimulationEvent(
                    task_id=task_id,
                    event_type="complete",
                    payload={"response": str(result)},
                ),
                event_type="simulation",
                source="native_async_simulation",
            ))
            
            # 可选：queue_prompt 提醒 Sim Agent
            ctx.agent.queue_prompt(
                f"[Simulation Task {task_id} completed]"
            )
            
            self._tasks[task_id].status = "complete"
            
        except Exception as e:
            ctx.agent.emit_event(CustomEvent(
                event_data=SimulationEvent(
                    task_id=task_id,
                    event_type="error",
                    payload={"error": str(e)},
                ),
                event_type="simulation",
            ))
    
    # ========== Elicitation Handling ==========
    
    async def get_elicitation(self, params: ElicitRequestParams) -> ElicitResult:
        """
        Target Agent 调用此方法时会阻塞等待
        同时发射事件通知 Sim Agent
        """
        task_id = self._current_task_id
        task = self._tasks[task_id]
        
        # 创建 Future 等待答案
        answer_future = asyncio.Future()
        task.pending_answer = answer_future
        task.status = "elicitation"
        
        # 发射 elicitation 事件给 Sim Agent
        task.sim_agent.emit_event(CustomEvent(
            event_data=SimulationEvent(
                task_id=task_id,
                event_type="elicitation",
                payload={
                    "questions": params,
                    "message": f"Task {task_id} needs your input",
                },
            ),
            event_type="simulation",
            source="native_async_simulation",
        ))
        
        # 同时用 queue_prompt 添加可见提醒
        task.sim_agent.queue_prompt(
            f"The Target Agent in task {task_id} is asking: "
            f"{params.message}\n"
            f"Use answer_elicitation(task_id='{task_id}', answers=...) to respond."
        )
        
        # 阻塞等待 Sim Agent 回答
        try:
            answer = await asyncio.wait_for(answer_future, timeout=300.0)
            return ElicitResult(action="accept", content=answer)
        except asyncio.TimeoutError:
            return ElicitResult(action="decline")
    
    @tool
    async def answer_elicitation(
        self,
        ctx: AgentContext,
        task_id: str,
        answers: dict,
    ) -> dict:
        """Sim Agent 回答问题"""
        task = self._tasks.get(task_id)
        if not task or not task.pending_answer:
            return {"status": "error", "message": "No pending elicitation"}
        
        # 解除 get_elicitation 的阻塞
        task.pending_answer.set_result(answers)
        task.pending_answer = None
        
        return {"status": "submitted"}
    
    @tool
    def get_task(self, ctx: AgentContext, task_id: str) -> dict:
        """查询任务状态"""
        task = self._tasks.get(task_id)
        if not task:
            return {"status": "not_found"}
        
        return {
            "task_id": task_id,
            "status": task.status,
            "has_pending_answer": task.pending_answer is not None,
        }
```

### 3. Sim Agent Event Handler

```python
class SimAgent:
    """
    Sim Agent 处理 CustomEvent 的方式
    """
    
    async def on_custom_event(self, event: CustomEvent):
        """
        监听 CustomEvent 回调
        需要 AgentPool 支持事件处理器注册
        """
        if event.event_type != "simulation":
            return
        
        sim_event: SimulationEvent = event.event_data
        task_id = sim_event.task_id
        
        match sim_event.event_type:
            case "started":
                logger.info(f"Task {task_id} started")
            
            case "elicitation":
                questions = sim_event.payload["questions"]
                logger.info(f"Task {task_id} needs input: {questions}")
                
                # Sim Agent 决策并回答
                answer = await self.decide_and_answer(questions)
                await self.tools.answer_elicitation(task_id, answer)
            
            case "complete":
                response = sim_event.payload["response"]
                logger.info(f"Task {task_id} completed: {response}")
            
            case "error":
                error = sim_event.payload["error"]
                logger.error(f"Task {task_id} error: {error}")
    
    async def run_with_events(self):
        """
        使用事件监听运行 Sim Agent
        """
        # 注册事件处理器
        self.event_handlers.append(self.on_custom_event)
        
        # 正常运行
        # 当 Target 发射 CustomEvent 时，会触发回调
        result = await self.run("Diagnose the device")
```

### 4. Alternative: Queue-Prompt-Based (No Events)

如果 Sim Agent 不支持事件监听，完全用 queue_prompt：

```python
class QueuePromptSimulationProvider(ResourceProvider, InputProvider):
    """
    只用 queue_prompt，不用 CustomEvent
    更简单的实现
    """
    
    async def get_elicitation(self, params: ElicitRequestParams) -> ElicitResult:
        task = self._current_task
        
        # 直接 queue_prompt 给 Sim Agent
        # Sim Agent 会在下一轮看到这条消息
        task.sim_agent.queue_prompt(
            f"[SIMULATION INTERVENTION REQUIRED]\n"
            f"Task: {task.task_id}\n"
            f"Status: ELICITATION\n"
            f"Question: {params.message}\n"
            f"\n"
            f"Call answer_elicitation(task_id='{task.task_id}', "
            f"answers={{...}}) to continue."
        )
        
        # 阻塞等待
        answer_future = asyncio.Future()
        task.pending_answer = answer_future
        answer = await answer_future
        
        return ElicitResult(action="accept", content=answer)
```

## Configuration

```yaml
agents:
  engineer_sim:
    type: native
    model: openai:gpt-4o
    
    # Event handlers (if supported)
    event_handlers:
      - type: custom_event
        filter: "event_type == 'simulation'"
        action: "handle_simulation_event"
    
    toolsets:
      - type: native_async_simulation
        target: diagnosis_agent
    
    system_prompt: |
      You are a repair engineer.
      
      When Target Agent asks questions:
      1. You'll receive a CustomEvent or queue_prompt notification
      2. Review the questions and decide what to reveal
      3. Call answer_elicitation(task_id, answers) to respond
      
      You can continue working while waiting for Target.

  diagnosis_agent:
    type: native
    model: claude-sonnet-4
    
    # Has "question" tool for elicitation
    tools:
      - name: question
        enabled: true
```

## Comparison with Hook-Based

| Aspect | Hook-Based | **CustomEvent + queue_prompt** |
|--------|-----------|-------------------------------|
| **Mechanism** | AgentPool Hooks | **AgentPool Native Events + Prompt Queue** |
| **Notification** | Hook callbacks | **CustomEvent emission** |
| **Sim Agent Control** | External orchestrator | **AgentPool manages flow** |
| **Intervention** | Hook-based | **InputProvider + queue_prompt** |
| **Implementation** | Complex Orchestrator | **Simple Provider** |
| **Coupling** | Loose (hooks) | **Tight (native mechanisms)** |

## Benefits

1. **Native Integration**: Uses AgentPool's built-in event and prompt systems
2. **Simpler**: No external orchestrator or task registry
3. **Natural Flow**: queue_prompt 让 Sim Agent 在下一轮处理
4. **Flexible**: Can use events or just queue_prompt

## Limitations

1. Sim Agent needs to support:
   - CustomEvent listeners, OR
   - Process queue_prompt messages
2. More coupled to AgentPool internals
3. Less control over timing (depends on Agent's run loop)
