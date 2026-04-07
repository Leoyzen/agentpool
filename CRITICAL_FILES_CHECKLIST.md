# 关键文件清单（按优先级排序）

## 🔴 P0 - 必须合并（核心基础设施）

### 核心代理系统
1. `src/agentpool/agents/context.py` - 新增 AgentRunContext（RFC-0021 核心）
2. `src/agentpool/agents/base_agent.py` - BaseAgent 状态迁移到 RunContext
3. `src/agentpool/agents/native_agent/agent.py` - NativeAgent 构造函数变更
4. `src/agentpool/agents/native_agent/tool_wrapping.py` - 工具包装传递 run_ctx

### 工具系统
5. `src/agentpool/tools/base.py` - Tool 统一转换逻辑（RFC-0002）
6. `src/agentpool/tools/__init__.py` - Tool 导出更新
7. `src/agentpool/storage/serialization.py` - TypeAdapter 类型修复

### 会话管理
8. `src/agentpool/sessions/store.py` - SessionStore 协议（新增）
9. `src/agentpool/storage/manager.py` - StorageManager 更新
10. `src/agentpool_storage/session_store.py` - SQLSessionStore 实现（新增）
11. `src/agentpool_storage/sql_provider/models.py` - 数据库模型更新
12. `migrations/versions/b2c3d4e5f6a7_add_agent_type_and_sdk_session_id.py` - 数据库迁移（新增）

### 事件系统
13. `src/agentpool_server/opencode_server/event_processor_context.py` - EventProcessorContext（新增）
14. `src/agentpool_server/opencode_server/event_processor.py` - EventProcessor（新增）
15. `src/agentpool_server/opencode_server/stream_adapter.py` - StreamAdapter 重构

### MCP 工具
16. `src/agentpool/mcp_server/client.py` - MCP 工具参数描述修复

---

## 🟡 P1 - 推荐合并（重要功能）

### 配置系统
17. `src/agentpool_config/skills.py` - Skills 配置模型重写（RFC-0004/0008）
18. `src/agentpool_config/paths.py` - ConfigPath 实现
19. `src/agentpool_config/skill_commands.py` - 技能命令配置

### Skills 系统
20. `src/agentpool/skills/skill.py` - Skill 模型增强
21. `src/agentpool/skills/command.py` - 技能命令注册
22. `src/agentpool/skills/command_registry.py` - 技能命令注册表
23. `src/agentpool/resource_providers/skills_instruction.py` - 动态技能注入（新增）

### OpenCode 服务器
24. `src/agentpool/agents/events/events.py` - 子会话事件（SpawnSessionStart）
25. `src/agentpool_server/opencode_server/models/session.py` - Todo 模型增强
26. `src/agentpool_server/opencode_server/routes/session_routes.py` - 会话路由增强

### ACP 服务器
27. `src/agentpool_server/acp_server/event_converter.py` - ACP 事件转换
28. `src/agentpool_server/acp_server/commands/skill_commands.py` - ACP 技能命令

---

## 🟢 P2 - 可选合并（功能增强）

### 问题处理（RFC-0015）
29. `src/agentpool_server/opencode_server/routes/message_routes.py` - 多问题提示
30. `tests/servers/opencode_server/test_question_integration.py` - 问题集成测试（新增）

### 其他增强
31. `src/agentpool/agents/native_agent/hook_manager.py` - HookManager 更新
32. `src/agentpool/delegation/pool.py` - AgentPool 小幅调整
33. `src/agentpool/messaging/messagenode.py` - MessageNode 类型优化

---

## 📚 文档（可选合并）

### RFC 文档
34. `docs/rfcs/accepted/RFC-0002-extended-tool-definition.md`（新增）
35. `docs/rfcs/accepted/RFC-0003-pydantic-ai-history-processors-integration.md`（新增）
36. `docs/rfcs/accepted/RFC-0008-dynamic-skills-injection.md`（新增）
37. `docs/rfcs/accepted/RFC-0013-subagent-event-unification.md`（新增）
38. `docs/rfcs/accepted/RFC-0014-spawn-session-events.md`（新增）
39. `docs/rfcs/draft/RFC-0015-multiple-questions-elicitation.md`（更新）
40. `docs/rfcs/draft/RFC-0016-skill-slash-commands.md`（新增）
41. `docs/rfcs/draft/RFC-0017-opencode-command-skill-support.md`（新增）
42. `docs/rfcs/draft/RFC-0021-agent-concurrent-execution-safety.md`（新增）

### 其他文档
43. `docs/configuration/skills.md` - Skills 配置文档更新
44. `docs/configuration/path-resolution.md` - 路径解析文档更新
45. `docs/features/skill-commands.md` - 技能命令文档（新增）

---

## 🧪 测试文件（必须合并）

### 并发安全测试（最重要）
46. `tests/agents/test_concurrent_safety.py`（新增）
47. `tests/tools/test_runcontext.py` - RunContext 测试更新

### 会话管理测试
48. `tests/sessions/test_session_hierarchy.py`（新增）
49. `tests/sessions/test_storage_provider_fixes.py`（新增）
50. `tests/verification/test_rfc0011_lineage.py`（新增）

### 事件处理器测试
51. `tests/servers/opencode_server/test_event_processor.py`（新增）
52. `tests/servers/opencode_server/test_subagent_event_propagation.py`（新增）
53. `tests/servers/opencode_server/test_spawn_session_start.py`（新增）

### 工具系统测试
54. `tests/tools/test_tool_schema.py` - 工具 schema 测试大幅扩展
55. `tests/tools/test_pydantic_ai_schema.py`（新增）
56. `tests/utils/test_context_wrapping.py`（新增）

### Skills 系统测试
57. `tests/skills/test_unit.py`（新增）
58. `tests/skills/test_manager_config.py`（新增）
59. `tests/skills/test_skills_integration.py`（新增）
60. `tests/integration/test_skill_commands_e2e.py`（新增）
61. `tests/integration/test_skills_injection.py`（新增）

### 其他重要测试
62. `tests/test_break_behavior.py`（新增）
63. `tests/test_opencode_model_switching.py`（新增）
64. `tests/test_schema_override.py` - Schema override 测试更新
65. `tests/test_history_processors.py` - 历史处理器测试更新
66. `tests/test_acp_event_converter_snapshots.py`（新增）
67. `tests/verification/test_acp_display_config.py`（新增）

---

## 📦 依赖和配置文件

### Python 依赖
68. `uv.lock` - 大幅更新（6000+ 行变更）
69. `pyproject.toml` - 依赖版本更新

### 配置 schema
70. `schema/config-schema.json` - 配置 schema 更新

### Git 配置
71. `.gitignore` - 忽略规则更新

---

## 📋 总结统计

- **总计文件数：** 231
- **P0 必须合并：** 16 个文件
- **P1 推荐合并：** 12 个文件
- **P2 可选合并：** 5 个文件
- **文档：** 12 个文件
- **测试：** 22 个文件
- **依赖和配置：** 4 个文件

---

## 🔍 快速查找命令

### 查看特定文件的变更
```bash
git diff $(git merge-base remotes/upstream/develop/agentic HEAD)..remotes/upstream/develop/agentic -- <文件路径>
```

### 查看所有 P0 文件的变更
```bash
git diff $(git merge-base remotes/upstream/develop/agentic HEAD)..remotes/upstream/develop/agentic -- \
  src/agentpool/agents/context.py \
  src/agentpool/agents/base_agent.py \
  src/agentpool/agents/native_agent/agent.py \
  src/agentpool/agents/native_agent/tool_wrapping.py \
  src/agentpool/tools/base.py \
  src/agentpool/storage/serialization.py \
  src/agentpool/sessions/store.py \
  src/agentpool/storage/manager.py \
  src/agentpool_storage/session_store.py \
  src/agentpool_storage/sql_provider/models.py \
  migrations/versions/b2c3d4e5f6a7_add_agent_type_and_sdk_session_id.py \
  src/agentpool_server/opencode_server/event_processor_context.py \
  src/agentpool_server/opencode_server/event_processor.py \
  src/agentpool_server/opencode_server/stream_adapter.py \
  src/agentpool/mcp_server/client.py
```

### 查看统计信息
```bash
git diff --stat $(git merge-base remotes/upstream/develop/agentic HEAD)..remotes/upstream/develop/agentic
```

---

## ⚠️ 重要提醒

1. **P0 文件必须全部合并**，否则会导致运行时错误
2. **测试文件必须合并**，否则无法验证新功能
3. **文档文件可以暂缓**，不影响功能
4. **依赖文件必须更新**，否则无法安装依赖

---

**创建时间：** 2026-04-07
**基于分支：** develop/agentic
**目标分支：** feature/merge_phi65_0406
