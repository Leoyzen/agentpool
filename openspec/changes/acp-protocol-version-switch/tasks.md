## 1. 版本解析基础设施

- [ ] 1.1 在 `acp/settings.py` 中实现 `resolve_protocol_version(cli_arg, env_var, config_value)` 函数，按优先级链返回最终版本
- [ ] 1.2 完善 `get_settings()` 读取 `ACP_PROTOCOL_VERSION` 环境变量的逻辑，无效值时 warning 日志并回退到 v1
- [ ] 1.3 添加 `ACP_V2_COMPAT_VERSION = "2.0.0-alpha.0"` 常量到 `acp/settings.py`
- [ ] 1.4 在 `agentpool_cli/serve_acp.py` 中添加 `--protocol-version` CLI 参数（`typer.Option`，类型 `int`，可选值 `1|2`）
- [ ] 1.5 将 CLI 参数传入 `ACPServer.from_config()`，与环境变量和代理配置合并
- [ ] 1.6 添加单元测试：验证优先级链（CLI > env > config > default）、无效值处理、v2 启动时记录 compat 版本

## 2. PROTOCOL_VERSION 连接配置系统 + 每连接版本存储

- [ ] 2.1 将 `AgentPoolACPAgent.PROTOCOL_VERSION` 从 `ClassVar = 1` 改为 `field(init=False, default=1)` 实例属性，在 `__post_init__` 中调用 `BaseACPAgentConfig.get_protocol_version()` 获取
- [ ] 2.2 在 `AgentSideConnection` 上添加 `_negotiated_version` 属性，在 `initialize()` 中通过 `min(client, server)` 设置
- [ ] 2.3 将 `_negotiated_version` 通过**方法参数**传递给 `ACPProtocolHandler.handle_prompt()` 等版本感知方法（不存储在共享 handler 实例上，避免并发覆盖）
- [ ] 2.4 在 `ACPEventConverter` 创建时（`_before_consumer_loop`）通过参数传入版本信息（不从共享 handler 状态读取）
- [ ] 2.5 添加测试：config 驱动版本、无 config 回退到默认、v2 server + v1 client 协商到 v1、v1 server + v2 client 协商到 v1
- [ ] 2.6 添加测试：同一服务器实例上并发 v1 和 v2 客户端连接，版本互不干扰

## 3. 版本感知的 initialize 能力协商

- [ ] 3.1 在 `initialize()` 中根据 `_negotiated_version` 分支返回 v1 或 v2 格式的 `InitializeResponse`
- [ ] 3.2 v2 格式：`info` 替代 `agent_info`，`capabilities` 替代 `agent_capabilities`，能力字段使用 `session.*` 作用域化结构
- [ ] 3.3 v2 模式启动时记录 `ACP_V2_COMPAT_VERSION` 到日志
- [ ] 3.4 添加测试：v2+v2 协商 → v2 能力结构；v2 server + v1 client → v1 能力结构；v1 server + v2 client → v1 能力结构

## 4. 统一 Prompt 生命周期双路径

- [ ] 4.1 在 `ACPProtocolHandler.handle_prompt()` 中统一 v2 非阻塞路径与现有 `turn_complete` 路径：`non_blocking = (_negotiated_version == 2) or (client_capabilities.turn_complete)`
- [ ] 4.2 v2 路径：跳过 `run_handle._turn_complete_event.wait()`，立即返回空 `PromptResponse()`
- [ ] 4.3 v2 路径：在 agent 开始执行前发送 `state_update(running)` 通知
- [ ] 4.4 v2 路径：在 agent 完成后发送 `state_update(idle + stop_reason)` 通知，**替代** `TurnCompleteUpdate`（converter 在 v2 模式下跳过 `TurnCompleteUpdate` 发送）
- [ ] 4.5 确保 v2 模式下不发送 `TurnCompleteUpdate`，只发送 `state_update(idle)` — 不产生重复通知
- [ ] 4.6 v1 路径：保持现有阻塞逻辑完全不变，`TurnCompleteUpdate` 行为不变
- [ ] 4.7 添加测试：v2 prompt 立即返回 + state_update 通知序列（running → 内容 → idle+stop_reason）
- [ ] 4.8 添加测试：v2 client 同时声明 turn_complete=True 时不产生重复通知（state_update 替代 TurnCompleteUpdate）
- [ ] 4.9 添加测试：v1 prompt 仍阻塞且发送 TurnCompleteUpdate（非 state_update）
- [ ] 4.10 添加测试：v2 模式下 session/cancel 行为（非阻塞 prompt 返回后 cancel 仍能终止 agent 执行）
- [ ] 4.11 添加测试：v2 模式下 agent 执行出错时 state_update(idle) 仍被发送

## 5. v2 方法路由 + AgentMethod 类型完整性

- [ ] 5.1 在 `acp/schema/messages.py` 的 `AgentMethod` Literal 中添加 `"auth/login"`、`"auth/logout"` 和已缺失的 `"session/set_config_option"`
- [ ] 5.2 在 `acp/agent/connection.py` 的 `_agent_handler()` match 语句中添加 `case "auth/login"` 和 `case "auth/logout"`
- [ ] 5.3 `auth/login` 复用现有 `authenticate()` 逻辑，`auth/logout` 添加空操作 handler
- [ ] 5.4 添加测试：v2 auth/login 路由；v2 auth/logout 返回成功响应；v1 方法在 v1 模式下不受影响

## 6. Deprecation 重定向

- [ ] 6.1 v2 模式下 `session/set_mode` 记录 deprecation 日志并重定向到 `session/set_config_option`（`mode_id` → `category_id="mode"` 配置项）
- [ ] 6.2 v2 模式下 `authenticate` 记录 deprecation 日志并执行现有认证逻辑
- [ ] 6.3 v2 模式下 `session/load` 记录 deprecation 日志但执行原始 load 逻辑（含历史回放，不重定向到 session/resume）
- [ ] 6.4 v1 模式下所有方法行为不变（无 deprecation 日志）
- [ ] 6.5 添加测试：v2 session/set_mode 重定向到 set_config_option；v2 authenticate 重定向到 auth/login 逻辑；v2 session/load 保留原逻辑
- [ ] 6.6 添加测试：v1 模式下无 deprecation 日志

## 7. ACPEventConverter V2 钩子激活

- [ ] 7.1 实现 `_on_state_change(state)` 方法：v2 模式下发送 `state_update` 通知（`running` / `idle`）
- [ ] 7.2 实现 `_on_out_of_turn_update()` 方法：v2 模式下发送非轮次内的会话更新通知
- [ ] 7.3 在 `convert()` 方法中调用 `_on_state_change` 的位置取消空操作注释，接入实际逻辑
- [ ] 7.4 确保 v2 模式下 `state_update(idle)` 替代 `TurnCompleteUpdate`（不发送 TurnCompleteUpdate）
- [ ] 7.5 v1 模式下这些钩子保持空操作，`TurnCompleteUpdate` 行为不变（零回归）
- [ ] 7.6 添加测试：v2 模式 state_update 通知序列；v1 模式无 state_update；顺序正确性

## 8. 会话存储 protocol_version 元数据

- [ ] 8.1 在会话存储格式（`SessionData` 或等效模型）中添加 `protocol_version: int | None` 字段
- [ ] 8.2 `new_session()` 时存储当前协商版本到会话元数据
- [ ] 8.3 `load_session()` 和 `resume_session()` 在恢复时检查存储版本与当前协商版本，不一致时记录 warning 日志
- [ ] 8.4 添加测试：v1 会话在 v2 服务器上恢复时发出 warning；v2 会话在 v2 服务器上恢复无 warning
- [ ] 8.5 添加 Alembic 迁移脚本，在 conversation/session 表上添加 nullable `protocol_version` 列。遵循 `migrations/MIGRATION_GUIDELINES.md`。旧记录默认为 NULL，恢复时触发版本不匹配 warning

## 9. 集成测试与验证

- [ ] 9.1 添加端到端测试：`agentpool serve-acp --protocol-version 2` 启动 → v2 initialize → v2 prompt → state_update 序列
- [ ] 9.2 添加端到端测试：`ACP_PROTOCOL_VERSION=2 agentpool serve-acp` 启动 → v2 行为
- [ ] 9.3 添加回归测试：v1 模式下所有现有 ACP snapshot 测试通过不变
- [ ] 9.4 验证 v2 server + v1 client 降级场景：协商到 v1，行为与纯 v1 server 一致
- [ ] 9.5 验证 v1 server + v2 client 降级场景：协商到 v1，客户端收到 v1 能力声明
- [ ] 9.6 添加并发测试：同一服务器实例上 v1 和 v2 客户端同时连接和交互
- [ ] 9.7 添加 v2 snapshot 测试：v2 事件序列（state_update 替代 TurnCompleteUpdate，验证 TurnCompleteUpdate 不发送）
- [ ] 9.8 运行 `ruff check`、`mypy` 和 `pytest -m "not slow"` (包括 acp_snapshot) 确保无回归和类型错误
