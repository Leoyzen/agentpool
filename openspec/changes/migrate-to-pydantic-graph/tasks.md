## 1. AgentNode Prototype (Phase 3a)

- [ ] 1.1 Create `AgentNode` dataclass extending `pydantic_graph.BaseNode[ChatMessage, AgentContext, ChatMessage]`
- [ ] 1.2 Implement `AgentNode.run()` that wraps agent `process()` with session creation via `SessionPool`
- [ ] 1.3 Handle event wrapping: collect agent events and return as `ChatMessage`
- [ ] 1.4 Test `AgentNode` with single native agent in a simple graph
- [ ] 1.5 Verify streaming events flow correctly through `AgentNode`
- [ ] 1.6 Verify session tree is created correctly (parent = graph run, child = node execution)
- [ ] 1.7 Benchmark `AgentNode` overhead vs direct `agent.run()`

## 2. Parallel Team Graph — YAML Only (Phase 3b)

- [ ] 2.1 Implement `ParallelTeamGraph` using `GraphBuilder` + `Fork` + `Join` for YAML config
- [ ] 2.2 Map YAML `mode: parallel` team members to `AgentNode` instances in `Fork`
- [ ] 2.3 Implement result collection via `Join` with `ChatMessage` union type
- [ ] 2.4 Keep programmatic `agent & other` using `asyncio.gather()` (unchanged)
- [ ] 2.5 Write tests for YAML parallel team graph execution
- [ ] 2.6 Write backward-compat tests ensuring programmatic `Team` still works
- [ ] 2.7 Benchmark YAML parallel graph vs `asyncio.gather()` for 2-3 agent teams

## 3. Sequential Team Graph — YAML Only (Phase 3c)

- [ ] 3.1 Implement `SequentialTeamGraph` using `GraphBuilder` sequential chaining
- [ ] 3.2 Map YAML `mode: sequential` team members to chained `AgentNode` instances
- [ ] 3.3 Keep programmatic `agent | other` using custom forwarding (unchanged)
- [ ] 3.4 Write tests for YAML sequential team graph execution
- [ ] 3.5 Write backward-compat tests ensuring programmatic `TeamRun` still works

## 4. Conditional Workflows & Cycle Detection (Phase 3d)

- [ ] 4.1 Add `Decision` node support for YAML workflow definitions
- [ ] 4.2 Implement build-time cycle detection for graph workflows
- [ ] 4.3 Reject cyclic YAML configs with clear error message
- [ ] 4.4 Write tests for conditional branching in YAML workflows
- [ ] 4.5 Write tests verifying cycle detection rejects cyclic configs
- [ ] 4.6 Enable Mermaid diagram generation for YAML team/workflow definitions
- [ ] 4.7 Add `agentpool visualize <name>` CLI command

## 5. Non-Native Agent Adapters (Phase 3e)

- [ ] 5.1 Implement `ClaudeCodeNode(BaseNode)` adapter for Claude Code agents
- [ ] 5.2 Implement `ACPNode(BaseNode)` adapter for ACP agents
- [ ] 5.3 Implement `AGUINode(BaseNode)` adapter for AG-UI agents
- [ ] 5.4 Verify heterogeneous teams (native + Claude + ACP) in graph execution
- [ ] 5.5 Write tests for non-native agent node adapters

## 6. Integration & Stabilization (Phase 3f)

- [ ] 6.1 End-to-end integration test: YAML config → graph construction → execution
- [ ] 6.2 Benchmark parallel graph vs `asyncio.gather()` for simple teams
- [ ] 6.3 Benchmark sequential graph vs custom forwarding for simple pipelines
- [ ] 6.4 Verify protocol servers work with graph-structured YAML teams
- [ ] 6.5 Update YAML config documentation for graph-based team definitions
- [ ] 6.6 Document which team patterns use graphs vs which keep custom implementation
- [ ] 6.7 Run complete test suite
