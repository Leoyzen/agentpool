# Async Simulation Pattern (Non-Streaming, Non-Parallel)

## Core Concept

**"异步执行"** - Sim Agent 启动 Target Agent 后立即返回 Future，不阻塞，但也不并行。

```
┌─────────────────────────────────────────────────────────────┐
│  Sim Agent (async def)                                       │
│                                                              │
│  1. future = await talk_to_target("...")  ───────────────►  │
│     Returns immediately: PendingResponse                     │
│     ┌────────────────────────────────────────┐              │
│     │  status: pending                       │              │
│     │  future: asyncio.Future                │              │
│     │  wait(): blocks until done             │              │
│     └────────────────────────────────────────┘              │
│                                                              │
│  2. Sim Agent 继续执行其他逻辑（不阻塞）                      │
│     ┌─────────────────────────────────┐                     │
│     │ search_docs()                   │                     │
│     │ query_kb()                      │  ◄── 单事件循环内   │
│     │ analyze_scenario()              │      顺序执行       │
│     └─────────────────────────────────┘                     │
│                                                              │
│  3. 当需要 Target 结果时：                                    │
│     result = await pending_response.wait()                  │
│     │                                                        │
│     │  ┌─────────────┐ 如果 Target 未完成                     │
│     └──┤  挂起等待   │─────► 事件循环调度其他任务             │
│        └─────────────┘            │                         │
│                                   ▼                         │
│                         Target Agent 继续执行               │
│                                   │                         │
│                                   ▼                         │
│                              完成时 resolve                 │
│                                   │                         │
│     result ◄──────────────────────┘                         │
│     status: completed | elicitation                         │
│                                                              │
│  4. 如果是 elicitation：                                     │
│     provide_answer() ──► resolve future                     │
│     goto step 1 继续下一轮                                  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**关键**：单事件循环，没有并行任务，但使用 asyncio.Future 实现挂起/恢复。

## Implementation

### Simplified Core

```python
@dataclass
class PendingResponse:
    """
    异步句柄，类似 asyncio.Task 但语义更清晰
    """
    status: Literal["pending", "completed", "elicitation", "error"]
    _future: asyncio.Future[Response]
    
    async def wait(self, timeout: float | None = None) -> Response:
        """
        等待完成，挂起当前协程但不阻塞事件循环
        """
        try:
            return await asyncio.wait_for(self._future, timeout=timeout)
        except asyncio.TimeoutError:
            return Response(status="timeout")
    
    def done(self) -> bool:
        return self._future.done()


class AsyncSimulationProvider(ResourceProvider, InputProvider):
    """
    异步仿真：Future-based，无流式，无并行
    """
    
    def __init__(self, target: Agent):
        self.target = target
        self._current_future: asyncio.Future | None = None
    
    @tool
    def talk_to_target(self, ctx: AgentContext, message: str) -> PendingResponse:
        """
        启动对话，立即返回 PendingResponse（不阻塞）
        """
        future = asyncio.Future()
        
        # 启动 Target Agent，但不 await！
        # 用 create_task 只是为了开始执行
        task = asyncio.create_task(
            self._run_target_agent(message, future)
        )
        
        # 立即返回句柄
        return PendingResponse(
            status="pending",
            _future=future,
        )
    
    async def _run_target_agent(
        self, 
        message: str, 
        future: asyncio.Future,
    ) -> None:
        """
        Target Agent 的执行协程
        注意：这不是并行任务，只是协程！
        """
        try:
            async for event in self.target.run_stream(message):
                if isinstance(event, ToolCallStartEvent):
                    if self._is_elicitation_tool(event.tool_name):
                        # 追问！resolve future 并返回
                        future.set_result(Response(
                            status="elicitation",
                            questions=self._extract_questions(event),
                        ))
                        return  # 协程结束，但不是进程结束！
            
            # 正常完成
            future.set_result(Response(
                status="completed",
                response=self._get_full_response(),
            ))
            
        except Exception as e:
            future.set_exception(e)
    
    @tool  
    async def provide_answer(
        self, 
        ctx: AgentContext, 
        answer: dict,
    ) -> PendingResponse:
        """
        提供答案，继续对话，返回新的 PendingResponse
        """
        # 注入答案到 InputProvider
        self._inject_answer(answer)
        
        # 返回新的 future，继续等待
        future = asyncio.Future()
        asyncio.create_task(self._continue_with_answer(answer, future))
        
        return PendingResponse(status="pending", _future=future)
    
    # === InputProvider ===
    
    async def get_elicitation(self, params: ElicitRequestParams) -> ElicitResult:
        """
        Target Agent 调用此方法时会挂起
        等待 Sim Agent 调用 provide_answer 注入结果
        """
        # 不是阻塞等待，而是设置一个 Future，让 _run_target_agent 继续
        self._answer_future = asyncio.Future()
        answer = await self._answer_future  # 挂起协程
        return ElicitResult(action="accept", content=answer)
```

### Usage Example

```python
class SimAgent:
    async def diagnose(self, scenario):
        # 1. 启动对话，立即返回，不阻塞
        pending = self.tools.talk_to_target(
            f"Device {scenario.device_id} error: {scenario.error}"
        )
        
        # 2. 立即做其他事情
        docs = await self.search_docs(scenario.device_id)
        context = await self.query_history(scenario.device_id)
        
        # 3. 现在需要结果了，等待（挂起协程，非阻塞事件循环）
        result = await pending.wait()
        
        # 4. 处理结果
        while result.status == "elicitation":
            # 决策
            answer = self.decide(result.questions, docs, context)
            
            # 提供答案，继续
            pending = self.tools.provide_answer(answer)
            
            # 可以再做一些事情...
            await self.update_notes(f"Answered: {answer}")
            
            # 等待结果
            result = await pending.wait()
        
        return result.response
```

## Key Characteristics

| Aspect | Behavior |
|--------|----------|
| **Parallel** | ❌ No - Single event loop |
| **Streaming** | ❌ No - Return Future, not Iterator |
| **Blocking** | ❌ No - Returns immediately |
| **Async** | ✅ Yes - Future-based suspension |
| **Cooperative** | ✅ Yes - await yields control |

## Comparison

```python
# 串行（阻塞）
result = await talk_to_target("...")  # 阻塞直到完成
# 不能做其他事情

# 并行（真正的并发）
task = asyncio.create_task(talk_to_target("..."))  # 新任务
other_task = asyncio.create_task(other_work())     # 另一个任务
await asyncio.gather(task, other_task)  # 真正的并行执行

# 异步（你的需求）
pending = talk_to_target("...")  # 立即返回，不阻塞
# 做其他事情（同一事件循环，顺序执行）
result = await pending.wait()    # 挂起协程，等待完成
```

## Why No Streaming?

Because:
- `talk_to_target()` returns `PendingResponse` (Future wrapper)
- Not `AsyncIterator[Event]` (streaming)
- Sim Agent either:
  1. Does other work then `await pending.wait()`
  2. Or chains multiple operations
- No need to observe intermediate events

## Implementation Notes

1. **Single Event Loop**: All agents run in same loop
2. **No create_task for Parallelism**: `create_task` used only to start coroutine, not for parallel execution
3. **Future as Bridge**: Connects Sim Agent's `await` with Target Agent's completion
4. **InputProvider Bridge**: Target's elicitation → Future resolution → Sim's next step

## Configuration

```yaml
agents:
  engineer_sim:
    type: native
    model: openai:gpt-4o
    
    toolsets:
      - type: async_simulation
        target: diagnosis_agent
        
    system_prompt: |
      You are a repair engineer. 
      
      Workflow:
      1. pending = talk_to_target(description)  - Start conversation
      2. [Do research in parallel - but not parallel execution!]
         docs = await search_docs()
         history = await query_kb()
      3. result = await pending.wait()         - Get response
      4. While result.status == "elicitation":
           answer = decide(result.questions, docs, history)
           pending = provide_answer(answer)
           result = await pending.wait()
```

## Simplified vs Producer-Consumer

| Feature | Producer-Consumer (Earlier) | This Simplified Version |
|---------|---------------------------|------------------------|
| Slot Management | Yes | No |
| Directive Queue | Yes | No |
| Controller | Yes | No |
| Streaming Events | Optional | No |
| **Complexity** | High | **Low** |
| **Core Mechanism** | Queue + Events | **Future** |

This version is much simpler: just `Future` and `async/await`.
