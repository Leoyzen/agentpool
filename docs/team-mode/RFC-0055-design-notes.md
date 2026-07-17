# RFC-0055 Design Notes: Linking to Local Code and Debt

This document bridges the abstract design of
[RFC-0055: Dynamic Team Mode](../rfcs/draft/RFC-0055-dynamic-team-mode.md) and
the current state of the `packages/agentpool` codebase. It is meant to make the
"what must be true before we implement" explicit.

## What RFC-0055 depends on

RFC-0055 is designed to reuse existing infrastructure. That is a strength, but it
also means the quality of the infrastructure directly determines the quality of
team mode. The main dependencies are:

1. `SessionController` public API.
2. `SessionPool` public API (`send_message`, `run_agent`, `revoke_message`).
3. `RunHandle` delivery mechanisms (`steer`, `followup`).
4. `Capability` injection system.
5. File-based persistence patterns.

## Local code status

The `packages/agentpool` branch `docs/team-agent-architecture` is based on
`develop/agentic`. At this base, the following is true:

- `SessionPool.send_message()` and `run_agent()` exist.
- `RunHandle` exists with `steer` and `followup` methods.
- The `Capability` / `AbstractCapability` system exists.
- `teams:` and `graph:` static team mechanisms exist.

The full `TeamCommCapability` implementation is not yet in this branch; it exists
on the `feat/dynamic-team-mode` branch. This docs branch does not include that
implementation. It only synchronizes the RFC and adds architecture context.

## Link to GitHub issue #170: session-management debt

Issue #170 identifies session-management technical debt that directly affects
Team Mode. The following items are particularly relevant.

| #170 Item | Why it matters for Team Mode | Risk if not fixed |
|---|---|---|
| `close_session` paths fragmented | `team_delete` must close all member sessions. If the path is fragmented, cleanup is inconsistent. | Orphaned sessions, leaked files |
| `SessionData.status` state machine not unified | Team members move between `active`, `idle`, `closed`. Ambiguous states make it hard to know if a member can receive messages. | Messages sent to dead sessions, confusion |
| Storage provider dual paths | Team state files (inbox, blackboard, task board) may be written through one path and read through another. | Data inconsistency, lost messages |
| `_get_active_run_handle` semantics unclear | `send_message` may need to wake up a member. If the run handle lifecycle is unclear, delivery is unreliable. | Missed messages, stalled teams |

**Recommendation**: Treat issue #170 as a prerequisite for RFC-0055 Phase 1. Do
not implement team member lifecycle on top of a fragile session lifecycle.

## Link to other local RFCs

| RFC | Relevance to Team Mode |
|---|---|
| RFC-0037 (Unify Steer and Followup) | `send_message` reuses the delivery model. RFC-0037 should be accepted before RFC-0055. |
| RFC-0042 (Unified Lifecycle Architecture) | Provides the dimensions (Journal, SnapshotStore) that Team Mode persistence can build on. |
| RFC-0028 (Delegation Provider Session Adaptation) | Clarifies how `subagent` sessions relate to parent sessions. Team members are similar but persistent. |
| RFC-0029 (Agent Reactivation via Pending Prompt Queue) | Could be an alternative delivery mechanism for member inbox. Worth comparing before finalizing. |

## Open questions that local code must answer

These are the 6 open questions from RFC-0055, reframed in terms of the current
codebase:

1. **SessionController API stability**
   - Are `receive_request`, `close_session`, and `_get_active_run_handle`
     considered stable public APIs or internal helpers?
   - If they are internal, what is the public API for Team Mode to use?

2. **Capability injection timing**
   - Should `TeamCommCapability` inject tools at agent spawn time or per turn?
   - The existing `Capability` system supports both patterns; which one is used
     for similar capabilities?

3. **Teammate initial prompt**
   - Where does the system prompt injection happen? Is it in `Capability` or in
     `RunHandle`?
   - Can a capability modify the prompt of a spawned session that is not the
     current run?

4. **Broadcast implementation**
   - Should `send_message` to multiple recipients use the `EventBus`, sequential
     delivery, or concurrent `asyncio.gather`?
   - The `EventBus` exists but may not be suitable for cross-session delivery.

5. **Multi-user `user_id` source**
   - In v1, `user_id` defaults to `"default"`. Where is this default set?
   - How will M5 multi-tenant requirements change this?

6. **Task auto-claim**
   - Should the framework enforce auto-claim, or should it be a protocol the LLM
     follows?
   - If the framework enforces it, where does the logic live?

## Suggested participation points

If you want to contribute to Team Mode without owning the core design, here are
concrete entry points:

1. **Issue #170 triage**: Categorize each item by its impact on RFC-0055 and
   propose a fix order.
2. **Capability injection timing**: Audit how existing capabilities inject tools
   and prompt text, then recommend a pattern for `TeamCommCapability`.
3. **Broadcast design**: Compare `EventBus` vs. sequential vs. concurrent
   delivery, write a small prototype, and measure the trade-offs.
4. **File state cleanup**: Design TTL cleanup for team state files. This is a
   bounded, well-defined task.
5. **Multi-user `user_id`**: Propose a `default_user_id` configuration and a
   migration path for M5.

## Implementation checklist

Before declaring RFC-0055 "implemented in this branch":

- [ ] RFC-0055 status updated from `DRAFT` to `IMPLEMENTED` or `IN_PROGRESS`.
- [ ] Issue #170 items that affect Team Mode are resolved or explicitly waived.
- [ ] `TeamCommCapability` and related capabilities are added.
- [ ] Team tools (`team_create`, `team_delete`, `send_message`, `read_blackboard`,
      `write_blackboard`, `task_create`, `task_list`, `task_update`) are
      implemented and tested.
- [ ] File-based inbox, blackboard, and task board are persisted and cleaned up.
- [ ] `auto_init` option works and is tested.
- [ ] Static `graph:` and `teams:` continue to pass existing tests.
- [ ] Documentation is updated to reflect the final design.

## How to use this branch

This branch is intentionally documentation-only. It is meant to be reviewed,
challenged, and merged before the implementation PR. If you disagree with a
claim, open a discussion in the PR for this branch. Do not wait until the
implementation PR to challenge the architecture.
