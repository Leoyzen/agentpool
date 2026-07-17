# 05: Framework Comparison

This document compares how multi-agent frameworks handle team creation,
communication, persistence, and state sharing. It is not a feature scorecard; it
is a survey of patterns that informed AgentPool's design choices.

## Dimensions

| Dimension | Question it answers |
|---|---|
| Team creation | Who decides the team composition — user, program, or LLM? |
| Communication | How do agents send messages to each other? |
| Persistence | How does shared state survive a process restart? |
| Message priority | Are messages ordered by urgency or FIFO? |
| LLM visibility | Can the LLM see and call the communication mechanism? |
| Use case bias | What kind of workflows is the framework optimized for? |

## Comparative matrix

| Framework | Team creation | Communication | Persistence | Message priority | LLM visible | Use case bias |
|---|---|---|---|---|---|---|
| **Qwen Code** | LLM-driven | `send_message` tool | File-based (tempdir) | SHUTDOWN > LEADER > PEER | Yes | Software engineering tasks |
| **OMO (oh-my-openagent)** | LLM-driven, from declared specs | `team_send_message` tool | File-based (3-phase delivery) | FIFO | Yes | General agent orchestration |
| **Hermes** | Program-defined | `delegate_task` (batch) | SQLite + FTS5 | N/A | No | Structured data extraction |
| **OpenCode** | Program-defined | Event-sourced | SQLite events | N/A | No | IDE-integrated coding agent |
| **Zed** | User-initiated | ACP protocol | In-memory | N/A | No | Editor-assisted agent workflows |
| **CrewAI** | Program-defined (roles + tasks) | Crew-level orchestration | In-memory | Task-driven | Partial | Business process automation |
| **AutoGen** | Program-defined (agents + group chat) | Conversational pattern | In-memory | Turn-based | Partial | Research prototyping |
| **AgentPool (target)** | LLM-driven via `team_mode:` | `send_message` tool | File-based (inbox / blackboard / task board) | steer/followup binary urgency | Yes | Multi-protocol agent harness |

## Key observations

### 1. LLM-driven team creation is the production trend

Qwen Code and OMO both let the LLM create teams at runtime. Program-defined
teams (CrewAI, AutoGen, Hermes, OpenCode) are easier to reason about but
require the team to be known at configuration time. For tasks like industrial
diagnosis or manual translation, this is too restrictive.

### 2. Tool-based communication is preferred over program-level channels

When the LLM is the coordinator, communication must be a tool the LLM can call.
`send_message(to, body, urgent)` is a simple, inspectable primitive. Hidden
program-level channels make it impossible to debug why an agent sent a message.

### 3. File-based persistence is common for inspectable shared state

Qwen Code and OMO use files for shared state. This makes the state visible to
operators and does not require a database. The trade-off is that files need TTL
cleanup and are not as fast as in-memory structures for high-frequency updates.

### 4. Message priority is under-specified in most frameworks

Qwen Code has explicit priority levels (SHUTDOWN > LEADER > PEER). Most other
frameworks use FIFO or turn-based ordering. AgentPool's v1 design reuses the
existing `steer/followup` binary urgency model, which keeps the implementation
small but may need richer priority later.

### 5. AgentPool's differentiator is protocol neutrality

Most frameworks are built for a single context (IDE, CLI, research notebook).
AgentPool's goal is to provide the same team primitives across ACP, MCP, AG-UI,
and OpenCode. This is the reason for constraints like "protocol neutrality" and
"configuration over code."

## What we adopt and what we adapt

| Pattern | Source | AgentPool adaptation |
|---|---|---|
| LLM-driven team creation | Qwen Code, OMO | Use `team_mode:` in YAML; reference existing `agents:` definitions |
| `send_message` tool | Qwen Code | Reuse `SessionPool.send_message` / `RunHandle.steer` |
| File-based shared state | Qwen Code, OMO | Use a configurable directory; add blackboard and task board tools |
| Protocol injection into members | OMO | Use AgentPool's `Capability` system for system prompt injection |
| Auto-claim task board | Qwen Code | v1 optional; explicit task board tools first |

## What we explicitly reject

| Pattern | Source | Why we reject it |
|---|---|---|
| Program-only team composition | CrewAI, AutoGen | Too restrictive for runtime task adaptation |
| In-memory-only team state | AutoGen, Zed | Does not survive process restart |
| Database-backed shared state | Hermes | Adds operational dependency; files are sufficient for v1 |
| LLM-defined inline agents | Qwen Code (future) | Out of scope for AgentPool v1; keep to pre-defined agents |
| Worktree-per-member | Some advanced systems | Too heavy for v1; member sessions are sufficient |

## Implications for RFC-0055

Dynamic Team Mode (RFC-0055) is not a clone of Qwen Code or OMO. It is an
AgentPool-specific adaptation that:

- Uses existing `Capability` and `SessionPool` primitives.
- Keeps members as pre-defined agents from the `agents:` section.
- Uses file-based state for the blackboard and task board.
- Defers advanced features (auto-claim, worktree-per-member, cross-team sharing)
  to future versions.

## Open questions from the comparison

1. Is binary urgency (`steer`/`followup`) enough for v1, or should we define a
   richer priority model from the start?
2. Should we adopt OMO's 3-phase delivery model for inbox messages, or is the
   simpler single-file approach sufficient?
3. How do we handle message ordering when a member receives multiple messages
   from different peers concurrently?

These questions are tracked in [RFC-0055 design notes](../team-mode/RFC-0055-design-notes.md).
