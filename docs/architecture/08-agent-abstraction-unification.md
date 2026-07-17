# 08: Agent 抽象统一：Agent、Subagent、Team Member 的关系

## 核心观点

**Agent 是一个统一的执行类。Subagent、Team Member、Lead、Worker 不是不同的类型，
而是同一个 Agent 在不同创建关系、不同地址策略、不同通信模式下的表现形式。**

这个统一视角能极大地简化系统设计：

- 不需要为 subagent 写特殊逻辑
- 不需要为 team member 写特殊类
- 所有 Agent 共享同一套生命周期和通信机制
- 深层嵌套（subagent 创建 subagent）自然发生，不需要额外处理

## 统一模型

```text
Agent
├── created by user/framework      → Native Agent
├── created by another Agent       → Subagent
└── created by Lead within Team    → Team Member
    ├── with coordination role     → Lead
    └── with execution role        → Worker
```

在这个模型下：

- **Agent** 是统一的执行实体
- **Subagent** 是"被另一个 Agent 创建的 Agent"（关系，不是类型）
- **Team Member** 是"在 Team 内保留地址的 Agent"
- **Lead / Worker** 是 Team Member 内部按职责划分的角色

## 概念对照

| 概念 | 本质 | 是否有地址 | 是否保留状态 | 典型生命周期 | 通信模式 |
|---|---|---|---|---|---|
| **Agent** | 统一执行类 | 可选 | 可选 | 由策略决定 | 均可 |
| **Native Agent** | 被用户/框架创建的 Agent | 是 | 是 | 长期 | 与用户/协议交互 |
| **Subagent** | 被另一个 Agent 创建的 Agent | 通常否 | 通常否 | 用完即弃 | Request/Reply |
| **Team Member** | 在 Team 内保留地址的 Agent | 是 | 是 | 可恢复 | Direct / Broadcast |
| **Lead** | 有协调职责的 Team Member | 是 | 是 | 长期/活跃 | 与所有成员通信 |
| **Worker** | 执行具体任务的 Team Member | 是/否 | 是/否 | 用完即弃或可恢复 | 接收任务，返回结果 |

## 通信机制与实体类型解耦

通信机制不是绑定到实体类型的。任何 Agent 都可以使用任何通信机制，
只要它有相应的地址和权限：

| 通信机制 | 说明 | 使用前提 |
|---|---|---|
| **Direct (1:1)** | 地址 A 发送消息到地址 B | 发送方和接收方都有地址 |
| **Broadcast (1:All)** | 地址 A 发送给同一作用域内所有地址 | 发送方有地址，且在同一作用域 |
| **Pub/Sub (Topic)** | 发布到主题，订阅者接收 | 发送方和订阅方都能访问 topic |
| **Blackboard** | 读写共享状态 | 被授权的 Agent 都可以访问 |

因此：
- Subagent 也可以使用 Blackboard，只是它的地址不保留
- Worker 也可以发送 Direct Message 给 Peer
- Lead 也可以订阅 Topic 接收事件

## 生命周期策略决定一切

不同的"类型"不是类型差异，而是**创建时策略**的差异：

```yaml
create_agent:
  role: translator
  lifecycle:
    retain_address: true/false     # 是否可被再次联系
    retain_state: true/false       # 是否保留状态
    recoverable: true/false        # 是否可休眠/唤醒
    warm: true/false               # 是否常驻进程
    ttl: 300s                      # 空闲多久后释放/休眠
```

### 策略组合示例

| 策略组合 | 表现 | 对应旧概念 |
|---|---|---|
| `retain_address: false` | 一次性，用完即弃 | subagent |
| `retain_address: true, recoverable: false` | 可多次调用，但不可恢复 | 简单服务 |
| `retain_address: true, recoverable: true, warm: false` | 按需唤醒 | team member |
| `retain_address: true, recoverable: true, warm: true` | 常驻 | persistent lead |

## 深层嵌套的自然性

因为所有 Agent 都是同一个类，深层嵌套不需要特殊逻辑：

```text
User
  └── Agent A (native, lead)
        └── creates Agent B (subagent / team member)
              └── creates Agent C (subagent)
                    └── creates Agent D (subagent)
```

每一层都是：

1. `create_agent(policy)` — 按策略创建
2. 通过地址发送消息 — 通信
3. 按策略自我管理生命周期 — 结束

Agent B 不需要特殊逻辑来管理 Agent C，
Agent C 也不需要特殊逻辑来管理 Agent D。
生命周期是 self-managed 的。

## 对当前设计的简化

| 之前的设计 | 统一后的设计 |
|---|---|
| `subagent` 是特殊工具 | `subagent` = `create_agent` with `retain_address=false` |
| `team_create` 是特殊工具 | `team_create` = `create_agent` with `retain_address=true` |
| persistent / ephemeral 是两种 agent | 只是生命周期策略不同 |
| 嵌套 subagent 需要特殊逻辑 | 和普通 agent 创建一样 |
| 通信机制绑定到角色 | 通信机制对所有 agent 通用 |

## 地址是核心抽象

在这个统一模型中，**地址**是关键的区分维度：

```text
地址 ≠ agent
地址 ≠ 进程
地址 ≠ 状态
地址 = 可路由的身份标识符
```

是否保留地址，决定了一个 Agent 是：

- **一次性函数调用**（subagent）：不保留地址
- **可联系的实体**（team member）：保留地址

详见 [地址的概念说明](https://github.com/Leoyzen/agentpool/discussions/160)（或后续补充文档）。

## 需要进一步决策的问题

### 1. 创建工具的命名

如果统一为 `create_agent`：

```yaml
tools:
  - create_agent
  - send_message
  - read_blackboard
  - write_blackboard
```

是否还需要保留 `subagent` 和 `team_create` 作为别名？

### 2. 父 Agent 是否对子 Agent 有控制权？

选项 A：父 Agent 只负责创建，子 Agent 自我管理
选项 B：父 Agent 可以强制销毁子 Agent
选项 C：框架根据策略自动管理，父 Agent 和子 Agent 都不直接控制

### 3. 地址作用域如何定义？

```text
team_local      # 只在同 team 内可见
session_global  # 同 session 内可见
global          # 跨 session 可见
```

### 4. 生命周期策略是否允许运行时变化？

例如，一个 Agent 创建时是 `retain_address=false`，
但在运行中因为业务需要被升级为 `retain_address=true`。

## 关联文档

- [07-team-mode-design-space](./07-team-mode-design-space.md)
- [01-vision-and-philosophy](./01-vision-and-philosophy.md)
- [RFC-0055: Dynamic Team Mode](../rfcs/draft/RFC-0055-dynamic-team-mode.md)
- [RFC-0055 design notes](../team-mode/RFC-0055-design-notes.md)
