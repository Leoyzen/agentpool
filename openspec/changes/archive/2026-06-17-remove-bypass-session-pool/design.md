## Context

当前架构中，`BaseAgent.run_stream()` 和 `BaseAgent.run()` 在路由到 SessionPool 之前会检查 `_should_bypass_session_pool()`：

```
BaseAgent.run_stream()
  ├── _should_bypass_session_pool() == True  → _run_stream_direct()
  └── _should_bypass_session_pool() == False → SessionPool.run_stream() → TurnRunner.run_loop()
                                               → turn_lock → _run_turn_unlocked()
                                               → _bypass_session_pool.set(True)
                                               → agent._run_stream_once()
```

`_bypass_session_pool` ContextVar 由 `_run_turn_unlocked()` 设置，目的是防止嵌套调用死锁：
- Turn A 持有 `turn_lock`
- Turn A 内部通过 tool call / subagent delegation 调用 `agent.run_stream()`
- 如果不 bypass，`run_stream()` 会路由到 `SessionPool.run_stream()` → 尝试获取同一个 `turn_lock`
- `asyncio.Lock` 不可重入 → DEADLOCK

这条 bypass 路径带来了三个问题：
1. **二元执行路径**：agent 的执行要么走 SessionPool（有 EventBus、auto-resume、并发控制），要么不走（直接执行）。这增加了测试和调试的复杂度
2. **隐式全局状态**：`ContextVar` 是协程级别的隐式状态，跨 await 点传播，容易在 background task 中丢失
3. **架构认知负担**：新开发者需要理解 `_bypass_session_pool`、`_should_bypass_session_pool()`、`_run_stream_direct()` 三个概念才能理解执行流程

AG-UI bypass（stack inspection）已在 thin-agentpool-core Phase 1 中移除。AG-UI adapter 现在直接调用 `agent.run_stream()` 而不设置 ContextVar——agent 来自 `AgentPool.all_agents`，所以 `agent.agent_pool` 不为 None。AG-UI 现在实际上走 SessionPool 路径（通过 `ProtocolEventConsumerMixin` 消费 EventBus 事件）。这不依赖 bypass 机制，是正常的 SessionPool 路由。

## Goals / Non-Goals

**Goals:**
- 消除 `_bypass_session_pool` ContextVar 和 `_should_bypass_session_pool()` 函数
- 消除 `_run_stream_direct()` 作为独立路径（与主路径合并）
- 简化 `BaseAgent.run_stream()` / `run()` 的路由逻辑——从"bypass vs SessionPool"变为"是否已在 turn 中"
- 删除所有相关的测试 mock 和 ContextVar set 调用

**Non-Goals:**
- 不改变 `turn_lock` 的语义——它仍然保护 agent 实例不被并发执行
- 不改变 SessionPool、TurnRunner、EventBus 的核心逻辑
- 不改变 AG-UI adapter——它已经通过 `agent_pool.session_pool is None` 走正确路径
- 不引入新的锁机制或并发原语

## Decisions

### Decision 1: Turn-Owner Tracking 替代 ContextVar

**方案：** 在 `SessionState` 上增加 `_turn_owner_task: asyncio.Task[Any] | None` 字段，跟踪当前持有 `turn_lock` 的 asyncio.Task。

```python
@dataclass
class SessionState:
    ...
    _turn_owner_task: asyncio.Task[Any] | None = None
```

在 `_run_turn_unlocked()` 入口设置，finally 中清除：

```python
async def _run_turn_unlocked(self, session_id, *prompts, **kwargs):
    session = self.sessions.get_session(session_id)
    session._turn_owner_task = asyncio.current_task()
    try:
        # ... existing logic ...
    finally:
        session._turn_owner_task = None
        # ... existing cleanup ...
```

**路由决策函数** 从 `_should_bypass_session_pool()` 变为 `_should_route_via_sessionpool()`：

```python
def _should_route_via_sessionpool(session_pool, session_id) -> bool:
    if session_pool is None:
        return False  # 没有 SessionPool → 直接执行
    session = session_pool.sessions.get_session(session_id)
    if session is None:
        return True   # 新 session → 通过 SessionPool 创建
    current_task = asyncio.current_task()
    if current_task is not None and session._turn_owner_task is current_task:
        return False  # 自己持有 turn_lock → 直接执行，避免死锁
    return True       # 别人持有或无人持有 → 通过 SessionPool
```

**为什么不是可重入锁（方案 A）：** 可重入锁允许同一协程多次获取锁，但这模糊了锁的语义——无法区分"我是嵌套调用"和"我错误地重复获取"。Turn-Owner Tracking 提供了更清晰的语义：检查"我是否已经在 turn 中"。

**为什么不是完全移除锁（方案 C）：** agent 实例（特别是非 per-session 的 ACP agent）仍然需要串行化保护。完全移除锁需要 agent 实例彻底无状态化，这是一个更大的工程。

### Decision 2: `_run_stream_direct()` 合并到主路径

当前 `_run_stream_direct()` 包含：
1. session 日志记录
2. `AgentRunContext` 创建
3. `_current_run_ctx_var` 设置
4. 调用 `_run_stream_once()` + follow-up loop

这些逻辑在新的架构中统一到 `run_stream()` 内部。当 `_should_route_via_sessionpool()` 返回 False 时，直接调用 `_run_stream_once()` 而不经过 `_run_stream_direct()` 包装器。

本质上，`_run_stream_direct()` 的逻辑被内联到 `run_stream()` 的"直接执行"分支中。这是一个纯重构，不改变行为。

### Decision 3: `run()` 方法同理

`BaseAgent.run()` 与 `run_stream()` 有相同的路由逻辑（见 base_agent.py:1580-1655）。同样的改造应用到 `run()` 上。

### Decision 4: AG-UI adapter 无需改动

当前 `base_agent_adapter.py:128` 调用 `self.agent.run_stream(prompt, store_history=False)`。
- Agent 来自 `AgentPool.all_agents.get(agent_name)`，所以 `agent.agent_pool` 不为 None
- AG-UI 现在实际走 SessionPool 路径（AGUI server 使用 `ProtocolEventConsumerMixin` 消费 EventBus 事件）
- 所以 `run_stream()` 路由到 `SessionPool.run_stream()` → EventBus → `ProtocolEventConsumerMixin` → AG-UI adapter
- 这个路径在新方案下不变，无需改动

**注意**：如果 `AGUIServer` 的 `AgentPool` 没有配置 `session_pool`（`agent_pool.session_pool is None`），则 `run_stream()` 走直接执行路径，这同样是正确的。

### Decision 5: 测试中的 `_bypass_session_pool.set(True)` 替换

测试文件中的 bypass 用法有两种情况：

1. **`test_streaming_redflag_tool_calls.py`**：动态注册 tool 到共享 agent，然后调用 `agent.run_stream()`。如果走 SessionPool，per-session agent 不会继承动态注册的 tool。
   - **修复方案**：创建 session 时将 tool 注册到 per-session agent 上，或直接通过 `_run_stream_once()` 调用。

2. **`test_turn_runner.py`**：验证 bypass ContextVar 的测试。
   - **修复方案**：这些测试改为验证 `SessionState._turn_owner_task` 在 turn 执行期间被正确设置和清除。

## Risks / Trade-offs

- **[Risk] `asyncio.current_task()` 在 task 外可能返回 None** → 当在非 asyncio 上下文（如同步代码、REPL）中调用时，`current_task()` 返回 None。此时 `_should_route_via_sessionpool()` 会保守地返回 True（通过 SessionPool 路由），这是安全的行为——因为如果没有 task，就不可能在 turn_lock 内部。
- **[Risk] 子 task 中的 `run_stream()` 调用导致死锁** → 这是最重要的风险。当前 `_bypass_session_pool` ContextVar 通过 `contextvars.Context` 自动传播到 `asyncio.create_task()` 创建的子 task。Turn-Owner Tracking 的 task identity 比较**不会**传播到子 task。

  **场景**：`_run_turn_unlocked()` 中创建了 background task，该 task 调用 `agent.run_stream()`。子 task 的 `asyncio.current_task()` 与 `session._turn_owner_task` 不同 → `_is_turn_owner()` 返回 False → 路由到 SessionPool → 尝试获取 `turn_lock`（父 task 已持有）→ **DEADLOCK**

  **Mitigation**：保留一个辅助的 `_in_turn_context: ContextVar[bool]`（默认 False），由 `_run_turn_unlocked()` 在入口设为 True、finally 中清除。`_should_route_via_sessionpool()` 的检查顺序为：
  1. 如果 `_in_turn_context.get()` 为 True → 直接执行（捕获子 task 情况）
  2. 否则走 `_turn_owner_task` identity 比较（主路径）
  
  这个 ContextVar 只作为辅助守卫，不参与主路由决策。它的存在是为了保留 ContextVar 传播到子 task 的能力。
  
  注意：`_in_turn_context` 与当前 `_bypass_session_pool` 的关键区别在于——它**不参与** `BaseAgent.run_stream()` 的路由决策（路由决策由 `_turn_owner_task` 做主）。它只作为**最后一道防线**，在子 task 调用 `run_stream()` 时直接走直接执行路径，避免死锁。

- **[Trade-off] 保留了一个辅助 ContextVar** → 虽然目标是最小化 ContextVar 使用，但子 task 死锁风险需要一个传播机制。`_in_turn_context` 的语义更窄（仅表示"是否在 turn 中"），不参与路由决策，只作为安全守卫。
- **[Trade-off] `SessionState._turn_owner_task` 引入了 session 和 asyncio.Task 的耦合** → 这是有意为之：turn_lock 保护的本身就是 agent 实例的并发安全，而并发执行的最小单位是 asyncio.Task。这个耦合是语义正确的。
- **[Risk] 并发清理 race** → 如果 session 在 `_run_turn_unlocked` 执行期间被关闭，`session._turn_owner_task` 的清除可能访问已清理的 session。当前的 `is_closing` 检查和 `close_session` 中的 `turn_lock` 等待已经处理了这个 case，无需额外保护。