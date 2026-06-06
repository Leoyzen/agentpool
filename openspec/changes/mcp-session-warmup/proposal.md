# MCP Session Warmup

## Problem

当前 lazy MCP 连接机制在第一次使用时才初始化，这导致 agent 在首次运行时可能无法拿到完整的工具列表，影响 system prompt 中 tool 描述的注入。虽然 PydanticAI 的 capability 路径不受影响，但 legacy/ACP 路径下的 agent 在首响时可能缺少工具信息。

## Solution

在 protocol session 连接时异步进行 warmup，触发所有 lazy MCP provider 的连接建立，确保 agent 在执行前已经拿到所有工具描述。

## Scope

- ACP server session 建立时 warmup
- OpenCode server session 建立时 warmup  
- AG-UI server session 建立时 warmup
- 不影响 eager 连接的 provider（幂等）
- 失败时优雅降级（记录日志但不阻塞 session）

## Success Criteria

- [ ] Session 建立后，lazy MCP providers 已连接
- [ ] Agent 首响时 system prompt 包含完整 tool 描述
- [ ] Warmup 失败不阻塞 session 建立
- [ ] 不影响 eager 连接的性能
