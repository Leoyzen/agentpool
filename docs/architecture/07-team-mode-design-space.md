# 07: Dynamic Agent Team 设计空间

> **假设前提**：本文档将技术债务（issue #170）与设计问题解耦。我们假设
> session-management、SessionPool API、RunHandle 等基础设施已经稳定可靠，
> 在此基础上讨论 Dynamic Agent Team 应该是什么、支持什么、不做什么。

## 1. 本文档的目标

本文档把 "Dynamic Agent Team" 从直觉转化为一个可分析的设计空间。

它试图回答：
- Agent Team 由哪些维度构成？
- 每个维度有哪些可选模式？
- RFC-0055 在每个维度上做了哪些选择？
- 还有哪些选择是 v1 没有做的、但未来可能需要做的？

## 2. 核心抽象

一个 Agent Team 不是 "几个 agent 放在一起"，而是由四个要素构成的协作系统：

```
Team = {
  members:       { role_id → agent_instance },
  topology:       谁和谁可以通信,
  shared_state:   blackboard / task board / inbox,
  protocol:       协作规则（怎么分任务、怎么解决冲突、怎么终止）,
  lifecycle:      创建、运行、变化、解散
}
```

## 3. 设计空间矩阵

```
Agent Team 设计空间
├── Role
│   ├── 预定义 vs 运行时定义
│   ├── 单实例 vs 多实例
│   └── Persistent vs Ephemeral
├── Topology
│   ├── Hierarchical / Star / Flat / Pipeline / Market
│   └── Static vs Dynamic
├── Communication
│   ├── Unicast / Multicast / Broadcast / PubSub
│   ├── Sync / Async / Mailbox
│   └── Request-Reply / Fire-Forget / Event
├── State
│   ├── Blackboard (shared KV)
│   ├── Task Board (structured tasks)
│   ├── Inbox (per-agent messages)
│   └── Context Sharing Policy
├── Coordination
│   ├── Lead-driven vs Self-organizing
│   ├── Assignment: Lead assigns / Self-claim / Predefined
│   └── Conflict resolution: Lead / Vote / Rule
├── Lifecycle
│   ├── Create / Run / Pause / Resume / Dissolve
│   ├── Join / Leave
│   └── Termination condition
├── Message Handling
│   ├── Preempt / Queue / Priority / Batch / Escalate
│   └── By agent decision or framework policy
└── Human Boundary
    ├── User talks to Lead only
    ├── User can @ any member
    └── User observes team state
```

## 4. 协作模式：从真实世界到 Agent

| 模式 | 人类组织 | Agent 场景 | 通信特点 | 当前是否被 RFC-0055 覆盖 |
|---|---|---|---|---|
| **层级制 (Hierarchical)** | 经理 → 员工 | Lead → Workers | 垂直通信，Lead 决策 | 是（默认模式） |
| **星型 (Hub-and-Spoke)** | 项目经理协调多个专家 | Lead 作为通信中枢 | 所有 peer 间通信经过 Lead | 是（可模拟） |
| **扁平/对等 (Flat/Peer)** | 敏捷小队 | 自主协作小组 | 任意成员可直接通信 | 部分（send_message 支持） |
| **流水线 (Pipeline)** | 工厂流水线 | 翻译 → 审校 → 一致性检查 | 顺序 handoff | 未显式支持 |
| **市场/竞标 (Market/Bidding)** | 自由职业者接任务 | Agent 看到任务板后认领 | 广播任务，竞争或协商 | 未支持 |
| **委员会 (Committee)** | 董事会投票 | 多个 reviewer 达成共识 | 多轮讨论，最终决策 | 未支持 |
| **蜂群/涌现 (Swarm/Emergent)** | 蚂蚁觅食 | 无中心，通过环境状态间接协调 | 无直接通信，只读写共享状态 | 部分（通过 blackboard） |
| **对手/竞争 (Adversarial)** | 辩论赛 | 正方 vs 反方，然后裁判裁决 | 竞争产出，第三方评估 | 未支持 |

**关键洞察**：一个健康的 Team 机制不应该锁定为某一种模式。它应该能表达多种模式，
或者至少为其他模式预留扩展点。

## 5. 通信模式

### 5.1 按拓扑分

| 模式 | 描述 | 例子 |
|---|---|---|
| **Unicast (1:1)** | A 直接发给 B | Lead 私下问某个 worker 进度 |
| **Multicast (1:N)** | A 发给指定子集 | 通知所有 translator |
| **Broadcast (1:All)** | A 发给所有成员 | glossary 更新 |
| **Pub/Sub (Topic)** | 按主题订阅，不关心发送者 | "chapter_completed" 事件 |
| **AnyCast** | 发给能满足条件的任意一个 | 任务分发，负载均衡 |

### 5.2 按语义分

| 模式 | 描述 | 例子 |
|---|---|---|
| **Request/Reply** | 发送消息并等待响应 | Lead 问 worker 术语翻译 |
| **Fire-and-Forget** | 发送不等待 | 广播状态更新 |
| **Event/Notification** | 状态变化通知 | "task X 完成" |
| **Command** | 要求对方做某事 | "停止当前任务" |
| **Query** | 询问当前状态 | "你的进度是多少？" |

### 5.3 按持久性分

| 模式 | 描述 | 适用 |
|---|---|---|
| **Synchronous** | 实时，等待对方处理 | 同步 delegation |
| **Asynchronous** | 放入 inbox，对方空闲时处理 | 大多数 team 通信 |
| **Mailbox** | 持久化队列，可跨重启 | 长任务团队 |

## 6. 成员生命周期：加入与退出

如果 Team 是围绕任务创建的，成员生命周期不能只有 Create/Delete。

### 6.1 加入机制

| 方式 | 描述 | 场景 |
|---|---|---|
| **初始成员 (Initial)** | team_create 时确定 | 固定小队 |
| **运行时加入 (Runtime join)** | 任务进行中补充成员 | 发现需要新专家 |
| **自我加入 (Self-join)** | agent 看到任务后认领加入 | 市场/竞标模式 |
| **复制/分身 (Clone)** | 一个 role 实例化多个 | 翻译需要 5 个 translator |

### 6.2 退出机制

| 方式 | 描述 | 场景 |
|---|---|---|
| **任务完成退出** | 成员完成自己的子任务后退出 | ephemeral worker |
| **显式移除** | Lead 或管理员移除 | 成员异常或不再需要 |
| **超时退出** | 超过 TTL 自动退出 | 防止孤儿 |
| **团队解散** | team_delete 时全部退出 | 团队结束 |

### 6.3 关键问题

成员加入或退出时，其他成员是否收到事件？

- 如果知道：blackboard 里的成员信息不会 stale，team_status 可信。
- 如果不知道：其他成员可能给已退出的成员发消息，导致消息丢失或错误。

**建议**：成员变化应该是一种事件，广播给所有存活成员。

## 7. 消息处理策略

正在工作的 Agent 收到新消息，有几种策略：

| 策略 | 行为 | 优点 | 缺点 | 是否适合 Harness 哲学 |
|---|---|---|---|---|
| **立即中断 (Preempt)** | 停下当前工作，处理新消息 | 响应快 | 上下文丢失，容易 thrashing | 部分（由 LLM 决定） |
| **队列 (Queue)** | 放入 inbox，当前 turn 结束后处理 | 简单，不丢消息 | 高优先级消息可能延迟 | 是 |
| **优先级抢占 (Priority Preempt)** | 只有高优先级消息才中断 | 平衡响应和专注 | 需要优先级模型 | 是 |
| **批量 (Batch)** | 收集多条消息后一起处理 | 减少上下文切换 | 延迟增加 | 部分 |
| **忽略/丢弃 (Drop)** | 忙碌时丢弃低优先级消息 | 防止过载 | 可能丢重要消息 | 否（框架不应替 LLM 丢弃） |
| **转交 (Escalate)** | 把消息转给 Lead 或指定代理 | 避免单个 agent 过载 | 增加 Lead 负担 | 是 |
| **原子工作单元 (Atomic Unit)** | 当前子任务不可分割，完成后才处理 | 保证完整性 | 长任务会阻塞消息 | 部分 |

**关键设计决策**：策略是由 Agent 自己决定，还是由框架决定？

- **Harness 哲学**：框架提供工具和参数（如 `urgent`），由 LLM/Agent 决定具体行为。
- **Framework 哲学**：框架强制规定策略（如 "忙碌时一律 queue"）。

RFC-0055 当前更接近 Harness：提供 `urgent` 参数，但具体行为由工具调用和系统 prompt 决定。

## 8. RFC-0055 当前选择映射

| 设计维度 | RFC-0055 v1 选择 | 是否可能扩展 |
|---|---|---|
| **Role 定义** | 预定义，引用 `agents:` | 是，未来可能支持 inline agent 定义 |
| **Topology** | 默认 Star/Hub（Lead 为中心） | 是，可扩展为多拓扑 |
| **Communication** | Unicast + Broadcast，Async，Mailbox（文件） | 是，可能加 Pub/Sub topic |
| **State** | Blackboard + Task Board + Inbox | 较稳定 |
| **Coordination** | Lead-driven | 是，可能支持 self-claim / market |
| **Lifecycle** | Create / Run / Delete，无 Join/Leave | 是，v2 可能加 Join/Leave |
| **Message Handling** | Queue（二进制 steer/followup） | 是，可能加 preempt / batch |
| **Human Boundary** | 用户只和 Lead 对话 | 是，可能允许用户 @ 成员 |

## 9. 当前未决设计问题

### 9.1 Team 拓扑是设计时定还是运行时定？

- **设计时定**：YAML 里写 `team_mode.topology: star`，LLM 只能在这个结构里协作。
- **运行时定**：LLM 通过 `team_create` 自己决定谁和谁通信。

RFC-0055 是运行时定，但只是默认 Lead 为中心。如果未来想支持 Pipeline，
是加配置项，还是让 LLM 通过工具约束实现？

### 9.2 Communication 是否需要 Pub/Sub？

现在只有 `send_message(to, body)` 和广播。但如果要表达：
- 只有 translator 需要听 glossary 更新
- 只有 consistency checker 需要听 chapter_completed

没有 topic，就只能靠 Lead 转发或每个 agent 自己过滤。这会让 Lead 成为瓶颈。

### 9.3 是否需要 Join/Leave 机制？

RFC-0055 v1 只有 Create/Delete。但真实任务里经常需要：
- 翻译到一半发现需要法律专家
- 某个 worker 完成后退出，但团队继续

如果 v1 不支持，很多场景会被迫拆成多个 team。

### 9.4 Blackboard 与 Task Board 的边界是否清晰？

两者都是共享状态：
- Blackboard：自由 KV，适合 glossary、风格指南
- Task Board：结构化任务，适合章节翻译、依赖跟踪

这个区分是否足够？未来是否会有第三种状态（如 conversation memory）？

### 9.5 团队终止条件由谁决定？

- Lead 宣布完成？
- Task Board 全部完成？
- 用户确认？
- 超时？

终止条件不同，团队生命周期工具的语义也不同。

## 10. 建议的后续输出

基于本文档，建议产出：

1. **DDR-002: Team Topology 选择**（为什么 v1 默认 Star/Hub）
2. **DDR-003: Communication 模式选择**（为什么 v1 无 Pub/Sub）
3. **DDR-004: Lifecycle 范围选择**（为什么 v1 无 Join/Leave）
4. **DDR-005: Message Handling 策略选择**（为什么 v1 是二进制 priority）

## 11. 关联文档

- [RFC-0055: Dynamic Team Mode](../rfcs/draft/RFC-0055-dynamic-team-mode.md)
- [RFC-0055 design notes](../team-mode/RFC-0055-design-notes.md)
- [01-vision-and-philosophy](./01-vision-and-philosophy.md)
- [02-system-overview](./02-system-overview.md)
- [03-problem-space](./03-problem-space.md)
- [04-constraints-and-principles](./04-constraints-and-principles.md)
- [05-framework-comparison](./05-framework-comparison.md)
- [06-rfc-roadmap](./06-rfc-roadmap.md)
- [06-decisions/DDR-001-why-dynamic-team-mode](./06-decisions/DDR-001-why-dynamic-team-mode.md)
