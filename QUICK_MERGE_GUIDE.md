# 快速合并指南

## 一、合并前准备

### 1. 创建备份分支
```bash
git checkout feature/merge_phi65_0406
git checkout -b backup-before-merge
git checkout feature/merge_phi65_0406
```

### 2. 确保当前分支干净
```bash
git status
# 如果有未提交的更改，先提交或暂存
```

### 3. 拉取最新代码
```bash
git fetch upstream
git fetch origin
```

---

## 二、合并策略

### 选项 A：完整合并（推荐）
```bash
git merge remotes/upstream/develop/agentic -m "Merge develop/agentic: RFC-0021 and other features"
```

### 选项 B：分批合并（如果有大量冲突）
如果完整合并产生太多冲突，可以按以下顺序分批合并关键功能：

```bash
# 批次 1：核心基础设施
git cherry-pick <AgentRunContext 提交>
git cherry-pick <Tool 统一转换提交>
git cherry-pick <BaseAgent 状态迁移提交>

# 批次 2：会话管理
git cherry-pick <SessionStore 提交>
git cherry-pick <SQLSessionStore 提交>
git cherry-pick <数据库迁移提交>

# 批次 3：事件系统
git cherry-pick <EventProcessor 提交>
git cherry-pick <StreamAdapter 重构提交>

# 批次 4：其他
git merge remotes/upstream/develop/agentic
```

---

## 三、解决常见冲突

### 1. 导入冲突
```python
# 冲突示例
<<<<<<< HEAD
from agentpool.agents.context import AgentContext
=======
from agentpool.agents.context import AgentContext, AgentRunContext
>>>>>>> develop/agentic

# 解决：保留 develop/agentic 的版本
from agentpool.agents.context import AgentContext, AgentRunContext
```

### 2. 方法签名冲突
```python
# 冲突示例
<<<<<<< HEAD
async def log_session(self, ..., agent_type: str | None = None) -> None:
=======
async def log_session(self, ..., parent_session_id: str | None = None) -> None:
>>>>>>> develop/agentic

# 解决：使用 develop/agentic 的签名
async def log_session(self, ..., parent_session_id: str | None = None) -> None:
```

### 3. 类属性冲突
```python
# 冲突示例
<<<<<<< HEAD
self._cancelled = False
self._current_stream_task = None
=======
self._background_run_ctx: AgentRunContext | None = None
self._current_run_ctx: AgentRunContext | None = None
>>>>>>> develop/agentic

# 解决：使用 develop/agentic 的 RunContext
self._background_run_ctx: AgentRunContext | None = None
self._current_run_ctx: AgentRunContext | None = None
```

### 4. 配置模型冲突
```python
# 冲突示例
<<<<<<< HEAD
@dataclass
class SkillsConfig:
    paths: list[UPath]
=======
class SkillsConfig(Schema):
    paths: list[ConfigPath] = Field(default_factory=list)
>>>>>>> develop/agentic

# 解决：使用 develop/agentic 的 Pydantic Schema
class SkillsConfig(Schema):
    paths: list[ConfigPath] = Field(default_factory=list)
```

---

## 四、合并后验证

### 1. 检查合并状态
```bash
git status
# 确保没有未解决的冲突
```

### 2. 类型检查
```bash
uv run mypy src/agentpool/ --strict
```

### 3. 代码格式检查
```bash
uv run ruff check src/
uv run ruff format --check src/
```

### 4. 运行关键测试
```bash
# 并发安全测试（最重要）
uv run pytest tests/agents/test_concurrent_safety.py -v

# 会话管理测试
uv run pytest tests/sessions/ -v

# 事件处理器测试
uv run pytest tests/servers/opencode_server/test_event_processor.py -v

# 工具系统测试
uv run pytest tests/tools/test_tool_schema.py -v

# 完整测试套件
uv run pytest -m unit -x
```

### 5. 数据库迁移
```bash
# 运行新的数据库迁移
uv run alembic upgrade head
```

---

## 五、如果出现错误

### 1. 类型检查失败
```bash
# 查看详细错误信息
uv run mypy src/agentpool/ --strict --show-error-codes

# 常见修复：
# - 添加缺失的导入
# - 修复类型注解
# - 添加 type: ignore 注释（仅当确实无法修复时）
```

### 2. 测试失败
```bash
# 查看失败测试的详细信息
uv run pytest tests/specific/test.py -vv

# 常见原因：
# - 配置格式变更导致测试数据失效
# - API 变更导致测试代码需要更新
# - 依赖项版本冲突
```

### 3. 运行时错误
```bash
# 查看详细日志
export OBSERVABILITY_ENABLED=true
export LOG_LEVEL=DEBUG
# 运行失败的命令
```

---

## 六、回滚计划

如果合并后出现严重问题：

### 1. 立即回滚
```bash
git reset --hard HEAD~1
# 如果已经推送到远程
git push origin +feature/merge_phi65_0406
```

### 2. 创建修复分支
```bash
git checkout -b fix-merge-issues
# 修复问题
git add .
git commit -m "fix merge issues"
```

### 3. 数据库迁移回滚
```bash
# 注意：数据库迁移不能直接回滚
uv run alembic downgrade <version>
# 或者手动修复数据库 schema
```

---

## 七、验证清单

合并完成后，确保：

- [ ] 所有冲突已解决
- [ ] `git status` 显示干净
- [ ] `mypy` 类型检查通过
- [ ] `ruff check` 通过
- [ ] `ruff format` 通过
- [ ] 并发安全测试通过
- [ ] 会话管理测试通过
- [ ] 事件处理器测试通过
- [ ] 工具系统测试通过
- [ ] 数据库迁移成功
- [ ] 本地功能测试通过

---

## 八、提交合并

### 1. 创建合并提交
```bash
git commit -m "Merge develop/agentic: Implement RFC-0021 and other RFCs

Major changes:
- RFC-0021: Agent concurrent execution safety with AgentRunContext
- RFC-0010/0011: Session management with parent_id support
- RFC-0002: Extended tool definition and native PydanticAI integration
- RFC-0008: Dynamic skills injection via ResourceProvider
- RFC-0004: Configurable skills loading paths
- EventProcessor: Major refactor for OpenCode event handling

Files changed: 231
Lines added: 47,853
Lines removed: 6,057"
```

### 2. 推送到远程
```bash
git push origin feature/merge_phi65_0406
```

### 3. 创建 Pull Request
```bash
# 如果需要创建 PR
gh pr create --title "Merge develop/agentic into feature/merge_phi65_0406" \
  --body "See detailed analysis in MERGE_ANALYSIS.md"
```

---

## 九、注意事项

### 关键警告
1. ⚠️ **不要跳过类型检查**：类型错误会在运行时导致严重问题
2. ⚠️ **不要跳过并发测试**：并发安全是本次合并的核心目标
3. ⚠️ **数据库迁移需要仔细处理**：不能直接回滚
4. ⚠️ **配置文件格式可能已变更**：需要更新现有配置

### 推荐做法
1. ✅ 在合并前运行完整测试套件，建立基线
2. ✅ 使用 `git diff` 仔细检查每个冲突
3. ✅ 分批提交，每批解决后立即测试
4. ✅ 保留详细的冲突解决记录

---

## 十、联系支持

如果遇到无法解决的问题：

1. 查看详细分析文档：`MERGE_ANALYSIS.md`
2. 查看相关 RFC 文档：`docs/rfcs/`
3. 检查测试用例：`tests/` 目录
4. 提交 Issue：在项目仓库创建 Issue

---

**快速命令参考**

```bash
# 合并
git merge remotes/upstream/develop/agentic

# 查看冲突
git diff --name-only --diff-filter=U

# 解决冲突后
git add .
git commit

# 回滚
git reset --hard HEAD~1

# 运行测试
uv run pytest -m unit -x

# 类型检查
uv run mypy src/ --strict

# 格式检查
uv run ruff check src/
uv run ruff format --check src/

# 数据库迁移
uv run alembic upgrade head
```

---

**预计时间：** 7-12 小时
**风险等级：** 高（大量架构变更）
**优先级：** P0（RFC-0021 并发安全）
