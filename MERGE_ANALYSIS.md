# develop/agentic 合并到 feature/merge_phi65_0406 影响分析报告

## 执行摘要

develop/agentic 分支包含 **231 个文件** 的变更，涉及 **14 个 RFC** 的实现。核心变更围绕并发安全、会话管理、事件系统和技能系统的重大重构。

**关键影响级别：**
- 🔴 **关键变更（必须合并）**：RFC-0021 并发安全、RFC-0010/0011 会话管理
- 🟡 **重要变更（推荐合并）**：RFC-0002 工具定义、RFC-0008 技能注入
- 🟢 **功能增强（可选合并）**：RFC-0015/0016/0017 问题处理、技能命令

---

## 一、核心架构变更（RFC-0021：Agent 并发执行安全）

### 1.1 新增 AgentRunContext

**文件：** `src/agentpool/agents/context.py`

**变更内容：**
- 新增 `AgentRunContext` 数据类，用于隔离每次运行的执行状态
- 包含字段：`cancelled`, `current_task`, `event_queue`, `injection_manager`, `session_id`, `deps`, `start_time`
- 修改 `AgentContext` 添加 `run_ctx` 引用

**是否需要改：** ✅ **必须**
**为什么需要改：** RFC-0021 的核心实现，确保并发执行时事件队列隔离
**不改的风险：**
- 并发执行时事件队列混乱
- 多个运行共享状态导致数据污染
- subagent 调用时事件路由错误

**解决冲突说明：**
```python
# 新增的 AgentRunContext 数据类
@dataclass(kw_only=True)
class AgentRunContext:
    """Per-execution isolated state container for agent runs."""
    cancelled: bool = False
    current_task: asyncio.Task[Any] | None = None
    event_queue: asyncio.Queue[Any] = field(default_factory=asyncio.Queue)
    injection_manager: PromptInjectionManager = field(default_factory=PromptInjectionManager)
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    deps: Any = None
    start_time: float = field(default_factory=time.perf_counter)
```

**合并优先级：** 🔴 P0（最高优先级）

---

### 1.2 BaseAgent 状态迁移

**文件：** `src/agentpool/agents/base_agent.py`

**变更内容：**
- 将 `_cancelled`, `_current_stream_task`, `_injection_manager` 从实例变量迁移到 `AgentRunContext`
- 添加 `_background_run_ctx` 和 `_current_run_ctx` 用于不同场景
- `get_context()` 方法添加 `run_ctx` 参数
- 移除 `storage` 参数（改用 agent_pool.storage）

**是否需要改：** ✅ **必须**
**为什么需要改：** 配合 AgentRunContext 重构，支持并发隔离
**不改的风险：**
- 并发执行时状态污染
- 背景任务和前台任务共享状态导致竞态条件
- 事件队列隔离失效

**解决冲突说明：**
```python
# 旧代码（单一实例变量）
self._cancelled = False
self._current_stream_task: asyncio.Task[Any] | None = None
self._injection_manager = PromptInjectionManager()

# 新代码（迁移到 RunContext）
self._background_run_ctx: AgentRunContext | None = None
self._current_run_ctx: AgentRunContext | None = None
```

**合并优先级：** 🔴 P0

---

### 1.3 NativeAgent 工具包装修复

**文件：** `src/agentpool/agents/native_agent/tool_wrapping.py`

**变更内容：**
- 工具包装时必须传递 `run_ctx` 参数
- 修复事件队列隔离问题（RFC-0021 关键修复）

**是否需要改：** ✅ **必须**
**为什么需要改：** 并发执行时工具调用需要独立的事件队列
**不改的风险：**
- 工具调用时事件发送到错误的队列
- 并发工具调用时事件混乱
- subagent 调用失败

**解决冲突说明：**
```python
# 关键变更：传播 run_ctx
call_ctx = replace(
    agent_ctx,
    tool_name=ctx.tool_name,
    tool_call_id=ctx.tool_call_id,
    tool_input=kwargs.copy(),
    model_name=model_name,
    run_ctx=ctx.deps.run_ctx if ctx.deps else None,  # 新增
)
```

**合并优先级：** 🔴 P0

---

### 1.4 NativeAgent 构造函数变更

**文件：** `src/agentpool/agents/native_agent/agent.py`

**变更内容：**
- 移除 `storage` 参数
- 移除 `history_processors` 参数（改为动态解析）
- 添加 `_resolve_history_processors()` 和 `_validate_processor_signature()` 方法
- 改进路径解析逻辑（ConfigPath 自动处理相对路径）

**是否需要改：** ✅ **必须**
**为什么需要改：** 配合架构重构，支持从配置动态加载历史处理器
**不改的风险：**
- 配置中的 history_processors 不生效
- 路径解析错误
- 无法使用动态技能注入功能

**解决冲突说明：**
```python
# 旧构造函数
def __init__(
    self,
    ...,
    history_processors: Sequence[Callable[..., Any]] | None = None,
    storage: StorageManager | None = None,
) -> None:

# 新构造函数
def __init__(
    self,
    ...,
    # history_processors 和 storage 已移除
) -> None:
    # 动态解析 history_processors
    self._resolved_history_processors: list[Callable[..., Any]] | None = None
```

**合并优先级：** 🔴 P0

---

## 二、Session 管理重构（RFC-0010/0011）

### 2.1 新增 SessionStore 协议

**文件：** `src/agentpool/sessions/store.py`（新增文件）

**变更内容：**
- 新增 `SessionStore` 协议定义
- 实现 `MemorySessionStore` 内存存储
- 添加 `parent_id` 过滤支持（RFC-0010）

**是否需要改：** ✅ **必须**
**为什么需要改：** 支持子会话管理和会话层级查询
**不改的风险：**
- 无法创建子会话
- 无法查询父会话的子会话列表
- OpenCode 子会话导航功能失效

**解决冲突说明：**
```python
# 新协议定义
@runtime_checkable
class SessionStore(Protocol):
    @abstractmethod
    async def list_sessions(
        self,
        pool_id: str | None = None,
        agent_name: str | None = None,
        parent_id: str | None = None,  # 新增
    ) -> list[str]:
        ...
```

**合并优先级：** 🔴 P0

---

### 2.2 SQLSessionStore 实现

**文件：** `src/agentpool_storage/session_store.py`（新增文件）

**变更内容：**
- 实现 SQL 版本的 SessionStore
- 支持 SQLite/PostgreSQL/MySQL
- 自动运行 Alembic 迁移

**是否需要改：** ✅ **必须**
**为什么需要改：** 提供持久化会话存储
**不改的风险：**
- 使用 SQL 存储时无法保存/加载会话
- OpenCode 会话历史功能失效
- 测试失败

**合并优先级：** 🔴 P0

---

### 2.3 数据库模型更新

**文件：** `src/agentpool_storage/sql_provider/models.py`

**变更内容：**
- `Conversation` 模型添加 `parent_id` 字段
- 添加 `Session = Conversation` 别名（RFC-0011 兼容）

**是否需要改：** ✅ **必须**
**为什么需要改：** 支持会话层级关系
**不改的风险：**
- 无法存储子会话关系
- 数据库查询失败

**解决冲突说明：**
```python
class Conversation(AsyncAttrs, SQLModel, table=True):
    ...
    parent_id: str | None = Field(default=None, index=True)
    """Parent conversation ID for subagent/forked sessions."""
    ...

# RFC-0011 兼容别名
Session = Conversation
```

**合并优先级：** 🔴 P0

---

### 2.4 数据库迁移

**文件：** `migrations/versions/b2c3d4e5f6a7_add_agent_type_and_sdk_session_id.py`（新增）

**变更内容：**
- 添加 `agent_type` 和 `sdk_session_id` 列到 conversation 表
- 创建相应索引

**是否需要改：** ✅ **必须**
**为什么需要改：** 支持代理类型区分和 SDK 会话跟踪
**不改的风险：**
- 数据库 schema 不匹配
- 运行时错误：列不存在

**合并优先级：** 🔴 P0

---

### 2.5 StorageManager 更新

**文件：** `src/agentpool/storage/manager.py`

**变更内容：**
- 移除构造函数的 `providers` 参数
- `log_session()` 方法签名变更：
  - 移除 `agent_type` 参数
  - 添加 `parent_session_id` 参数
- 添加 `save_session()`, `load_session()`, `delete_session()` 方法
- 改进标题生成逻辑

**是否需要改：** ✅ **必须**
**为什么需要改：** 配合 SessionStore 协议，支持子会话
**不改的风险：**
- 无法保存/加载会话
- 无法记录父会话关系
- API 不兼容

**解决冲突说明：**
```python
# 旧方法签名
async def log_session(
    self,
    session_id: str,
    node_name: str,
    start_time: datetime | None = None,
    model: str | None = None,
    agent_type: str | None = None,  # 移除
    initial_prompt: str | None = None,
    on_title_generated: Callable[[str], None] | None = None,
) -> None:

# 新方法签名
async def log_session(
    self,
    session_id: str,
    node_name: str,
    start_time: datetime | None = None,
    model: str | None = None,
    initial_prompt: str | None = None,
    parent_session_id: str | None = None,  # 新增
    on_title_generated: Callable[[str], None] | None = None,
) -> None:
```

**合并优先级：** 🔴 P0

---

## 三、事件系统重构

### 3.1 新增 EventProcessor

**文件：** `src/agentpool_server/opencode_server/event_processor.py`（新增文件，~1009 行）

**变更内容：**
- 新增 `EventProcessor` 类，处理 RichAgentStreamEvent → OpenCode SSE 事件转换
- 使用 `EventProcessorContext` 管理可变状态
- 支持递归子会话处理
- 统一事件处理逻辑

**是否需要改：** ✅ **必须**
**为什么需要改：** OpenCode 服务器事件处理核心重构
**不改的风险：**
- OpenCode 服务器无法工作
- 事件流中断
- 子会话事件路由错误

**合并优先级：** 🔴 P0

---

### 3.2 StreamAdapter 重构

**文件：** `src/agentpool_server/opencode_server/stream_adapter.py`

**变更内容：**
- 使用 `EventProcessor` 替代内联事件处理逻辑
- 状态管理迁移到 `EventProcessorContext`
- 简化适配器代码
- 添加 `state`, `processor`, `main_context` 字段

**是否需要改：** ✅ **必须**
**为什么需要改：** 配合 EventProcessor 重构
**不改的风险：**
- 无法与 EventProcessor 协作
- 状态管理混乱
- 事件丢失

**解决冲突说明：**
```python
# 旧代码（内联事件处理）
def _process_text_delta(self, delta: str) -> Iterator[Event]:
    if not self._text_part:
        self._text_part = TextPart(...)
    ...

# 新代码（委托给 EventProcessor）
processor: EventProcessor = field(default_factory=EventProcessor, init=False)
main_context: EventProcessorContext = field(init=False)
```

**合并优先级：** 🔴 P0

---

### 3.3 EventProcessorContext

**文件：** `src/agentpool_server/opencode_server/event_processor_context.py`（新增文件）

**变更内容：**
- 新增 `EventProcessorContext` 类，管理事件处理可变状态
- 包含工具部分、文本累积、令牌计数等

**是否需要改：** ✅ **必须**
**为什么需要改：** EventProcessor 的核心依赖
**不改的风险：**
- EventProcessor 无法工作
- 状态管理失败

**合并优先级：** 🔴 P0

---

## 四、OpenCode 服务器增强

### 4.1 会话路由增强

**文件：** `src/agentpool_server/opencode_server/routes/session_routes.py`

**变更内容：**
- 新增命令执行逻辑（`_execute_slashed_command`, `_execute_skill_command`）
- 新增技能模板处理（`_process_skill_template`）
- 添加子会话查询支持
- 改进错误处理

**是否需要改：** ✅ **必须**
**为什么需要改：** RFC-0016/0017 技能命令支持
**不改的风险：**
- 技能命令功能失效
- 子会话导航功能失效

**合并优先级：** 🔴 P0

---

### 4.2 Todo 模型增强

**文件：** `src/agentpool_server/opencode_server/models/session.py`

**变更内容：**
- `Todo` 模型添加 `priority` 字段
- `TodoPriority` 类型定义（"high", "medium", "low"）

**是否需要改：** ✅ **必须**
**为什么需要改：** 支持 todo 优先级功能
**不改的风险：**
- API 不兼容
- 客户端解析错误

**合并优先级：** 🟡 P1

---

### 4.3 子会话事件支持

**文件：** `src/agentpool/agents/events/events.py`

**变更内容：**
- 新增 `SpawnSessionStart` 事件（RFC-0014）
- 新增 `SubAgentEvent` 事件（RFC-0013）

**是否需要改：** ✅ **必须**
**为什么需要改：** 子会话生命周期管理
**不改的风险：**
- 无法创建子会话
- 子会话事件路由失败

**合并优先级：** 🔴 P0

---

## 五、Skills 系统重构（RFC-0004/0008/0016/0017）

### 5.1 Skills 配置模型重写

**文件：** `src/agentpool_config/skills.py`

**变更内容：**
- 完全重写 `SkillsConfig`，从 dataclass 改为 Pydantic Schema
- 新增 `SkillsInstructionConfig` 支持动态技能注入
- 使用 `ConfigPath` 自动处理路径解析
- 移除硬编码的 dev_browser skill

**是否需要改：** ✅ **必须**
**为什么需要改：** RFC-0004/0008 的核心实现
**不改的风险：**
- 配置加载失败
- 动态技能注入不工作
- 路径解析错误

**解决冲突说明：**
```python
# 旧代码
@dataclass
class Skill:
    url: str
    name: str

# 新代码
class SkillsConfig(Schema):
    paths: list[ConfigPath] = Field(default_factory=list)
    include_default: bool = Field(default=True)
    instruction: SkillsInstructionConfig = Field(default_factory=SkillsInstructionConfig)
```

**合并优先级：** 🔴 P0

---

### 5.2 Skill 模型增强

**文件：** `src/agentpool/skills/skill.py`

**变更内容：**
- 新增字段：`disable_model_invocation`, `user_invocable`, `context`, `agent`, `argument_hint`
- 修改 `to_prompt()` 方法支持新字段
- 添加过滤逻辑（跳过 disable_model_invocation 的技能）

**是否需要改：** ✅ **必须**
**为什么需要改：** RFC-0016/0017 技能命令支持
**不改的风险：**
- 技能元数据丢失
- 技能命令功能失效
- 技能过滤不生效

**合并优先级：** 🟡 P1

---

### 5.3 技能命令注册

**文件：** `src/agentpool/skills/command.py`, `src/agentpool/skills/command_registry.py`

**变更内容：**
- 新增技能到斜杠命令的转换逻辑
- 支持技能参数提示
- 支持技能上下文设置

**是否需要改：** ✅ **推荐**
**为什么需要改：** RFC-0016/0017 实现
**不改的风险：**
- 无法使用技能命令
- 技能发现功能受限

**合并优先级：** 🟡 P1

---

### 5.4 SkillsInstructionProvider

**文件：** `src/agentpool/resource_providers/skills_instruction.py`（新增文件）

**变更内容：**
- 新增 `SkillsInstructionProvider` 实现动态技能注入
- 支持三种模式："off", "metadata", "full"
- 支持 agent 覆盖配置

**是否需要改：** ✅ **推荐**
**为什么需要改：** RFC-0008 的核心实现
**不改的风险：**
- 动态技能注入不工作
- 技能发现受限

**合并优先级：** 🟡 P1

---

## 六、工具系统重构（RFC-0002）

### 6.1 Tool 统一转换

**文件：** `src/agentpool/tools/base.py`, `src/agentpool/tools/__init__.py`

**变更内容：**
- 使用 `Tool.from_schema` 统一工具转换逻辑
- 移除 `SchemaWrapper` 类
- 添加 `prepare` hook 支持
- 改进 schema 生成回退机制

**是否需要改：** ✅ **必须**
**为什么需要改：** RFC-0002 的核心实现，修复验证问题
**不改的风险：**
- 工具验证失败（validate_json 缺失）
- AgentContext 类型错误
- prepare hook 不生效

**合并优先级：** 🔴 P0

---

### 6.2 MCP 工具修复

**文件：** `src/agentpool/mcp_server/client.py`

**变更内容：**
- 修复 MCP 工具转换时参数描述丢失问题
- 传递 `schema_override` 参数保留原始参数描述

**是否需要改：** ✅ **必须**
**为什么需要改：** 修复 MCP 工具元数据丢失
**不改的风险：**
- MCP 工具参数描述丢失
- LLM 无法理解工具参数

**解决冲突说明：**
```python
# 旧代码
return FunctionTool.from_callable(tool_callable, source="mcp")

# 新代码
return FunctionTool.from_callable(
    tool_callable,
    source="mcp",
    schema_override=schema,  # 保留参数描述
)
```

**合并优先级：** 🔴 P0

---

## 七、历史处理器（RFC-0003）

### 7.1 History Processors 动态解析

**文件：** `src/agentpool/agents/native_agent/agent.py`

**变更内容：**
- 添加 `_resolve_history_processors()` 方法
- 添加 `_validate_processor_signature()` 方法
- 支持从配置动态加载历史处理器

**是否需要改：** ✅ **推荐**
**为什么需要改：** RFC-0003 实现
**不改的风险：**
- 配置中的 history_processors 不生效
- 无法扩展历史处理逻辑

**合并优先级：** 🟡 P1

---

## 八、配置路径解析（RFC-0004）

### 8.1 ConfigPath 统一处理

**文件：** `src/agentpool_config/paths.py`, `src/agentpool_config/skills.py`, `src/agentpool/agents/native_agent/agent.py`

**变更内容：**
- 新增 `ConfigPath` 类型，自动处理相对路径解析
- 所有配置路径使用 ConfigPath 替代手动解析
- 简化路径处理逻辑

**是否需要改：** ✅ **必须**
**为什么需要改：** RFC-0004 的核心实现
**不改的风险：**
- 路径解析错误
- 配置文件相对路径失效

**合并优先级：** 🟡 P1

---

## 九、问题处理增强（RFC-0015）

### 9.1 多问题提示

**文件：** `src/agentpool_server/opencode_server/`（多个文件）

**变更内容：**
- 支持连续多个问题的提示
- 改进问题收集逻辑
- 添加相关测试

**是否需要改：** ⚪ **可选**
**为什么需要改：** RFC-0015 实现，提升用户体验
**不改的风险：**
- 多问题场景下用户体验下降
- 需要多次确认

**合并优先级：** 🟢 P2

---

## 十、其他重要变更

### 10.1 类型注解修复

**文件：** `src/agentpool/storage/serialization.py`

**变更内容：**
- 修复 `TypeAdapter` 类型注解错误

**是否需要改：** ✅ **必须**
**为什么需要改：** 运行时错误修复
**不改的风险：**
- 序列化失败
- mypy 类型检查错误

**合并优先级：** 🔴 P0

---

### 10.2 Native Agent 会话加载

**文件：** `src/agentpool/agents/native_agent/agent.py`

**变更内容：**
- 使用 storage manager 的 `get_session_messages` 加载会话

**是否需要改：** ✅ **必须**
**为什么需要改：** 配合 SessionStore 重构
**不改的风险：**
- 会话历史加载失败
- 测试失败

**合并优先级：** 🔴 P0

---

### 10.3 OpenCode 会话恢复

**文件：** `src/agentpool_server/opencode_server/`（多个文件）

**变更内容：**
- 修复会话恢复问题
- 添加消息模型角色属性
- 改进会话切换逻辑

**是否需要改：** ✅ **必须**
**为什么需要改：** 修复关键 bug
**不改的风险：**
- 会话恢复失败
- 用户体验差

**合并优先级：** 🔴 P0

---

## 十一、文档和测试

### 11.1 RFC 文档

**文件：** `docs/rfcs/` 目录下多个文件

**变更内容：**
- 新增 RFC-0002, RFC-0003, RFC-0008, RFC-0010, RFC-0011, RFC-0012, RFC-0013, RFC-0014, RFC-0015, RFC-0016, RFC-0017, RFC-0019, RFC-0021 文档

**是否需要改：** ⚪ **可选**
**为什么需要改：** 文档更新
**不改的风险：**
- 无，仅影响文档完整性

**合并优先级：** 🟢 P2

---

### 11.2 测试覆盖

**文件：** `tests/` 目录下多个新增和修改的测试文件

**变更内容：**
- 新增并发安全测试
- 新增会话管理测试
- 新增事件处理器测试
- 新增技能系统测试

**是否需要改：** ✅ **必须**
**为什么需要改：** 确保新功能正确性
**不改的风险：**
- 新功能缺乏测试
- 回归风险

**合并优先级：** 🔴 P0

---

## 合并执行顺序

### 阶段 1：核心基础设施（必须先合并）
1. ✅ 合并 `src/agentpool/agents/context.py`（AgentRunContext）
2. ✅ 合并 `src/agentpool/tools/base.py`（Tool 统一转换）
3. ✅ 合并 `src/agentpool/storage/serialization.py`（类型修复）
4. ✅ 合并 `src/agentpool/agents/native_agent/tool_wrapping.py`（工具包装修复）

### 阶段 2：代理基础重构
5. ✅ 合并 `src/agentpool/agents/base_agent.py`（BaseAgent 状态迁移）
6. ✅ 合并 `src/agentpool/agents/native_agent/agent.py`（NativeAgent 构造函数变更）

### 阶段 3：会话管理系统
7. ✅ 合并 `src/agentpool/sessions/store.py`（SessionStore 协议）
8. ✅ 合并 `src/agentpool_storage/session_store.py`（SQLSessionStore）
9. ✅ 合并 `src/agentpool_storage/sql_provider/models.py`（数据库模型）
10. ✅ 合并 `migrations/versions/b2c3d4e5f6a7_add_agent_type_and_sdk_session_id.py`（数据库迁移）
11. ✅ 合并 `src/agentpool/storage/manager.py`（StorageManager 更新）

### 阶段 4：事件系统重构
12. ✅ 合并 `src/agentpool_server/opencode_server/event_processor_context.py`（EventProcessorContext）
13. ✅ 合并 `src/agentpool_server/opencode_server/event_processor.py`（EventProcessor）
14. ✅ 合并 `src/agentpool_server/opencode_server/stream_adapter.py`（StreamAdapter 重构）

### 阶段 5：OpenCode 服务器
15. ✅ 合并 `src/agentpool/agents/events/events.py`（子会话事件）
16. ✅ 合并 `src/agentpool_server/opencode_server/models/session.py`（Todo 模型）
17. ✅ 合并 `src/agentpool_server/opencode_server/routes/session_routes.py`（会话路由）
18. ✅ 合并 `src/agentpool_server/opencode_server/` 其他修复文件

### 阶段 6：Skills 系统（可选）
19. ✅ 合并 `src/agentpool_config/skills.py`（Skills 配置）
20. ✅ 合并 `src/agentpool/skills/skill.py`（Skill 模型）
21. ✅ 合并 `src/agentpool/skills/command.py`（技能命令）
22. ✅ 合并 `src/agentpool/resource_providers/skills_instruction.py`（技能注入）

### 阶段 7：MCP 和其他修复
23. ✅ 合并 `src/agentpool/mcp_server/client.py`（MCP 工具修复）
24. ✅ 合并 `src/agentpool_config/paths.py`（ConfigPath）
25. ✅ 合并其他配置模型文件

### 阶段 8：测试和文档
26. ✅ 合并 `tests/` 目录下所有测试文件
27. ✅ 合并 `docs/rfcs/` 目录下 RFC 文档

---

## 冲突解决指南

### 常见冲突类型

#### 1. 导入顺序冲突
```python
# develop/agentic
from agentpool.agents.context import AgentContext, AgentRunContext

# feature/merge_phi65_0406
from agentpool.agents.context import AgentContext

# 解决：合并导入
from agentpool.agents.context import AgentContext, AgentRunContext
```

#### 2. 方法签名冲突
```python
# develop/agentic
async def log_session(
    self,
    ...,
    parent_session_id: str | None = None,
) -> None:

# feature/merge_phi65_0406
async def log_session(
    self,
    ...,
    agent_type: str | None = None,
) -> None:

# 解决：使用 develop/agentic 的签名，移除 agent_type
```

#### 3. 类属性冲突
```python
# develop/agentic
self._background_run_ctx: AgentRunContext | None = None
self._current_run_ctx: AgentRunContext | None = None

# feature/merge_phi65_0406
self._cancelled = False
self._current_stream_task: asyncio.Task[Any] | None = None

# 解决：使用 develop/agentic 的 RunContext，迁移现有代码
```

#### 4. 配置模型冲突
```python
# develop/agentic（Pydantic Schema）
class SkillsConfig(Schema):
    paths: list[ConfigPath] = Field(default_factory=list)

# feature/merge_phi65_0406（dataclass）
@dataclass
class SkillsConfig:
    paths: list[UPath]

# 解决：使用 develop/agentic 的 Pydantic Schema
```

---

## 验证步骤

合并完成后，必须执行以下验证：

### 1. 类型检查
```bash
uv run mypy src/agentpool/ --strict
```

### 2. 代码格式检查
```bash
uv run ruff check src/
uv run ruff format --check src/
```

### 3. 单元测试
```bash
uv run pytest -m unit
```

### 4. 并发安全测试
```bash
uv run pytest tests/agents/test_concurrent_safety.py -v
```

### 5. 会话管理测试
```bash
uv run pytest tests/sessions/ -v
```

### 6. 事件处理器测试
```bash
uv run pytest tests/servers/opencode_server/test_event_processor.py -v
```

### 7. 技能系统测试
```bash
uv run pytest tests/skills/ -v
```

### 8. 集成测试
```bash
uv run pytest -m integration
```

---

## 风险评估

### 高风险区域
1. 🔴 **AgentRunContext 迁移**：影响所有代理执行路径
2. 🔴 **SessionStore 协议**：影响会话存储和查询
3. 🔴 **EventProcessor 重构**：影响 OpenCode 事件流
4. 🔴 **Tool 统一转换**：影响所有工具调用

### 中风险区域
1. 🟡 **Skills 配置重写**：配置格式变更
2. 🟡 **ConfigPath**：路径解析逻辑变更
3. 🟡 **MCP 工具修复**：影响 MCP 集成

### 低风险区域
1. 🟢 **文档更新**：仅影响文档
2. 🟢 **测试补充**：仅增加测试覆盖
3. 🟢 **问题处理增强**：可选功能

---

## 回滚计划

如果合并后出现严重问题：

1. **立即回滚**：使用 `git revert` 回滚相关提交
2. **分阶段回滚**：按合并顺序反向回滚
3. **保留数据**：数据库迁移需要特殊处理，不能直接回滚
4. **分支保护**：合并前创建备份分支

---

## 总结

### 关键要点
1. ✅ **RFC-0021（并发安全）** 是最重要的变更，必须优先合并
2. ✅ **RFC-0010/0011（会话管理）** 是核心基础设施，必须合并
3. ✅ **EventProcessor 重构** 是 OpenCode 服务器的重大变更，需要仔细测试
4. ✅ **Skills 系统重构** 是可选但有价值的增强

### 建议策略
1. 先合并核心基础设施（Context、Tool、BaseAgent）
2. 再合并会话管理系统（SessionStore、StorageManager）
3. 然后合并事件系统（EventProcessor、StreamAdapter）
4. 最后合并可选功能（Skills、问题处理）

### 预计时间
- 合并代码：4-6 小时
- 解决冲突：2-4 小时
- 运行测试：1-2 小时
- 总计：7-12 小时

---

**报告生成时间：** 2026-04-07
**分析分支：** develop/agentic → feature/merge_phi65_0406
**变更文件数：** 231
**涉及 RFC：** 14 个
