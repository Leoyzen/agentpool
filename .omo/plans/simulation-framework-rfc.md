# RFC: AgentPool Simulation Framework (Simplified)

## Status: Draft

## 1. 问题背景

需要一个模拟框架用于测试 Agents，通过 adversarial user simulation 来验证 Agent 行为。

## 2. 核心设计决策

### 2.1 架构原则
- **不维护独立 History**：利用 Target Agent 自身的 conversation 自动管理历史
- **Tool-Based 方案**：使用两个工具抽象所有交互（不使用 InputProvider 循环）
- **统一执行入口**：talk_to_target 和 answer_elicitation 底层复用同一方法

### 2.2 架构图

```
Sim Agent (Native Agent)
    ├─ talk_to_target(message) → RunResult
    └─ answer_elicitation(answers) → RunResult
           ↓
    ┌─────────────────────────────────────┐
    │   SimulationProvider                │
    │   ├─ _run_target_agent()           │
    │   └─ _detect_elicitation()         │
    └─────────────────────────────────────┘
           ↓
    Target Agent (Native Agent)
           ├─ run_stream(message)
           ├─ conversation (auto-managed history)
           └─ tools (may include question tool for elicitation)
```

## 3. 核心组件

### 3.1 Target Session 管理

```python
@dataclass
class TargetSession:
    """轻量级 Target Agent 包装"""
    agent: Agent  # Target Agent 自己管理 conversation
    metadata: dict = field(default_factory=dict)
```

**关键**：不保存/恢复 history，完全依赖 Agent 自身的 conversation。

### 3.2 统一执行方法

```python
async def _run_target_agent(
    self,
    session_id: str,
    user_input: str | dict,  # str=初始消息, dict=追问回答
) -> RunResult:
    """核心抽象：运行 Target Agent，探测追问或完成"""
    session = self._get_or_create_session(session_id)
    
    # 直接运行，历史自动延续
    async for event in session.agent.run_stream(user_input):
        # 检测追问（通过 ToolCallStartEvent）
        if self._is_elicitation(event):
            return RunResult(
                status="elicitation",
                questions=self._extract_questions(event),
                partial_response=self._buffered_response()
            )
            
    return RunResult(status="completed", response=self._full_response())
```

### 3.3 处理 Elicitation

当 Target Agent 的 tool 调用触发 question（或类似追问机制）时：

```python
def _is_elicitation(self, event: AgentStreamEvent) -> bool:
    """检测是否是追问事件"""
    return (
        isinstance(event, ToolCallStartEvent) 
        and event.tool_name in ELICITATION_TOOLS
    )
```

**重要**：不使用 InputProvider 拦截，而是检测 ToolCall 事件。

## 4. 工具定义

### 4.1 talk_to_target

```python
@tool
def talk_to_target(
    message: str,
    target_agent_name: str | None = None,  # 可选：指定目标 Agent
) -> dict:
    """发送消息给目标 Agent，返回完成响应或追问。
    
    Returns:
        {
            "status": "completed" | "elicitation",
            "response": str | None,           # completed 时有
            "questions": list | None,         # elicitation 时有
            "session_id": str,
        }
    """
```

### 4.2 answer_elicitation

```python
@tool
def answer_elicitation(
    answers: dict[str, Any],  # {question_id: answer_value}
    session_id: str,
) -> dict:
    """回答追问并继续对话。
    
    Returns:
        同 talk_to_target，支持嵌套追问
    """
```

## 5. Sim Agent 使用模式

```python
# 基础模式：单次问答
result = await talk_to_target("Hello")
if result["status"] == "completed":
    print(result["response"])

# 复杂模式：处理嵌套追问
async def run_conversation(initial_message: str):
    result = await talk_to_target(initial_message)
    
    while result["status"] == "elicitation":
        # Sim Agent 决定如何回答（可以使用其他工具）
        answers = await decide_answers(result["questions"])
        
        # 继续对话
        result = await answer_elicitation(answers, result["session_id"])
    
    return result["response"]
```

## 6. 关键实现细节

### 6.1 追问检测机制

| 方案 | 实现 | 备注 |
|------|------|------|
| ToolCall 检测 | 监听 ToolCallStartEvent | 适用于显式 question tool |
| Custom Event | Agent 发出 ElicitationSignalEvent | 需要 Target Agent 配合 |
| Output 解析 | 解析 response 文本 | 最灵活但最脆弱 |

**推荐**：方案 A（ToolCall 检测），简单且无需修改 Target Agent。

### 6.2 避免递归死锁

当 answer_elicitation 再次触发追问时：

```python
# 正常流程，不会死锁
result = answer_elicitation(answers)
if result["status"] == "elicitation":
    # 返回给 Sim Agent 继续决策
    # 不是递归调用，是循环
    pass
```

### 6.3 History 管理

- **Sim Agent**：不关心 Target Agent 的 history
- **Target Agent**：self.conversation 自动累积
- **会话隔离**：每个 session_id 对应独立的 Target Agent 实例

## 7. 配置示例

```yaml
# simulation-config.yml
agents:
  sim_user:
    type: native
    model: openai:gpt-4o-mini
    system_prompt: "You are testing an AI assistant..."
    tools:
      - name: talk_to_target
        provider: simulation
      - name: answer_elicitation
        provider: simulation
    
  target_assistant:
    type: native
    model: anthropic:claude-sonnet-4
    system_prompt: "You are a helpful assistant..."
    # Target Agent 可以有 question tool
    tools:
      - name: question
        enabled: true

providers:
  simulation:
    type: simulation
    target_agent: target_assistant  # 默认目标
```

## 8. 未解决问题

1. **追问超时**：Target Agent 无限等待追问回答时的处理
2. **多模态追问**：如何传递非文本追问（如文件上传请求）
3. **批处理支持**：如何批量运行多个测试用例

## 9. 实现优先级

1. P0: 基础 talk_to_target 和 answer_elicitation 工具
2. P1: ToolCall 检测追问机制
3. P2: 会话生命周期管理（清理、超时）
4. P3: 运行轨迹记录（通过 AgentPool Storage）

---

## 10. 更新记录

### 2026-03-23: 简化设计

**主要变更**：
- 移除了仿真层独立维护 history 的设计
- 移除了 InputProvider 循环方案
- 改为 Tool-Based 方案，检测 ToolCall 事件识别追问
- 统一了 talk_to_target 和 answer_elicitation 的底层实现

**理由**：
1. Target Agent 自身的 conversation 已足够
2. 避免 Sim 层和 Target 层的 history 同步问题
3. Tool 方案更简单、易调试
