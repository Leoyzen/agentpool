## 1. DispatchAgent 实现

- [ ] 1.1 创建 `src/agentpool_server/acp_server/shared/dispatch_agent.py`
- [ ] 1.2 实现 `__init__`：接收 `client`、`default_agent`、`debug_commands`、`load_skills`、`server`、`subagent_display_mode`，初始化 `_delegate = None`
- [ ] 1.3 实现 `initialize()`：读取 `protocolVersion`，调用 `VersionNegotiator.negotiate()`，根据结果创建 v1 或 v2 agent 作为 `_delegate`，委托 `delegate.initialize(params)` 返回响应
- [ ] 1.4 实现 SessionPool 降级逻辑：v2 请求但 `use_session_pool=False` 时创建 v1 agent，返回 `protocolVersion=2` + `_meta.fallback=true`
- [ ] 1.5 实现显式委托方法：`new_session`、`prompt`、`cancel`、`close_session`、`load_session`、`list_sessions`、`fork_session`、`resume_session`、`set_session_config_option`、`ext_method`、`ext_notification`、`close`
- [ ] 1.6 实现 v1 方法名：`authenticate`、`logout`、`set_session_mode`、`set_session_model`（委托给 v1 delegate 或对 v2 delegate 返回错误）
- [ ] 1.7 实现 v2 方法名：`auth_login`、`auth_logout`（委托给 v2 delegate 或对 v1 delegate 返回错误）
- [ ] 1.8 实现 `__getattr__` 动态兜底委托：未显式定义的属性转发到 `self._delegate`

## 2. server.py 集成

- [ ] 2.1 在 `server.py` 中 import `DispatchAgent`
- [ ] 2.2 将 `_start_async()` 中的 `functools.partial(AgentPoolACPAgent, ...)` 替换为 `functools.partial(DispatchAgent, ...)`
- [ ] 2.3 删除临时版本路由注释（"v2 路径暂返回 NotImplementedError" 等）
- [ ] 2.4 验证 `serve()` 接受 DispatchAgent factory 正常工作

## 3. 测试

- [ ] 3.1 创建 `tests/servers/acp_server/test_dispatch_agent.py`
- [ ] 3.2 测试 v1 initialize → 创建 v1 delegate，返回 protocolVersion=1
- [ ] 3.3 测试 v2 initialize → 创建 v2 delegate，返回 protocolVersion=2
- [ ] 3.4 测试 v0 initialize → 抛出 RequestError
- [ ] 3.5 测试 SessionPool 未启用时 v2 降级：创建 v1 delegate，返回 protocolVersion=2 + fallback=true
- [ ] 3.6 测试委托完整性：initialize 后调用 prompt/cancel/new_session 等，验证委托到 delegate
- [ ] 3.7 测试 v1 方法名（authenticate）在 v1 delegate 上工作
- [ ] 3.8 测试 v2 方法名（auth_login）在 v2 delegate 上工作
- [ ] 3.9 测试 `__getattr__` 兜底委托
- [ ] 3.10 跑全量 v1 测试确认无回归

## 4. Lint 与验证

- [ ] 4.1 `uv run ruff check src/agentpool_server/acp_server/shared/dispatch_agent.py tests/servers/acp_server/test_dispatch_agent.py --select F401,F811,F841,E9,W6,I`
- [ ] 4.2 `uv run pytest tests/servers/acp_server/ tests/acp_v2/ -q -m unit` 全量通过
