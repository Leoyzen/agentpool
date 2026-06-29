# Tasks: AnyIO CancelScope Nesting and EventBus Backpressure

## 1. Core Infrastructure

- [ ] 1.1 Create BoundedMemoryObjectStream wrapper class
- [ ] 1.2 Add EventBusSettings configuration model
- [ ] 1.3 Add ConcurrentEventPublisher class for parallel safety
- [ ] 1.4 Create unit tests for bounded stream wrapper
- [ ] 1.5 Create unit tests for concurrent publisher

## 2. EventBus Backpressure Implementation

- [ ] 2.1 Replace MemoryObjectStream with BoundedMemoryObjectStream in EventBus
- [ ] 2.2 Implement configurable queue sizes via EventBusSettings
- [ ] 2.3 Add send_timeout and receive_timeout support
- [ ] 2.4 Update event publishers to use bounded streams
- [ ] 2.5 Add integration tests for backpressure scenarios
- [ ] 2.6 Add metrics hooks for queue depth monitoring

## 3. CancelScope Nesting Implementation

- [ ] 3.1 Update AgentPool.spawn_subagent to pass parent CancelScope
- [ ] 3.2 Modify delegation.Team to propagate scope context
- [ ] 3.3 Update TurnRunner for child agent scope inheritance
- [ ] 3.4 Add scope validation and hierarchy tracking
- [ ] 3.5 Create unit tests for cancellation propagation
- [ ] 3.6 Add integration tests for nested subagent scenarios

## 4. Protocol Server Updates

- [ ] 4.1 Update ACP server to use bounded event streams
- [ ] 4.2 Update OpenCode server to use bounded event streams
- [ ] 4.3 Update AG-UI server to use bounded event streams
- [ ] 4.4 Update OpenAI API server to use bounded event streams
- [ ] 4.5 Add error handling for stop operations
- [ ] 4.6 Update all servers to respect backpressure

## 5. Testing and Validation

- [ ] 5.1 Run existing test suite to verify no regressions
- [ ] 5.2 Add stress tests for backpressure scenarios
- [ ] 5.3 Add cancellation propagation tests with deep nesting
- [ ] 5.4 Benchmark performance impact of new synchronization
- [ ] 5.5 Test concurrent producer scenarios
- [ ] 5.6 Validate memory usage under load

## 6. Documentation and Examples

- [ ] 6.1 Update AGENTS.md with CancelScope nesting patterns
- [ ] 6.2 Add EventBus configuration examples
- [ ] 6.3 Document backpressure behavior and tuning guide
- [ ] 6.4 Update protocol server integration docs
- [ ] 6.5 Add troubleshooting section for cancellation issues