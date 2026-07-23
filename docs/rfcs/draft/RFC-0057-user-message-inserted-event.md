---
rfc_id: RFC-0057
title: "UserMessageInsertedEvent: Displaying Steer/Followup Messages as User Messages in Protocol Frontends"
status: DRAFT
author: yuchen.liu
reviewers:
  - name: TBD
    status: pending
created: 2026-07-21
last_updated: 2026-07-21
decision_date:
related_rfcs:
  - RFC-0056 (SystemNotificationEvent — complementary future work, NOT a dependency)
  - RFC-0037 (Unify Steer and Followup — steer/followup are sync, constrains emission)
  - RFC-0042 (Unified Lifecycle Architecture — CommChannel/EventBus foundation)
  - RFC-0013 (Subagent Event Unification — SpawnSessionStart precedent for synthetic events)
related_specs:
  - openspec/changes/steer-followup-user-message-display/ (implementation spec — proposal, design, specs, tasks)
  - openspec/specs/unified-event-routing/ (event routing rules — bans event_queue as channel)
  - openspec/specs/steer-followup-api/ (steer/followup API — sync methods)
  - openspec/specs/acp-server/ (ACP server behavior)
---

# RFC-0057: UserMessageInsertedEvent — Displaying Steer/Followup Messages as User Messages in Protocol Frontends

## Overview

AgentPool's steer/followup mechanism allows messages to be injected into an active agent session — `steer()` injects mid-turn (priority `"asap"`), while `followup()` queues for the next turn (priority `"when_idle"`). When these messages are injected, the frontend (ACP client like Zed, or OpenCode TUI) has no unified mechanism to display them as user messages in the conversation transcript.

Current behavior is ad-hoc per protocol handler: ACP's `handle_prompt()` emits `UserMessageChunk` for every `session/prompt` call (including when busy), and OpenCode's `message_routes.py` creates a `UserMessage` before routing — but internal steer paths (background tasks, programmatic API) bypass protocol handlers entirely, leaving the user with no visibility into what was injected.

This RFC proposes adding a first-class `UserMessageInsertedEvent` to `RichAgentStreamEvent`, published from the single routing choke point (`SessionController._route_message()`), and handled by both ACP and OpenCode protocol converters to display the inserted content as `role="user"` messages. This is distinct from RFC-0056's proposed `SystemNotificationEvent` (which would render as system-level `ToolPart(tool="system")` notifications) — this RFC renders as actual user messages in the transcript. RFC-0056 is complementary future work and is **not** a dependency of this RFC.

## Table of Contents

- [Background & Context](#background--context)
- [Problem Statement](#problem-statement)
- [Goals & Non-Goals](#goals--non-goals)
- [Evaluation Criteria](#evaluation-criteria)
- [Options Analysis](#options-analysis)
- [Recommendation](#recommendation)
- [Technical Design](#technical-design)
- [Security Considerations](#security-considerations)
- [Implementation Plan](#implementation-plan)
- [Open Questions](#open-questions)
- [Decision Record](#decision-record)
- [References](#references)

---

## Background & Context

### Current State

**Steer/Followup Architecture**: Protocol handlers → `handle_prompt()` → `session_pool.send_message()` → `session_pool_messaging.py` → `SessionController._route_message()` → routing based on session state and priority:
- Idle session → `_start_run_handle()` → new `RunHandle` + background `_consume_run()` task
- Busy + `priority="asap"` (steer) → `run.steer(content)` → `agent_run.enqueue(priority="asap")` → PydanticAI `PendingMessageDrainCapability` drains at `before_model_request`
- Busy + `priority="when_idle"` (followup) → `session.prompt_queue.put_nowait(content)` → `_consume_run()` picks up after current turn

`RunHandle.steer()` and `followup()` are **synchronous** methods (constrained by RFC-0037). They do not publish any EventBus event.

**User Message Display — Current Ad-Hoc Mechanisms**:

| Protocol | Mechanism | Covers steer? | Covers internal steer? |
|---|---|---|---|
| ACP | `handle_prompt()` → `_emit_user_message_chunks()` for every `session/prompt` | ✅ (if client sends 2nd prompt while busy) | ❌ |
| OpenCode | `message_routes.py` creates `UserMessage` + broadcasts `MessageUpdatedEvent` before routing | ✅ (REST handler creates it regardless of delivery) | ❌ |

Internal steer paths (`steer_from_background_task()`, programmatic API) bypass protocol handlers entirely.

**ACP Protocol Versions**:

| Feature | v1 | v2 |
|---|---|---|
| `user_message_chunk` (streaming single block) | ✅ | ✅ |
| `user_message` (whole-message upsert with patch semantics) | ❌ | ✅ (added v0.13.7, June 2026, `unstable-v2`) |
| Zed support | ✅ (current) | ❌ |
| AgentPool Python schema | ✅ `UserMessageChunk` | ❌ `UserMessage` not yet defined |

The v2 `user_message` variant is described as "Agents can send this when they accept or replay a user message." The agent is the "source of truth for message IDs" and can send `user_message` updates at any point while the session exists.

**ACP Protocol Version Storage**: The negotiated protocol version is stored on the ACP agent instance (`acp_agent.py:380`). The `ACPEventConverter` currently does **not** have a `protocol_version` field — this RFC adds one (see Technical Design).

**RFC-0056 (SystemNotificationEvent) — Complementary Future Work**: A complementary RFC that proposes adding `SystemNotificationEvent` to `RichAgentStreamEvent` for system-level notifications (background task completion, lifecycle events, steer/followup injection). It would render as `ToolPart(tool="system")` with `metadata.system_notification=True` in the OpenCode TUI. ACP bridge mapping is deferred. This RFC-0057 is distinct: it renders as `role="user"` messages, not system notifications. RFC-0056 is **not** a dependency — this RFC is fully implementable without it. If RFC-0056 is implemented in the future, both event types can coexist (see Decision on OQ5 below).

### Historical Context

- **ACP v1** has `user_message_chunk` but no `user_message` (whole-message upsert). The v2 schema added `user_message` in v0.13.7 under `unstable-v2` feature flag.
- **OpenCode TUI** renders user messages via `session.next.prompted` event → `SessionMessage.User` → timeline projection. The `delivery` field (`"steer"` | `"queue"`) does NOT affect display — only execution timing.
- The `unified-event-routing` spec explicitly bans `run_ctx.event_queue` as an event channel and states "Business layer code SHALL NOT perform manual event routing."

### Glossary

| Term | Definition |
|------|------------|
| `RichAgentStreamEvent` | PEP 695 union type of event variants flowing through the agent stream |
| `EventBus` | In-process pub/sub for agent events, scoped by session |
| `EventProcessor` | OpenCode server component that converts `RichAgentStreamEvent` → OpenCode SSE events |
| `ACPEventConverter` | ACP server component that converts `RichAgentStreamEvent` → ACP `SessionUpdate` notifications |
| `UserMessageChunk` | ACP v1/v2 `SessionUpdate` variant — streaming single content block |
| `UserMessage` | ACP v2 `SessionUpdate` variant — whole-message upsert with patch semantics (NOT in v1) |
| `CommChannel` | M2 lifecycle dimension that journals events before delivery (owns Journal) |
| `SystemNotificationEvent` | RFC-0056 proposed event type — renders as system notification (`ToolPart(tool="system")`) |
| `UserMessageInsertedEvent` | This RFC's proposed event type — renders as user message (`role="user"`) |
| `ProtocolEventConsumerMixin` | Base mixin for protocol server EventBus consumers |
| `send_message()` | Entry point on `SessionPool` for routing prompts; called by protocol handlers via `session_pool_messaging.py` |
| `_route_message()` | Internal routing method on `SessionController`, the single choke point for all message routing |

---

## Problem Statement

### The Problem

Three categories of user message insertion are invisible to frontends:

1. **Internal steer from background tasks**: `steer_from_background_task()` in `session_controller.py:305-333` calls `RunHandle.steer()` directly. No protocol handler is involved. The frontend has no visibility into what was injected.

2. **Followup from `_consume_run()` chaining**: When a followup is picked up from `prompt_queue`, `_consume_run()` → `_create_per_prompt_handle()` (line 150) creates a new `RunHandle` directly WITHOUT calling `_route_message()`. If the followup was queued internally (not through a protocol handler), no user message event is emitted.

3. **No unified mechanism**: User message display is ad-hoc per protocol handler. There is no EventBus event for user message insertion that internal paths could publish. The `RichAgentStreamEvent` union has 21 types, none for user message display.

### Evidence

- **Code inspection**: `RunHandle.steer()` (`orchestrator/run.py:530-586`) and `followup()` (`orchestrator/run.py:599-621`) publish no EventBus event. `steer_from_background_task()` (`session_controller.py:305-333`) calls `steer()` directly — it is a **synchronous** method using `_active_steer_callback`.
- **ACP handler**: `handle_prompt()` (`handler.py:461-636`) calls `_emit_user_message_chunks()` for every `session/prompt` request, but this is in the protocol handler, not in the routing layer. `_emit_user_message_chunks()` generates `message_id` internally via `build_user_message_chunks()` at `event_converter.py:294` and sends directly to client via `self.client.session_update(notification)`, bypassing `ACPEventConverter` entirely.
- **OpenCode handler**: `message_routes.py` creates `UserMessage` and broadcasts `MessageUpdatedEvent` before routing, but only for REST-originated messages. There are 6+ `UserMessage` creation sites in OpenCode (`message_routes.py:311,884`, `session_routes.py:414,638`, `opencode_event_bridge.py:368,638`) — all need dedup wiring.
- **Call chain**: Protocol handlers call `handle_prompt()` → `session_pool.send_message()` → `session_pool_messaging.py` → `SessionController._route_message()`. The method `receive_request()` does **not** exist — `send_message()` is the actual entry point.
- **`_meta` extraction**: `handle_prompt()` does NOT receive `_meta` directly. `_meta` is extracted at `acp_agent.py:698` for trace context but NOT forwarded to `handle_prompt()`.
- **Followup-from-queue gap**: `_consume_run()` → `_create_per_prompt_handle()` (line 150) creates `RunHandle` directly without calling `_route_message()`, bypassing event publication entirely.
- **EventBus event types**: `events.py` defines 21 event types in `RichAgentStreamEvent`. None carry user message content for display purposes.

### Impact of Inaction

- **User experience**: When a background task completes and steers the model, the user sees the model's response change but has no visibility into what was injected. This is confusing for multi-agent workflows.
- **Protocol gap**: Internal steer paths are invisible. Users relying on ACP clients (Zed) or OpenCode TUI cannot see programmatic injections.
- **Debugging**: Without user message visibility, debugging steer-related behavior requires inspecting logs rather than seeing the conversation transcript.

---

## Goals & Non-Goals

### Goals (In Scope)

1. Add `UserMessageInsertedEvent` to `RichAgentStreamEvent` union with typed fields (`session_id`, `message_id`, `content`, `delivery`, `source`, `timestamp`).
2. Publish `UserMessageInsertedEvent` from `SessionController._route_message()` for all routing paths (idle, steer, followup), if EventBus is available.
3. Publish `UserMessageInsertedEvent` from `steer_from_background_task()` for internal steer visibility, if EventBus is available.
4. Publish `UserMessageInsertedEvent` from `_consume_run()` for followup-from-queue visibility, if EventBus is available.
5. Add `emit_user_message` parameter to `RunHandle.steer()` (default `True`) and `followup()` (default `False`) for fire-and-forget emission via `asyncio.create_task()`.
6. Handle `UserMessageInsertedEvent` in ACP `ACPEventConverter` — emit `UserMessageChunk` (v1) or `UserMessage` (v2), with `protocol_version` passed to the converter constructor.
7. Handle `UserMessageInsertedEvent` in OpenCode `EventProcessor` — create `UserMessage` and broadcast `MessageUpdatedEvent`.
8. Add `UserMessage` Pydantic model to ACP Python schema (`session_updates.py`).
9. Extract `delivery` from `_meta` in `acp_agent.py:prompt()` (line ~698) and pass it as a `delivery` parameter through `handle_prompt()` → `send_message()` → `_route_message()`.
10. Deduplication by `message_id` using a shared dedup set accessible by BOTH protocol handler emission paths AND `ACPEventConverter`/`EventProcessor` (EventBus path).
11. Audit all 27 `match event:` sites across 20 files for exhaustive handling of the new type.
12. Audit all 6+ OpenCode `UserMessage` creation sites for dedup wiring.

### Non-Goals (Out of Scope)

1. **Replacing `SystemNotificationEvent` (RFC-0056)** — both coexist for different purposes (user message vs system notification). RFC-0056 is complementary future work, not a dependency.
2. **ACP protocol schema changes** — only the Python schema gets `UserMessage` added; no upstream ACP protocol modification.
3. **`reply_to` / `parent_message_id` correlation** — ACP has no such field. Correlation is by timing (steer `UserMessageChunk` followed by new `AgentMessageChunk`).
4. **OS-level desktop notifications** — `ToastInfo` remains the chrome-level channel.
5. **Journal/CommChannel persistence** — `UserMessageInsertedEvent` bypasses CommChannel (point-in-time signal, never journaled, never replayed).
6. **Modifying `SystemNotificationEvent`** or its rendering path.
7. **Team mode tool modifications** — `source="background_task"` covers the primary internal use case; team-specific `source="team"` can be added in a follow-up.

### Success Criteria

- [ ] An internal `steer_from_background_task()` call produces a visible `role="user"` message in the OpenCode TUI.
- [ ] An ACP v1 client receives `UserMessageChunk` for a steer message injected internally.
- [ ] An ACP v2 client receives `UserMessage` (whole-message upsert) for a steer message.
- [ ] `RunHandle.steer(emit_user_message=True)` produces a `UserMessageInsertedEvent` in the EventBus, if EventBus is available.
- [ ] `RunHandle.steer(emit_user_message=False)` suppresses the event from `steer()` (event may still come from `_route_message()`).
- [ ] A followup message picked up from `prompt_queue` by `_consume_run()` produces a `UserMessageInsertedEvent`, if EventBus is available.
- [ ] No double display when both protocol handler and EventBus event fire (dedup by `message_id` via shared dedup set).
- [ ] All 27 `match event:` sites across 20 files either handle `UserMessageInsertedEvent` or have a catch-all.
- [ ] All 6+ OpenCode `UserMessage` creation sites have dedup wiring.
- [ ] No breaking changes to existing `steer()`/`followup()` callers (new parameter has default).

---

## Evaluation Criteria

| Criterion | Weight | Description | Minimum Threshold |
|-----------|--------|-------------|-------------------|
| Type safety | High | Static type checker (mypy/pyright) can distinguish the event in `match` dispatch | Must be a distinct type |
| Spec compliance | High | Does not violate `unified-event-routing` spec (no `event_queue`, no business-layer routing) | Must pass spec audit |
| Protocol coverage | High | Works for both ACP (v1 and v2) and OpenCode | Must cover v1, v2, and OpenCode |
| Internal path coverage | High | Covers `steer_from_background_task()` and programmatic API | Must cover all internal paths |
| Implementation effort | Medium | Lines of code, files touched, complexity | ≤ 10 files modified for core implementation |
| Backward compatibility | High | No breaking changes to existing consumers | All existing `match` with `case _:` unaffected |
| Dedup reliability | Medium | No double display in any scenario | Must handle all dedup cases |
| ACP v1 compatibility | High | Works with current Zed (ACP v1) | Must use `UserMessageChunk` for v1 |
| Crash recovery safety | Medium | No duplicate messages on crash recovery replay | Must not journal or must filter on replay |

---

## Options Analysis

### Decision 1: Event Type Design

#### Option 1A: New `UserMessageInsertedEvent` dataclass (Recommended)

**Description**

Add a new `@dataclass(frozen=True) UserMessageInsertedEvent` to `events.py` and add it to the `RichAgentStreamEvent` union. Fields: `session_id`, `message_id`, `content` (`str | list[Any]` for multi-modal support), `delivery` (Literal["initial", "steer", "followup"]), `source` (Literal["protocol", "background_task", "internal"]), `timestamp`.

**Advantages**

- Type-safe `match` dispatch: `case UserMessageInsertedEvent(delivery=d, ...)` is self-documenting and exhaustive-checkable.
- Distinct from `SystemNotificationEvent` (RFC-0056) — different rendering target (`role="user"` vs `ToolPart(tool="system")`).
- Fields are specific to user message insertion (`delivery`, `source`) — not a generic payload.
- Follows the existing pattern of typed event dataclasses in the union.

**Disadvantages**

- Adds a 22nd type to the union — all exhaustive `match` statements need a new case.
- Slightly more code than reusing an existing type.

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Type safety | ✅ High | Distinct type, static dispatch |
| Spec compliance | ✅ Pass | Uses `event_bus.publish()`, not `event_queue` |
| Protocol coverage | ✅ High | Can be mapped to both ACP and OpenCode formats |
| Implementation effort | Medium | 1 new dataclass + union update + protocol handler cases |
| Backward compatibility | ✅ Pass | Catch-all `case _:` unaffected |
| Crash recovery safety | ✅ Pass | Bypasses CommChannel — not journaled |

**Effort Estimate**

- Complexity: Low-Medium
- Files: 8-10 (event type, session controller, run handle, ACP converter, ACP schema, OpenCode processor, tests, docs)
- Dependencies: None

**Risk Assessment**

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Exhaustive match sites break | Low | Low | Audit task covers all 27 sites across 20 files; most have `case _:` catch-all |
| Double display if dedup fails | Medium | Medium | `message_id` shared dedup set per session; protocol handlers generate ID first and register before emitting |

---

#### Option 1B: Reuse `SystemNotificationEvent` with `source="steer"` (from RFC-0056)

**Description**

Use RFC-0056's proposed `SystemNotificationEvent` with `source="steer"` or `source="followup"` to carry user message content. The OpenCode `EventProcessor` and ACP `ACPEventConverter` would need to special-case `source="steer"` to render as `role="user"` instead of `ToolPart(tool="system")`.

**Advantages**

- No new union member — no exhaustive match impact.
- Less code (no new dataclass).
- Reuses existing RFC-0056 infrastructure.

**Disadvantages**

- **Semantic conflation**: `SystemNotificationEvent` renders as `ToolPart(tool="system")` — a system notification. User messages need `role="user"` rendering. Special-casing `source="steer"` to render differently breaks the type's single rendering contract.
- **Field mismatch**: `SystemNotificationEvent` has `level`, `title`, `text`, `ref_session_id`, `ref_label` — but no `delivery` field, no `content` field (uses `text`), and no `message_id` field (needed for ACP `messageId`).
- **Rendering complexity**: Protocol handlers need to check `source` to decide rendering target — violates single-responsibility principle.
- **ACP mapping unclear**: `SystemNotificationEvent` maps to `ToolPart` in OpenCode. Mapping it to `UserMessageChunk`/`UserMessage` in ACP requires completely different converter logic for the same event type, depending on `source`.
- **Dependency on unmerged RFC**: RFC-0056 / PR #219 is not yet merged. This RFC should not depend on it.

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Type safety | ⚠️ Medium | Same type, different rendering by `source` — runtime branching |
| Spec compliance | ✅ Pass | |
| Protocol coverage | ⚠️ Medium | Requires special-casing in each protocol handler |
| Implementation effort | ✅ Low | No new type |
| Backward compatibility | ✅ Pass | |
| ACP v1 compatibility | ⚠️ Medium | `SystemNotificationEvent` ACP mapping is deferred in RFC-0056; adding user message mapping expands scope |

**Effort Estimate**

- Complexity: Medium (special-casing logic in converters)
- Files: 6-8

**Risk Assessment**

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Rendering inconsistency | High | Medium | Two rendering paths for one event type |
| ACP mapping scope creep | High | High | RFC-0056 explicitly defers ACP mapping |
| Dependency on unmerged PR | High | High | RFC-0056 is not merged; this RFC must be independent |

---

#### Option 1C: Reuse `CustomEvent[UserMessagePayload]`

**Description**

Define a `UserMessagePayload` dataclass and use `CustomEvent[UserMessagePayload]` as the event type. No new union member.

**Advantages**

- No union change — no exhaustive match impact.

**Disadvantages**

- **Loses type safety**: `match` dispatch cannot distinguish `CustomEvent[UserMessagePayload]` from `CustomEvent[OtherPayload]` without runtime `isinstance` check.
- **Semantic opacity**: `CustomEvent` is a generic escape hatch. Consumers don't know it's a user message without inspecting the payload type.
- **Inconsistent with codebase**: All other events in the union are dedicated dataclasses.

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Type safety | ❌ Low | Generic payload — runtime `isinstance` needed |
| Spec compliance | ✅ Pass | |
| Protocol coverage | ✅ High | Can be mapped |
| Implementation effort | ✅ Low | No union change |
| Backward compatibility | ✅ Pass | |

**Effort Estimate**

- Complexity: Low
- Files: 5-6

---

### Decision 2: Publication Point

#### Option 2A: Publish from `_route_message()`, `steer_from_background_task()`, and `_consume_run()` (Recommended)

**Description**

`SessionController._route_message()` publishes `UserMessageInsertedEvent` before routing. `steer_from_background_task()` also publishes (synchronously, using `asyncio.create_task()` for the EventBus publish). `_consume_run()` publishes for followup-from-queue messages. `RunHandle.steer()`/`followup()` emit via `asyncio.create_task()` as a secondary mechanism.

**Advantages**

- `_route_message()` is the single choke point — publishing here covers all protocol-initiated paths.
- `steer_from_background_task()` covers internal steer paths.
- `_consume_run()` covers followup-from-queue (the gap identified by Oracle review).
- `steer()`/`followup()` `asyncio.create_task()` provides a fallback for direct `steer()` calls that bypass `_route_message()`.

**Disadvantages**

- Potential for double emission (from `_route_message()` AND `steer()`) — mitigated by shared dedup set.
- `steer_from_background_task()` is synchronous — requires `asyncio.create_task()` for EventBus publish, with `RuntimeError` handling for no-running-loop scenarios.

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Internal path coverage | ✅ High | Covers `steer_from_background_task()` and `_consume_run()` followup-from-queue |
| Dedup reliability | Medium | Requires shared `message_id` dedup set |
| Spec compliance | ✅ Pass | |

---

#### Option 2B: Publish from protocol handlers only

**Description**

Only ACP `handle_prompt()` and OpenCode `message_routes.py` emit user messages (current behavior). No EventBus event.

**Advantages**

- Simplest — no new event type.
- No dedup needed.

**Disadvantages**

- Does not cover internal steer paths — the primary problem.
- No unified mechanism for future protocols.

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Internal path coverage | ❌ Low | Does not cover `steer_from_background_task()` |
| Protocol coverage | ⚠️ Medium | Only ACP and OpenCode, no extensibility |

---

#### Option 2C: Publish from `steer()`/`followup()` only

**Description**

Only `RunHandle.steer()` and `followup()` publish the event via `asyncio.create_task()`.

**Advantages**

- Covers direct `steer()`/`followup()` calls.
- Simple publication point.

**Disadvantages**

- Does not cover followup from `prompt_queue` (picked up by `_consume_run()` without calling `followup()`).
- Does not cover initial prompt (which doesn't go through `steer()`/`followup()`).

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Internal path coverage | ⚠️ Medium | Misses `prompt_queue` followup path |
| Protocol coverage | ⚠️ Medium | Misses initial prompt |

---

### Decision 3: ACP Version Handling

#### Option 3A: v1 uses `UserMessageChunk`; v2 uses `UserMessage` (Recommended)

**Description**

`ACPEventConverter` checks `protocol_version` (passed to its constructor from the ACP agent, which stores it at `acp_agent.py:380`). For v1 (`protocol_version < 2`), emit `UserMessageChunk` with `TextContentBlock`. For v2 (`protocol_version >= 2`), emit `UserMessage` (whole-message upsert). Add `UserMessage` Pydantic model to Python schema.

**Advantages**

- v1 compatibility with current Zed.
- v2 uses the proper whole-message upsert mechanism.
- Future-proof when v2 becomes stable.
- `protocol_version` is passed explicitly to the converter — no global state needed.

**Disadvantages**

- Two code paths in `ACPEventConverter`.
- `UserMessage` model needs to be added to Python schema (currently missing).
- `ACPEventConverter.__init__()` must be modified to accept `protocol_version: int`.

---

#### Option 3B: v1-only via `UserMessageChunk`

**Description**

Always emit `UserMessageChunk` regardless of protocol version.

**Advantages**

- Single code path.
- No schema changes needed.

**Disadvantages**

- Does not use v2 `UserMessage` upsert — the proper mechanism for whole-message insertion.
- No forward path to v2 features.

---

#### Option 3C: v2-only via `UserMessage`

**Description**

Always emit `UserMessage`. Requires v2 protocol negotiation.

**Advantages**

- Uses the proper v2 mechanism.
- Clean upsert semantics.

**Disadvantages**

- Breaks v1 clients (Zed).
- Requires protocol version bump.

---

### Options Comparison Summary

| Criterion | 1A: New Type | 1B: Reuse SysNotif | 1C: CustomEvent | 2A: _route_message+consume | 2B: Handlers only | 2C: steer() only | 3A: v1+v2 | 3B: v1-only | 3C: v2-only |
|-----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Type safety | ✅ | ⚠️ | ❌ | — | — | — | — | — | — |
| Spec compliance | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | — | — |
| Protocol coverage | ✅ | ⚠️ | ✅ | — | ⚠️ | ⚠️ | ✅ | ⚠️ | ❌ |
| Internal path | — | — | — | ✅ | ❌ | ⚠️ | — | — | — |
| Effort | Medium | Low | Low | Medium | Low | Low | Medium | Low | Medium |
| Backward compat | ✅ | ✅ | ✅ | — | — | — | ✅ | ✅ | ❌ |
| ACP v1 compat | — | ⚠️ | — | — | — | — | ✅ | ✅ | ❌ |
| Crash recovery | ✅ | ✅ | ✅ | — | — | — | — | — | — |

---

## Recommendation

### Recommended Options

**Decision 1: Option 1A — New `UserMessageInsertedEvent` dataclass**

**Decision 2: Option 2A — Publish from `_route_message()`, `steer_from_background_task()`, and `_consume_run()`**

**Decision 3: Option 3A — v1 uses `UserMessageChunk`; v2 uses `UserMessage` (with `protocol_version` in converter)**

### Justification

**1A over 1B**: Type safety is weighted High. `SystemNotificationEvent` (RFC-0056) has a single rendering contract (`ToolPart(tool="system")`). Special-casing `source="steer"` to render as `role="user"` breaks this contract and creates two rendering paths for one event type. Additionally, `SystemNotificationEvent` lacks `message_id` and `delivery` fields needed for ACP correlation and dedup. RFC-0056 explicitly defers ACP mapping — reusing it for user message display would expand scope into that deferred work. Furthermore, RFC-0056 / PR #219 is not yet merged — this RFC must not depend on it.

**1A over 1C**: `CustomEvent[T]` loses static dispatch — consumers need runtime `isinstance` checks, which defeats the purpose of PEP 695 typed unions. The codebase convention is dedicated dataclasses.

**2A over 2B**: Option 2B (protocol handlers only) does not cover internal steer paths — the primary problem this RFC addresses. Without `_route_message()` publication, `steer_from_background_task()` remains invisible.

**2A over 2C**: Option 2C (`steer()`/`followup()` only) misses the `prompt_queue` followup path (picked up by `_consume_run()` without calling `followup()`) and the initial prompt path. Option 2A adds `_consume_run()` publication to close the followup-from-queue gap identified by Oracle review.

**3A over 3B**: v1-only does not use the proper v2 `UserMessage` upsert mechanism. Adding the `UserMessage` model to the Python schema is low effort and future-proofs the implementation.

**3A over 3C**: v2-only breaks current Zed (ACP v1) clients.

### Accepted Trade-offs

1. **Double emission potential**: `_route_message()` and `steer()` may both emit for the same message. Mitigated by shared `message_id` dedup set. The protocol handler generates the ID first, registers it in the dedup set, emits to client, then passes the ID to `send_message()` → `_route_message()`; the EventBus event carries the same ID and the converter checks the dedup set and skips.

2. **`asyncio.create_task()` emission from `steer()`/`followup()` and `steer_from_background_task()`**: Fire-and-forget means the emission may be lost on shutdown. If no event loop is running (non-async context), emission is silently skipped (`RuntimeError` caught). Acceptable for display notifications — the message is still processed by the agent.

3. **No `reply_to` correlation**: ACP has no such field. Clients correlate by timing — the steer `UserMessageChunk` is followed by a new `AgentMessageChunk` (new `message_id`). Documented as the correlation mechanism.

4. **ACP v2 `UserMessage` is `unstable-v2`**: The v2 schema is behind a feature flag. The Python schema addition is forward-looking — it does not affect v1 clients.

5. **No journal persistence**: `UserMessageInsertedEvent` is lost on crash. Acceptable for display signals — the information is visible in logs and the agent's conversation history.

6. **`EventBus is None` for standalone execution**: When no EventBus is available (standalone `agent.run()`), publication is silently skipped. All spec language uses "SHALL publish ... if EventBus is available" to reflect this.

---

## Technical Design

### Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Entry Points                                                     │
│  ┌──────────────┐  ┌──────────────────┐  ┌────────────────────┐ │
│  │ ACP          │  │ OpenCode         │  │ Internal           │ │
│  │ handle_prompt│  │ message_routes   │  │ steer_from_bg_task │ │
│  │ (delivery    │  │ (delivery field) │  │ (no protocol,      │ │
│  │  from _meta) │  │                  │  │  SYNC method)      │ │
│  └──────┬───────┘  └────────┬─────────┘  └─────────┬──────────┘ │
│         │                   │                      │            │
│         ▼                   ▼                      │            │
│  ┌──────────────────────────────────────┐          │            │
│  │ session_pool.send_message()          │          │            │
│  │ → session_pool_messaging.py          │          │            │
│  │ → SessionController._route_message() │          │            │
│  │ ┌─ publish UserMessageInsertedEvent ─┼──────────┘            │
│  │ │  (if EventBus available)           │                       │
│  │ └────────────────────────────────────┘                       │
│  └──────────────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ _consume_run() / _create_per_prompt  │                       │
│  │  _handle() — followup-from-queue     │                       │
│  │  publish UserMessageInsertedEvent    │                       │
│  │  (source="internal", delivery=       │                       │
│  │   "followup", if EventBus available) │                       │
│  └──────────────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ EventBus                             │                       │
│  │ event_bus.publish(session_id, event) │                       │
│  └──────────┬───────────────────────────┘                       │
│             │                                                   │
│             ▼                                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Protocol Server Consumers (ProtocolEventConsumerMixin)     │ │
│  │                                                            │ │
│  │  ┌──────────────────────┐  ┌─────────────────────────────┐│ │
│  │  │ ACP ACPEventConverter│  │ OpenCode EventProcessor     ││ │
│  │  │ (protocol_version)   │  │                             ││ │
│  │  │ case UserMessageIns: │  │ case UserMessageInserted:   ││ │
│  │  │  check dedup set     │  │  check dedup set            ││ │
│  │  │  v1→UserMessageChunk │  │  → create UserMessage       ││ │
│  │  │  v2→UserMessage      │  │  → broadcast                ││ │
│  │  │  (dedup by msg_id)   │  │    MessageUpdatedEvent      ││ │
│  │  └──────────────────────┘  └─────────────────────────────┘│ │
│  └────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### Key Components

#### `UserMessageInsertedEvent` (new)

```python
@dataclass(frozen=True)
class UserMessageInsertedEvent:
    session_id: str = ""
    message_id: str = ""                      # unique per insertion, for dedup
    content: str | list[Any] = ""             # str or list[Any] for multi-modal
    delivery: Literal["initial", "steer", "followup"] = "initial"
    source: Literal["protocol", "background_task", "internal"] = "protocol"
    timestamp: float = field(default_factory=time.time)
```

Added to `RichAgentStreamEvent` PEP 695 `type` statement.

The `content` field is `str | list[Any]` to support multi-modal prompts (text + images, structured content blocks, etc.). `_route_message()` accepts `content: str | list[Any]`, and `steer()` accepts `message: str | list[Any]`.

#### `source` Field Mapping

| Call site | `source` value |
|---|---|
| `_route_message()` from protocol handler | `"protocol"` |
| `steer_from_background_task()` | `"background_task"` |
| `steer()` / `followup()` direct call | `"internal"` |
| `_consume_run()` followup-from-queue | `"internal"` |

#### `SessionController._route_message()` (modified)

```python
async def _route_message(
    self, session_id, content: str | list[Any],
    priority="when_idle", message_id=None, delivery=None,
):
    message_id = message_id or str(uuid.uuid4())
    if delivery is None:
        delivery = "initial" if session_idle else ("steer" if priority == "asap" else "followup")

    # Publish event BEFORE routing, if EventBus is available
    if self._event_bus:
        event = UserMessageInsertedEvent(
            session_id=session_id,
            message_id=message_id,
            content=content,
            delivery=delivery,
            source="protocol",
        )
        await self._event_bus.publish(session_id, event)

    # ... existing routing logic ...
```

#### ACP `_meta` extraction (modified — `acp_agent.py:prompt()` ~line 698)

`handle_prompt()` does NOT receive `_meta` directly. `_meta` is extracted at `acp_agent.py:698` for trace context but NOT forwarded to `handle_prompt()`. The fix: extract `delivery` from `_meta` in `acp_agent.py:prompt()` and pass it through the call chain.

```python
# In acp_agent.py:prompt() around line 698
# _meta is already extracted for trace context at this point
delivery = "steer" if (meta and meta.get("delivery") == "steer") else "when_idle"
# Pass delivery to handle_prompt → send_message → _route_message
await self.handle_prompt(session_id, prompt, delivery=delivery)
```

#### `steer_from_background_task()` (modified — SYNC, not async)

`steer_from_background_task()` is on `SessionController` at `session_controller.py:305`, uses `_active_steer_callback`, and is **synchronous**. It must NOT be made `async def`. Instead, use `asyncio.create_task()` for the EventBus publish:

```python
def steer_from_background_task(self, session_id, content: str | list[Any]):
    # ... existing steer logic via _active_steer_callback ...
    if emit_user_message and self._event_bus:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._emit_user_message_inserted(
                session_id=session_id,
                content=content,
                delivery="steer",
                source="background_task",
            ))
        except RuntimeError:
            pass  # no running loop — emission silently skipped
```

> **Note**: If no event loop is running (non-async context), emission is silently skipped.

#### `_consume_run()` followup-from-queue (modified)

`_consume_run()` → `_create_per_prompt_handle()` (line 150) creates `RunHandle` directly WITHOUT calling `_route_message()`. This means followup messages picked up from `prompt_queue` bypass the event publication entirely. Fix: add event publication in `_consume_run()`:

```python
# In _consume_run() after picking up from prompt_queue
if self._event_bus:
    event = UserMessageInsertedEvent(
        session_id=session_id,
        message_id=str(uuid.uuid4()),
        content=content,
        delivery="followup",
        source="internal",
    )
    await self._event_bus.publish(session_id, event)
```

#### Emission helper (new — with `logfire.span` and exception handling)

All `asyncio.create_task()` emission calls use this shared helper. It wraps the emission in a `logfire.span` to prevent orphan traces, and catches all exceptions to prevent task failures from propagating:

```python
async def _emit_user_message_inserted(self, session_id, content, delivery, source):
    with logfire.span("event.user_message_inserted.emit", session_id=session_id):
        try:
            event = UserMessageInsertedEvent(
                session_id=session_id,
                message_id=str(uuid.uuid4()),
                content=content,
                delivery=delivery,
                source=source,
            )
            if self._event_bus:
                await self._event_bus.publish(session_id, event)
        except Exception:
            logger.warning("Failed to emit UserMessageInsertedEvent", exc_info=True)
```

All `asyncio.create_task()` call sites must have try/except for `RuntimeError` (no running loop):

```python
try:
    loop = asyncio.get_running_loop()
    loop.create_task(self._emit_user_message_inserted(...))
except RuntimeError:
    pass  # no running loop — emission silently skipped
```

#### `RunHandle.steer()` / `followup()` (modified)

```python
def steer(self, content: str | list[Any], emit_user_message: bool = True) -> None:
    # ... existing steer logic ...
    if emit_user_message:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._emit_user_message_inserted(content, "steer", "internal")
            )
        except RuntimeError:
            pass  # no running loop — emission silently skipped

def followup(self, content: str | list[Any], emit_user_message: bool = False) -> None:
    # ... existing followup logic ...
    if emit_user_message:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._emit_user_message_inserted(content, "followup", "internal")
            )
        except RuntimeError:
            pass  # no running loop — emission silently skipped
```

#### `ACPEventConverter` (modified — with `protocol_version`)

`ACPEventConverter` currently has NO `protocol_version` field. Protocol version is stored on the ACP agent (`acp_agent.py:380`). Fix: add `protocol_version: int` to the constructor, passed from the ACP agent:

```python
class ACPEventConverter:
    def __init__(self, ..., protocol_version: int = 1):
        self._protocol_version = protocol_version
        self._displayed_message_ids: set[str] = set()
        # ... other init ...

    # In the event handler:
    case UserMessageInsertedEvent(message_id=mid, content=content):
        if mid in self._displayed_message_ids:
            return  # dedup — already emitted by protocol handler
        # Convert content (str | list[Any]) to ContentBlocks
        blocks = _content_to_blocks(content)
        if self._protocol_version >= 2:
            yield SessionUpdate(
                session_update=SessionUpdateKind.UserMessage,
                message_id=mid,
                content=blocks,
            )
        else:
            for block in blocks:
                yield SessionUpdate(
                    session_update=SessionUpdateKind.UserMessageChunk,
                    message_id=mid,
                    content=block,
                )
        self._displayed_message_ids.add(mid)
```

When `content` is `list[Any]`, convert each item to the appropriate `ContentBlock` (e.g., `str` → `TextContentBlock`, dict with image → `ImageContentBlock`).

#### OpenCode `EventProcessor` (modified)

```python
case UserMessageInsertedEvent(message_id=mid, content=content, timestamp=ts):
    if mid in self._displayed_message_ids:
        return  # dedup
    # Convert content (str | list[Any]) to Parts
    parts = _content_to_parts(content)
    user_msg = UserMessage(
        id=mid,
        session_id=ctx.session_id,
        role="user",
        content=parts,
        created_at=ts,
    )
    yield MessageUpdatedEvent(message=user_msg)
    yield PartUpdatedEvent(...)
    self._displayed_message_ids.add(mid)
```

When `content` is `list[Any]`, convert each item to the appropriate `Part` (e.g., `str` → `TextPart`, dict with image → `ImagePart`).

#### ACP `UserMessage` schema (new)

```python
class UserMessage(BaseModel):
    session_update: Literal["user_message"] = "user_message"
    message_id: str
    content: list[ContentBlock] | None = None
    meta: dict[str, Any] | None = None
```

Added to `SessionUpdate` union in `session_updates.py`.

### Data Model

```python
@dataclass(frozen=True)
class UserMessageInsertedEvent:
    session_id: str = ""
    message_id: str = ""
    content: str | list[Any] = ""
    delivery: Literal["initial", "steer", "followup"] = "initial"
    source: Literal["protocol", "background_task", "internal"] = "protocol"
    timestamp: float = field(default_factory=time.time)
```

### Deduplication Strategy

The dedup mechanism uses a **shared `message_id` dedup set** accessible by BOTH the protocol handler emission path AND the `ACPEventConverter`/`EventProcessor` (EventBus path).

**Current problem**: `_emit_user_message_chunks()` generates `message_id` internally via `build_user_message_chunks()` at `event_converter.py:294` and sends directly to client via `self.client.session_update(notification)`, bypassing `ACPEventConverter` entirely. This means the converter has no way to know which messages were already emitted.

**Fix**: Modify `_emit_user_message_chunks()` to:
1. Generate `message_id` FIRST (before emitting chunks).
2. Register `message_id` in the shared dedup set.
3. Pass `message_id` through `send_message(message_id=mid)` → `_route_message(message_id=mid)`.

The dedup set must be accessible by BOTH `_emit_user_message_chunks()` (protocol handler path) AND `ACPEventConverter` (EventBus path).

```
Protocol handler (ACP/OpenCode)
  │
  ├─ 1. Generate message_id = str(uuid.uuid4())
  ├─ 2. Register message_id in shared dedup set
  ├─ 3. Emit user message to frontend (UserMessageChunk / UserMessage + SSE)
  ├─ 4. Pass message_id to send_message()
  │
  ▼
send_message() → session_pool_messaging.py → _route_message()
  │
  ├─ 5. Publish UserMessageInsertedEvent(message_id=same_id)
  │     (if EventBus is available)
  │
  ▼
EventBus → Protocol Consumer (ACPEventConverter / EventProcessor)
  │
  ├─ 6. Check message_id in shared dedup set
  ├─ 7. Found → skip (dedup — already emitted by protocol handler)
  └─ 8. Not found → emit (internal path, no prior protocol emission)
```

**For ACP**: `_emit_user_message_chunks()` generates ID, registers in dedup set, emits to client, passes ID to `send_message()` → `_route_message()`. The `ACPEventConverter` checks the same dedup set.

**For OpenCode**: `message_routes.py` generates ID, registers in dedup set, creates `UserMessage`, passes ID to `route_message()`. The `EventProcessor` checks the same dedup set. **All 6+ `UserMessage` creation sites** must be wired with dedup:
- `message_routes.py:311,884`
- `session_routes.py:414,638`
- `opencode_event_bridge.py:368,638`

---

## Security Considerations

### Threat Analysis

| Threat | Impact | Likelihood | Mitigation |
|--------|--------|------------|------------|
| Steer content leaks to all EventBus subscribers | Medium | Low | `content` is user-authored or system-generated. No credential leakage expected. |
| Dedup set grows unbounded | Low | Low | Bounded by session lifetime; entries removed on session close or TTL (5 min default) |
| `message_id` collision | High | Very Low | UUID4 makes collision astronomically unlikely |
| ACP v1 client receives unexpected `UserMessageChunk` mid-turn | Medium | Medium | Document behavior; `UserMessageChunk` is valid at any point per ACP spec |
| Orphan traces from `create_task()` emission | Medium | Medium | Emission helper wraps in `logfire.span("event.user_message_inserted.emit")` to prevent orphan traces |
| Exception in emission coroutine crashes task | Medium | Low | Emission helper catches all exceptions with `try/except Exception` and logs warning |

---

## Implementation Plan

### Phases

#### Phase 1: Core Event Type + Publication (Tasks 1-4)

- `UserMessageInsertedEvent` dataclass + union update
- `SessionController._route_message()` publication (if EventBus available)
- `steer_from_background_task()` publication (SYNC, `asyncio.create_task()`)
- `_consume_run()` followup-from-queue publication
- `RunHandle.steer()`/`followup()` `emit_user_message` parameter
- Emission helper with `logfire.span` and exception handling
- Unit tests

#### Phase 2: ACP Schema + Handler (Tasks 5-7)

- `UserMessage` Pydantic model in `session_updates.py`
- `acp_agent.py:prompt()` `_meta.delivery` extraction (line ~698) → pass to `handle_prompt()` → `send_message()` → `_route_message()`
- `ACPEventConverter` constructor: add `protocol_version: int` from ACP agent
- `ACPEventConverter` new case + dedup (shared dedup set)
- `_emit_user_message_chunks()` dedup wiring (generate ID first, register, pass to `send_message()`)
- Unit tests

#### Phase 3: OpenCode Handler (Tasks 8-10)

- `EventProcessor` new case + dedup (shared dedup set)
- `message_routes.py` dedup wiring (generate ID first, register, pass to `route_message()`)
- Audit and wire dedup at ALL 6+ `UserMessage` creation sites:
  - `message_routes.py:311,884`
  - `session_routes.py:414,638`
  - `opencode_event_bridge.py:368,638`
- Unit tests

#### Phase 4: Audit + Integration Tests + Docs (Tasks 11-14)

- Exhaustive `match event:` audit — enumerate ALL 27 match sites across 20 files
- Integration tests (ACP v1, ACP v2, OpenCode, internal steer, followup-from-queue, dedup)
- Documentation updates
- Quality gates (ruff, mypy, pytest)

### Milestones

| Milestone | Description | Dependencies |
|-----------|-------------|--------------|
| M1: Event type + publication | `UserMessageInsertedEvent` in union, published from `_route_message()`, `steer_from_background_task()`, `_consume_run()` | None |
| M2: ACP mapping | `UserMessage` schema, `ACPEventConverter` with `protocol_version`, `_meta.delivery` extraction, dedup wiring | M1 |
| M3: OpenCode mapping | `EventProcessor` handles event, all 6+ `UserMessage` creation sites wired with dedup | M1 |
| M4: Audit + tests + docs | All 27 match sites across 20 files audited, integration tests pass, docs updated | M1-M3 |

### Rollback Strategy

Remove `UserMessageInsertedEvent` from the union, remove `emit_user_message` parameters, remove `ACPEventConverter`/`EventProcessor` cases, remove `UserMessage` schema model, remove `protocol_version` from `ACPEventConverter`. No data migration needed — events are not journaled. Existing consumers with `case _:` catch-all are unaffected.

---

## Open Questions

1. **ACP v1 client UI ordering assumptions**
   - Context: Some ACP v1 clients may assume user messages only appear between turns. Receiving `UserMessageChunk` mid-turn (during steer) may cause rendering issues.
   - Owner: Implementer (integration test with Zed)
   - Status: Open — needs empirical verification. Document as known behavior if issues arise.

2. **Dedup set TTL**
   - Context: The dedup `set[str]` per session needs cleanup. Should entries expire after a fixed time, or only on session close?
   - Owner: Implementer
   - Status: Open — default to session close cleanup; add TTL (5 min) if memory is a concern.

3. **`_meta.delivery` standardization**
   - Context: Using `_meta.delivery` for ACP steer priority is a custom extension. Should this be proposed as an ACP protocol standard?
   - Owner: Follow-up RFC
   - Status: Open — deferred. Current implementation uses `_meta` as the standard extension mechanism.

4. **Team mode `source="team"` integration**
   - Context: RFC-0056 has `source="team"` for team mode notifications. Should `UserMessageInsertedEvent` also support `source="team"` with `ref_label`?
   - Owner: Follow-up
   - Status: Open — deferred. Current `source` enum has "protocol", "background_task", "internal". "team" can be added when team mode integration is needed.

### Decisions (Resolved)

**D5: Interaction with RFC-0056 (SystemNotificationEvent)**

**Decision**: When `emit_user_message=True`, `SystemNotificationEvent` (if implemented in the future via RFC-0056) should default to suppressed for the same message to avoid redundant display. This is a decision, not an open question.

**Rationale**: Both event types serving the same steer message would result in duplicate display (one as `role="user"`, one as `ToolPart(tool="system")`). When `UserMessageInsertedEvent` is emitted, the `SystemNotificationEvent` for the same content is redundant. Future implementations of RFC-0056 should check whether a `UserMessageInsertedEvent` was already emitted for the same `message_id` and skip the notification.

---

## Decision Record

> Complete this section after RFC review is concluded.

### Decision

**Status**: DRAFT — Pending review

**Date**: TBD

**Approvers**:
- [Reviewer 1]
- [Reviewer 2]

### Decision Summary

TBD

### Key Discussion Points

TBD

### Conditions of Approval

TBD

### Dissenting Opinions

TBD

---

## References

### Related Documents

- [OpenSpec Change: steer-followup-user-message-display](../../openspec/changes/steer-followup-user-message-display/)
- [RFC-0056: SystemNotificationEvent](./draft/RFC-0056-system-notification-event.md) — complementary future work (NOT a dependency)
- [RFC-0037: Unify Steer and Followup](./draft/RFC-0037-unify-steer-followup.md) — steer/followup sync constraints
- [RFC-0042: Unified Lifecycle Architecture](./draft/RFC-0042-unified-lifecycle-architecture.md) — CommChannel/EventBus foundation
- [Spec: unified-event-routing](../../openspec/specs/unified-event-routing/spec.md) — event routing rules
- [Spec: steer-followup-api](../../openspec/specs/steer-followup-api/spec.md) — steer/followup API
- [Spec: acp-server](../../openspec/specs/acp-server/spec.md) — ACP server behavior

### External Resources

- [ACP v2 Schema — SessionUpdate](https://github.com/agentclientprotocol/agent-client-protocol) — `user_message` variant (v2-only, `unstable-v2`)
- [ACP v2 Prompt Lifecycle RFD](https://agentclientprotocol.com/rfds/v2/prompt-lifecycle) — "they would receive a user message that they didn't prompt"
- [OpenCode TUI](https://github.com/sst/opencode) — `SessionMessage.User` projection

### Review History

| Reviewer | Date | Verdict | Key Findings |
|----------|------|---------|--------------|
| Metis (Pre-Planning) | 2026-07-21 | 7 blockers, 12 concerns | B1-B7 codebase accuracy; C1-C12 implementation concerns |
| Oracle (Architecture) | 2026-07-21 | 2 blockers, 6 concerns, 2 acceptable | Dedup mechanism broken; followup-from-queue gap |
| Momus (Plan Quality) | 2026-07-21 | Rejected (format) | Only accepts .omo/plans/*.md paths |

### Revision History

| Revision | Date | Changes |
|----------|------|---------|
| 1 | 2026-07-21 | Initial draft |
| 2 | 2026-07-21 | Post-review: removed PR #219 dependency; fixed receive_request→send_message; fixed steer_from_background_task sync; fixed _meta extraction; content: str→str\|list[Any]; fixed dedup mechanism; added protocol_version to converter; added _consume_run publication; added exception handling; added EventBus=None guard; defined source mapping; added logfire.span; updated match site count to 27; resolved OQ5 as decision |
