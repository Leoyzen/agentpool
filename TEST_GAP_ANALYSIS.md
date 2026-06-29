# 分析和补齐测试计划

## 现有测试覆盖情况

### ✅ 已有测试 (tests/hooks/test_hooks_capability.py)
- [x] 基础的 as_capability() 功能
- [x] before_run/after_run 映射
- [x] before_tool_execute/after_tool_execute 映射  
- [x] 空 hooks 返回空 Hooks
- [x] deny hooks 抛出 RuntimeError
- [x] 修改输入参数传递
- [x] 多个相同事件的 hooks

## ❌ 缺失测试 (tasks.md 第5节)

### 1. 新增的 _ToolInterceptCapability 功能测试

#### 1.1 get_wrapper_toolset() 相关测试
```python
[ ] 测试 mode="always" 时用 ApprovalRequiredToolset 包装整个 toolset
[ ] 测试 mode="never" 时返回 None 不包装
[ ] 测试 mode="per_tool" 时只包装 requires_confirmation=True 的工具
[ ] 从 ctx.deps.node 读取 tool_confirmation_mode 配置
```

#### 1.2 prepare_tools() 相关测试  
```python
[ ] 测试准备工具时注入 bridge metadata
[ ] 使用 dataclasses.replace() 避免修改共享 ToolDefinition 状态
[ ] 验证修改后的 schema 包含预期元数据
```

#### 1.3 wrap_tool_execute() 相关测试
```python
[ ] 测试成功执行返回 unchanged结果
[ ] 测试异常执行时返回带注释错误信息的 ToolReturn
[ ] 正确转换 agentpool ToolResult 到 pydantic-ai ToolReturn
[ ] 提取 structured_content 或 content 并正确包装
```

#### 1.4 before_tool_execute() 相关测试
```python
[ ] 执行 pre-tool hooks
[ ] 处理 "deny" 决策并抛出 ModelRetry (不是 RuntimeError)
[ ] 应用 modified_input 到已验证参数
```

#### 1.5 after_tool_execute() 相关测试
```python
[ ] 执行 post-tool hooks
[ ] 应用 modified_output (替换结果)
[ ] 应用 additional_context (通过 _inject_additional_context)
[ ] 修复现有缺失 additional_context 的问题
```

### 2. 双发问题修复测试

```python
[ ] 验证移除 if not self.hooks guard 后不会双发
[ ] 旧 AgentHooks 激活时 capability chain 仍正确工作
[ ] hooks_cap 的 after_tool_execute 为 pass-through
[ ] CombinedCapability 链顺序正确：hooks_cap → _ToolInterceptCapability
```

### 3. wrap_tool() 简化测试

```python
[ ] 移除 handle_confirmation() 调用后确认仍正常工作
[ ] 移除 _execute_with_hooks() 后hooks仍触发
[ ] 移除 _inject_additional_context() 后injection仍处理
[ ] 移除 _handle_confirmation_result() 后确认映射仍正常
[ ] 延迟执行支持保持完整
[ ] AgentContext 注入逻辑完整
```

### 4. approval_bridge 清理测试

```python
[ ] 移除 mode == "never" 的冗余自动批准检查
[ ] mode="always" 时延迟批准正确处理
[ ] mode="per_tool" 时仅 requires_confirmation=True 工具延迟
```

### 5. MCP 集成测试

```python
[ ] MCP 工具的 hooks 正确触发 (不只是直接工具)
[ ] mode="always" 时 MCP 工具确认工作
[ ] mode="per_tool" 时带 requires_confirmation 的 MCP 工具正确处理
```

### 6. Spike 和集成测试

```python
[ ] 验证 ModelRetry 能被 pydantic-ai 正确捕获和重试
[ ] hooks firing 集成测试
[ ] confirmation 集成测试
[ ] 验证返回的 ToolReturn 格式正确
[ ] 修改输入/输出功能正常
[ ] consumption of pending injection 正常
```

### 7. 清理和文档测试

```python
[ ] 清理未使用的导入
[ ] 移除 _handle_confirmation_result 函数
[ ] 移除冗余的 mode == "never" 检查
[ ] 更新相关文档字符串
[ ] 添加 AgentContext.handle_confirmation() 弃用通知
```

### 8. 回归测试

```python
[ ] 运行现有测试套件确保没有破坏
[ ] 修复任何回归
[ ] 运行类型检查
```

## 测试优先级

### 高优先级 (阻塞核心功能)
1. get_wrapper_toolset() 三种模式测试
2. wrap_tool_execute() 异常处理和转换测试  
3. before_tool_execute() deny 处理测试
4. after_tool_execute() modified_output/additional_context 测试

### 中优先级 (重要但非阻塞)
5. 双发问题修复验证
6. wrap_tool() 简化后功能验证
7. MCP 集成测试

### 低优先级 (清理和优化)
8. 清理相关测试
9. 文档更新验证
10. 回归测试

## 下一步行动

1. 创建新测试文件专门测试 _ToolInterceptCapability
2. 实现高优先级测试
3. 运行测试套件验证功能
4. 更新 tasks.md 标记完成的测试