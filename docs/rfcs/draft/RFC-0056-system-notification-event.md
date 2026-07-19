---
rfc_id: RFC-0056
title: "SystemNotificationEvent: First-Class System Notifications in the Event Stream"
status: DRAFT
author: yuchen.liu
reviewers:
  - name: Oracle (Architecture Review)
    status: completed — 3 blockers identified and resolved
  - name: Metis (Pre-Planning Analysis)
    status: completed — 3 blockers identified and resolved
  - name: Momus (Plan Quality)
    status: completed — OKAY verdict
created: 2026-07-19
last_updated: 2026-07-19 (revision 3: restored steer/followup emission via asyncio.create_task; added team source + ref_label field for PR #168 integration)
decision_date:
related_rfcs:
  - RFC-0042 (Unified Lifecycle Architecture — CommChannel/EventBus foundation)
  - RFC-0037 (Unify Steer and Followup — steer/followup are sync, constrains emission)
  - RFC-0013 (Subagent Event Unification — SpawnSessionStart precedent for synthetic events)
related_specs:
  - openspec/changes/add-system-notification-event/ (implementation spec — proposal, design, specs, tasks)
  - openspec/specs/unified-event-routing/ (event routing rules — bans event_queue as channel)
  - openspec/specs/steer-followup-api/ (steer/followup API — sync methods, not modifiable here)
---

# RFC-0056: SystemNotificationEvent — First-Class System Notifications in the Event Stream

## Overview

AgentPool's `RichAgentStreamEvent` union (20 event types) flows through `EventBus` → protocol servers (OpenCode, ACP, AG-UI, OpenAI API). The OpenCode `EventProcessor.process()` is a `match event:` dispatch that handles 12 event types; the remaining 8 — including `CompactionEvent`, `PlanUpdateEvent`, `SessionResumeEvent` — fall through silently with no TUI rendering.

Three categories of user-visible signal are invisible to the TUI today:

1. **Background task completion** — `AgentRunContext.complete_background_task()` injects result text into the model's context via `steer_callback`, but emits no event. The TUI sees the result only if the model echoes it.
2. **Lifecycle events** — compaction, plan updates, and session resume are silently dropped by `EventProcessor`.
3. **Custom tool notifications** — capabilities/tools have no public API to emit a system-visible message without importing `EventBus` or `RunHandle`.

This RFC proposes adding a first-class `SystemNotificationEvent` to `RichAgentStreamEvent`, an `AgentRunContext.emit_system_notification()` emission API (following the existing `report_progress()` pattern), and an OpenCode `EventProcessor` mapping that renders system notifications as `ToolPart(tool="system")` entries with a `metadata.system_notification` flag.

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

**Event taxonomy**: `RichAgentStreamEvent` (PEP 695 `type` statement at `events.py:904`) is a union of 20 event types: `PartStartEvent`, `PartDeltaEvent`, `RunStartedEvent`, `RunErrorEvent`, `RunFailedEvent`, `StreamCompleteEvent`, `ToolCallStartEvent`, `ToolCallProgressEvent`, `ToolCallCompleteEvent`, `ToolCallDeferredEvent`, `ElicitationDeferredEvent`, `SubAgentEvent`, `SpawnSessionStart`, `CompactionEvent`, `PlanUpdateEvent`, `ToolResultMetadataEvent`, `CustomEvent[T]`, `StateUpdate`, `ToolCallUpdateEvent`, `MessageReplacementEvent`, `SessionResumeEvent`.

**Event flow**: Events flow through two paths (per RFC-0042):
1. `event_bus.publish(session_id, event)` — backward-compatible path, consumed by protocol servers via `ProtocolEventConsumerMixin`.
2. `comm_channel.publish(event)` — M2 CommChannel path, journals first (append or upsert via `_derive_upsert_key()`), then publishes to EventBus.

**OpenCode EventProcessor**: `event_processor.py:129` is a `match event:` dispatch. 12 cases are handled (text, thinking, tool calls, stream complete, run error). The remaining 8 event types match nothing and silently produce zero SSE events.

**Existing emission pattern**: Tools and capabilities emit events via `AgentContext.events` → `StreamEventEmitter._emit()` → `event_bus.publish()`. `AgentContext.report_progress()` (`context.py:566`) does the same directly. This is the canonical pattern — no `event_queue` involvement.

**ToastInfo**: A separate dataclass (`events.py:146`) with `message`, `level`, `duration`, `action` fields. It is NOT in `RichAgentStreamEvent`. It is emitted only by ACP clients via `_agentpool/toast` extension, and is literally ignored by ACP `session_events.py:71` (`"Received ToastInfo, ignoring"`). It represents chrome-level OS toast/sound, not conversation-inline system messages.

### Historical Context

- **OpenCode's TUI** solves the equivalent problem by reducing every event to a typed `StreamCommit` with `kind`/`source`/`phase` fields. `source: "system"` marks system messages. This is a client-side reduction, not a protocol-level type.
- **ACP v2** has no native system notification event type. `SessionUpdate` has 16 variants, none for system notifications. `Role` enum has only `Assistant` and `User` (no `System`). Extension is via `_meta` fields or `_`-prefixed custom methods.
- The `unified-event-routing` spec (`openspec/specs/unified-event-routing/`) explicitly states: "`run_ctx.event_queue` SHALL NOT be used as an event channel between tools and the stream consumer." and "Business layer code SHALL NOT perform manual event routing."

### Glossary

| Term | Definition |
|------|------------|
| `RichAgentStreamEvent` | PEP 695 union type of 20 event variants flowing through the agent stream |
| `EventBus` | In-process pub/sub for agent events, scoped by session (`session`, `descendants`, `subtree`, `all`) |
| `EventProcessor` | OpenCode server component that converts `RichAgentStreamEvent` → OpenCode SSE `Event` objects |
| `CommChannel` | M2 lifecycle dimension that journals events before delivery (owns Journal) |
| `ToastInfo` | Chrome-level OS toast/sound dataclass — NOT in `RichAgentStreamEvent` |
| `ToolPart` | OpenCode SSE part type representing a tool call with lifecycle (running → completed/error) |
| `StreamEventEmitter` | Existing emitter on `AgentContext` that publishes events to `EventBus` |

---

## Problem Statement

### The Problem

Several user-visible signals are invisible to the OpenCode TUI:

1. **Background task completion**: `AgentRunContext.complete_background_task()` (`context.py:188`) calls `steer_callback` to inject result text into the model's context, but emits no event. The TUI only sees the result if the model echoes it — and models frequently don't.

2. **Steer/followup notification emission via `asyncio.create_task()`** — `RunHandle.steer()` and `followup()` are synchronous but always called from async contexts. Emission is scheduled as a fire-and-forget task via `asyncio.get_running_loop().create_task(...)`. This works because: (a) a running event loop always exists when `steer()`/`followup()` are called, (b) an active span is inherited via contextvars (no orphan trace), (c) the emission is best-effort and slightly delayed (one event loop tick — fine for notifications). `steer()` defaults `emit_notification=True`; `followup()` defaults `emit_notification=False`. Team mode `send_message` can pass `emit_notification=False` to suppress the generic notification and emit a specific `source="team"` notification with `ref_session_id` and `ref_label` instead.
3. **Lifecycle events silently dropped**: `CompactionEvent`, `PlanUpdateEvent`, `SessionResumeEvent` reach `EventProcessor.process()` but match no case. The user has no visibility into compaction events, plan updates, or session resume operations.

3. **No public emission API for tools/capabilities**: A tool that wants to display "Analysis complete" or "Checkpoint saved" to the user has no way to do so without importing `EventBus` directly (which violates `unified-event-routing`'s "business layer SHALL NOT perform manual event routing" — although the existing `StreamEventEmitter` pattern already does this via a thin wrapper).

### Evidence

- **Code inspection**: `EventProcessor.process()` (`event_processor.py:129-227`) has 12 `case` branches and no `case _:` catch-all. Verified: `CompactionEvent`, `PlanUpdateEvent`, `SessionResumeEvent`, `SubAgentEvent`, `CustomEvent`, `MessageReplacementEvent`, `StateUpdate`, `ToolCallUpdateEvent` all fall through silently.
- **`complete_background_task()`** (`context.py:188-210`): calls `steer_callback` then sets `child_done_events[child_session_id]`. No event emission.
- **`ToastInfo`**: emitted only by `acp_agent/client_handler.py:411` via `_agentpool/toast` extension. Consumed by `acp_server/session_events.py:71` which logs `"Received ToastInfo, ignoring"`. Not in `RichAgentStreamEvent` union. Completely disconnected.
- **Oracle/Metis review findings** (3 independent reviewers): confirmed all three problems via codebase verification.

### Impact of Inaction

- **User experience**: Background task completion is the primary use case. Users fire off a long-running research subagent, and when it completes, there's no visible signal — the result just appears in the model's context, if at all. This is a significant UX gap for multi-agent workflows.
- **Observability**: Compaction and plan updates are important lifecycle events. Silent dropping means users can't see when context was compacted or plans changed — reducing trust in the system.
- **Extensibility**: Without a public emission API, every capability that wants to show a system message must invent its own mechanism, leading to inconsistency.

---

## Goals & Non-Goals

### Goals (In Scope)

1. Add `SystemNotificationEvent` to `RichAgentStreamEvent` union with typed fields (`level`, `source`, `title`, `text`, `ref_session_id`, `ref_label`, `timestamp`).
2. Add `AgentRunContext.emit_system_notification()` async method that publishes directly to `self.event_bus` — same pattern as `report_progress()`.
3. Wire `complete_background_task()` to emit `SystemNotificationEvent(source="background_task")`.
4. Wire `RunHandle.steer(emit_notification=True)` and `followup(emit_notification=False)` to emit `SystemNotificationEvent` via `asyncio.create_task()` (fire-and-forget).
5. Map `CompactionEvent`, `PlanUpdateEvent`, `SessionResumeEvent` to system notifications in the OpenCode `EventProcessor` (not at emission source).
6. Map `SystemNotificationEvent` to OpenCode SSE via `ToolPart(tool="system")` with `metadata.system_notification=True`.
7. Audit all ~20 `match event:` sites for exhaustive handling of the new type.
8. Support team mode (PR #168) integration via `source="team"` enum value and `ref_label` field for human-readable session references.

### Non-Goals (Out of Scope)

1. **ACP bridge mapping** — `_agentpool/notification` extension method. Deferred to a follow-up change. ACP has no native system notification event type; mapping requires extension method design.
2. **OS-level desktop notifications / sound** — `ToastInfo` remains the chrome-level channel. This RFC does not wire `SystemNotificationEvent` to OS toast.
3. **OpenCode protocol changes** — no new SSE event type. Initial mapping reuses existing `ToolPart` rendering path.
4. **Modifying `ToastInfo`** or its ACP emission path.
5. **Journal/CommChannel semantics** — notifications are point-in-time signals that bypass `CommChannel` entirely (published directly to `EventBus`, never journaled, never replayed). This matches the existing behavior of `StreamEventEmitter._emit()`.
6. **Team mode tool modifications** — this RFC provides the `source="team"` enum value and `ref_label` field to support PR #168 integration, but does not modify `TeamCommCapability` itself. That integration is a consumer of the API, done separately.

### Success Criteria

- [ ] A background subagent completing surfaces a visible entry in the OpenCode TUI (not dependent on model echoing the result).
- [ ] A `CompactionEvent` reaching `EventProcessor` produces a visible system notification (not silently dropped).
- [ ] A tool calling `run_ctx.deps.emit_system_notification(text="...")` produces a visible entry in the OpenCode TUI.
- [ ] `RunHandle.steer()` with default `emit_notification=True` produces a `SystemNotificationEvent(source="steer")` in the OpenCode SSE output.
- [ ] `RunHandle.steer(emit_notification=False)` suppresses the generic notification (for team mode to emit its own specific notification).
- [ ] A `SystemNotificationEvent(source="team", ref_label="member: researcher")` renders as `(member: researcher)` in the TUI, not as a raw session UUID.
- [ ] All ~20 `match event:` sites either handle `SystemNotificationEvent` or have an explicit catch-all.
- [ ] No breaking changes to existing `steer()`/`followup()` callers (new `emit_notification` parameter has defaults).

---

## Evaluation Criteria

The following criteria are used to objectively evaluate each option:

| Criterion | Weight | Description | Minimum Threshold |
|-----------|--------|-------------|-------------------|
| Type safety | High | Static type checker (mypy/pyright) can distinguish the event in `match` dispatch | Must be a distinct type, not a generic payload |
| Spec compliance | High | Does not violate `unified-event-routing` spec (no `event_queue` as channel, no business-layer routing) | Must pass spec audit |
| Implementation effort | Medium | Lines of code, files touched, complexity | ≤ 5 files modified for core type + emission |
| Extensibility | Medium | Can capabilities/tools emit custom notifications without framework changes | Must have a public API on per-run context |
| Rendering fidelity | Medium | How clearly the TUI distinguishes system notifications from tool calls | Must be visually distinct (verified empirically) |
| Backward compatibility | High | No breaking changes to existing consumers | All existing `match` with `case _:` unaffected |
| Crash recovery safety | Medium | No duplicate notifications on crash recovery replay | Must not journal or must filter on replay |

---

## Options Analysis

### Decision 1: Event Type Design

#### Option 1A: New `SystemNotificationEvent` dataclass (Recommended)

**Description**

Add a new `@dataclass(kw_only=True) SystemNotificationEvent` to `events.py` and add it to the `RichAgentStreamEvent` union. Fields: `session_id`, `level` (info/warning/error/success), `source` (background_task/system/lifecycle/custom), `title`, `text` (required), `ref_session_id`, `timestamp` (via `field(default_factory=time.time)`).

**Advantages**

- Type-safe `match` dispatch: `case SystemNotificationEvent(level=l, ...)` is self-documenting and exhaustive-checkable.
- Distinct from `ToastInfo` (chrome-level) — no semantic conflation.
- Fields are specific to system notifications (`level`, `source`, `ref_session_id`) — not a generic payload.
- Follows the existing pattern of typed event dataclasses in the union.

**Disadvantages**

- Adds a 21st type to the union — all exhaustive `match` statements need a new case (though only `EventProcessor` is exhaustive today).
- Slightly more code than reusing an existing type.

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Type safety | ✅ High | Distinct type, static dispatch |
| Spec compliance | ✅ Pass | No `event_queue`, no routing — publishes to `event_bus` |
| Implementation effort | Medium | 1 new dataclass + union update + `EventProcessor` case |
| Extensibility | ✅ High | `source` enum covers known producers; `custom` for tools |
| Rendering fidelity | Depends on mapping | See Decision 2 |
| Backward compatibility | ✅ Pass | Catch-all `case _:` unaffected |
| Crash recovery safety | ✅ Pass | Bypasses CommChannel — not journaled |

**Effort Estimate**

- Complexity: Low-Medium
- Files: 4-5 (`events.py`, `context.py`, `event_processor.py`, tests, docs)
- Dependencies: None

**Risk Assessment**

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Exhaustive match sites break | Low | Low | Audit task covers ~20 sites; only `EventProcessor` is exhaustive |
| ToolPart rendering broken | Medium | Medium | Fallback to `TextPart` spec'd as requirement |

---

#### Option 1B: Reuse `CustomEvent[SystemNotificationPayload]`

**Description**

`CustomEvent[T]` is already in the union. Define a `SystemNotificationPayload` dataclass and use `CustomEvent[SystemNotificationPayload]` as the event type. No new union member.

**Advantages**

- No union change — no exhaustive match impact.
- Less code (no new dataclass in the union).

**Disadvantages**

- **Loses type safety**: `match` dispatch can't distinguish `CustomEvent[SystemNotificationPayload]` from `CustomEvent[OtherPayload]` without inspecting the generic parameter at runtime. `case CustomEvent(payload=SystemNotificationPayload(...))` requires runtime `isinstance` check inside the case.
- **Semantic opacity**: `CustomEvent` is a generic escape hatch. Consumers don't know it's a notification without reading the payload type.
- **Inconsistent with the codebase**: All other events in the union are dedicated dataclasses, not `CustomEvent` wrappers.

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Type safety | ❌ Low | Generic payload — runtime `isinstance` needed |
| Spec compliance | ✅ Pass | |
| Implementation effort | ✅ Low | No union change |
| Extensibility | Medium | Tools must import `SystemNotificationPayload` |
| Rendering fidelity | Same as 1A | |
| Backward compatibility | ✅ Pass | |
| Crash recovery safety | ✅ Pass | |

**Effort Estimate**

- Complexity: Low
- Files: 3-4

**Risk Assessment**

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Runtime type checks in match | High | Medium | Defeats the purpose of typed dispatch |

---

#### Option 1C: Promote `ToastInfo` into the union

**Description**

Add `ToastInfo` to `RichAgentStreamEvent` and use it for system notifications.

**Advantages**

- Reuses an existing type — no new dataclass.
- `ToastInfo` already has `level`, `message`, `duration`, `action`.

**Disadvantages**

- **Semantic conflation**: `ToastInfo` represents chrome-level OS toast/sound (duration, action buttons). System notifications are conversation-inline messages. Conflating them forces consumers to disambiguate by field values.
- **Field mismatch**: `ToastInfo` has `duration` and `action` (chrome-specific) but no `source`, `ref_session_id`, or `timestamp`. Would need to add fields, making it a hybrid type.
- **Existing `ToastInfo` is dead code** (ignored by ACP, not in union). Promoting it would require fixing the ACP path too, expanding scope.
- **Breaks the separation** between chrome-level and conversation-level notifications.

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Type safety | ✅ High | Distinct type |
| Spec compliance | ✅ Pass | |
| Implementation effort | Medium | Need to add fields to `ToastInfo`, fix ACP path |
| Extensibility | ❌ Low | Chrome-specific fields are irrelevant for inline notifications |
| Rendering fidelity | ❌ Low | TUI would need to distinguish chrome vs inline by field values |
| Backward compatibility | Medium | ACP `ToastInfo` handling needs updating |
| Crash recovery safety | ✅ Pass | |

**Effort Estimate**

- Complexity: Medium (scope creep into ACP `ToastInfo` fix)
- Files: 5-6

---

### Decision 2: OpenCode SSE Mapping

#### Option 2A: `ToolPart` with synthetic tool name (Recommended)

**Description**

Map `SystemNotificationEvent` to `PartUpdatedEvent` containing `ToolPart(tool="system", state="completed", metadata={"system_notification": True})`. Follows the existing `tool="elicitation"` precedent (`event_processor.py:567`) which uses `metadata.elicitation=True` for TUI recognition.

**Advantages**

- Reuses existing `ToolPart` rendering path — no protocol changes.
- Follows the `tool="elicitation"` precedent — verified pattern.
- `metadata.system_notification=True` flag allows TUI to distinguish from real tool calls.

**Disadvantages**

- `ToolPart` lifecycle is `running → completed/error`. A notification is instantaneous (no `running` state). May render as a zero-duration tool call.
- TUI may show a tool-call affordance (expand/collapse, tool icon) — potentially misleading.
- `call_id` and `id` fields need synthetic values (collision risk if not handled).

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Implementation effort | ✅ Low | Reuses existing path |
| Rendering fidelity | ⚠️ Medium | Needs empirical verification; fallback spec'd |
| Backward compatibility | ✅ Pass | No protocol change |

**Risk Assessment**

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| TUI shows misleading tool icon | Medium | Medium | Fallback to `TextPart` spec'd as requirement |
| `call_id` collision | Low | High | Use `f"system-{uuid4().hex}"` |
| `id` collision for rapid notifications | Medium | Medium | Use `identifier.ascending("part")` |

---

#### Option 2B: `TextPart` with `[system]` prefix

**Description**

Map to `PartUpdatedEvent` containing `TextPart(content=f"[system] {text}")`. Uses the dormant `TextPart.synthetic` field (`parts.py:80`, never used) with `synthetic=True`.

**Advantages**

- `TextPart` renders as plain text — no tool-call affordance.
- `synthetic=True` flag exists and is unused — could activate it.

**Disadvantages**

- `TextPart.synthetic` is never used in the codebase — TUI rendering behavior is unknown.
- No `level` or `source` metadata visible — just text.
- No precedent for `synthetic=True` rendering.

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Implementation effort | Low | |
| Rendering fidelity | Unknown | `synthetic` field is dormant |
| Backward compatibility | ✅ Pass | |

---

#### Option 2C: New OpenCode SSE event type

**Description**

Define a new `SystemMessageEvent` SSE type in the OpenCode protocol schema.

**Advantages**

- Semantically correct — system messages are a distinct concept.
- TUI can render them with dedicated styling.

**Disadvantages**

- Requires changes to the OpenCode protocol/schema and the OpenCode TUI's rendering code.
- Out of scope for this RFC — significant cross-repo coordination.
- The OpenCode protocol is an external dependency; changes require upstream negotiation.

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Implementation effort | ❌ High | Cross-repo protocol change |
| Rendering fidelity | ✅ High | Dedicated rendering |
| Backward compatibility | ❌ Low | Protocol change |

---

### Decision 3: Emission API Location

#### Option 3A: `AgentRunContext.emit_system_notification()` (Recommended)

**Description**

Add `async def emit_system_notification(self, *, level, source, text, title="", ref_session_id=None)` to `AgentRunContext` (`context.py`). Publishes via `await self.event_bus.publish(self.session_id, event)`.

**Advantages**

- `AgentRunContext` already has `event_bus` (line 106) — no new field needed.
- Accessible from `RunContext[AgentRunContext].deps` in tools/capabilities.
- Follows the pattern of `complete_background_task()` (already on `AgentRunContext`).
- Named method is self-documenting.

**Disadvantages**

- Tools typically receive `AgentContext` (not `AgentRunContext`) via `RunContext[AgentContext]`. They access `run_ctx` via `ctx.deps.run_ctx`. Slightly indirect.
- `AgentRunContext` is the per-run state container; adding emission methods is a minor layering concern (but `complete_background_task` and `steer_callback` are already there).

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Spec compliance | ✅ Pass | Uses `event_bus` (existing), not `event_queue` (banned) |
| Implementation effort | ✅ Low | One method on existing class |
| Extensibility | ✅ High | Public API, named method |

---

#### Option 3B: Use existing `ctx.events.emit_event()` directly

**Description**

No new method. Tools call `ctx.events.emit_event(SystemNotificationEvent(...))` via the existing `StreamEventEmitter`.

**Advantages**

- Zero new API surface — uses existing emitter.
- `StreamEventEmitter` already publishes to `EventBus`.

**Disadvantages**

- Tools must construct the `SystemNotificationEvent` manually (including `session_id`, `timestamp`).
- No named API — less discoverable. Users must know to use `ctx.events.emit_event(SystemNotificationEvent(...))`.
- Doesn't serve `complete_background_task()` (which is on `AgentRunContext`, not `AgentContext`).

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Spec compliance | ✅ Pass | |
| Implementation effort | ✅ Lowest | No new code |
| Extensibility | ❌ Low | Not discoverable; manual construction |

---

#### Option 3C: `AgentContext.emit_system_notification()`

**Description**

Add the method to `AgentContext` (the pydantic-ai context) instead of `AgentRunContext`. `AgentContext` already has `report_progress()` and `events`.

**Advantages**

- Tools receive `AgentContext` directly via `RunContext[AgentContext]` — most natural access point.
- Follows `report_progress()` precedent exactly.

**Disadvantages**

- `AgentContext` accesses `event_bus` via `self.run_ctx.event_bus` (indirect). `AgentRunContext` has `event_bus` directly.
- `complete_background_task()` is on `AgentRunContext`, not `AgentContext`. The emission from `complete_background_task` would need to go through `self.run_ctx` or duplicate the method.

**Evaluation Against Criteria**

| Criterion | Rating | Notes |
|-----------|--------|-------|
| Spec compliance | ✅ Pass | |
| Implementation effort | Low | |
| Extensibility | ✅ High | |

---

### Options Comparison Summary

| Criterion | 1A: New Type | 1B: CustomEvent | 1C: ToastInfo | 2A: ToolPart | 2B: TextPart | 2C: New SSE | 3A: RunCtx | 3B: Existing | 3C: AgentCtx |
|-----------|-------------|-----------------|---------------|--------------|--------------|-------------|-----------|-------------|-------------|
| Type safety | ✅ | ❌ | ✅ | — | — | — | — | — | — |
| Spec compliance | ✅ | ✅ | ✅ | — | — | — | ✅ | ✅ | ✅ |
| Effort | Medium | Low | Medium | Low | Low | High | Low | Lowest | Low |
| Extensibility | ✅ | Medium | ❌ | — | — | — | ✅ | ❌ | ✅ |
| Backward compat | ✅ | ✅ | Medium | ✅ | ✅ | ❌ | — | — | — |
| Crash recovery | ✅ | ✅ | ✅ | — | — | — | — | — | — |

---

## Recommendation

### Recommended Options

**Decision 1: Option 1A — New `SystemNotificationEvent` dataclass**

**Decision 2: Option 2A — `ToolPart` with synthetic tool name** (with `TextPart` fallback as spec'd requirement)

**Decision 3: Option 3A — `AgentRunContext.emit_system_notification()`**

### Justification

**1A over 1B**: Type safety is weighted High. `CustomEvent[T]` loses static dispatch — consumers need runtime `isinstance` checks inside `match` cases, which defeats the purpose of PEP 695 typed unions. The codebase convention is dedicated dataclasses, not generic wrappers.

**1A over 1C**: `ToastInfo` carries chrome-level semantics (OS toast, sound, duration, action buttons). Promoting it into the union would conflate chrome-level and conversation-level notifications, requiring consumers to disambiguate by field values. Additionally, `ToastInfo` is currently dead code (ignored by ACP) — promoting it would expand scope into fixing the ACP path.

**2A over 2B**: `ToolPart` has a verified precedent (`tool="elicitation"` with `metadata.elicitation=True`). `TextPart.synthetic` is dormant and untested. The `metadata.system_notification=True` flag follows the established pattern. The risk (misleading tool icon) is mitigated by the spec'd `TextPart` fallback requirement.

**2A over 2C**: A new OpenCode SSE event type is semantically ideal but requires cross-repo protocol changes — out of scope for this RFC. Can be pursued as a follow-up if the `ToolPart` mapping proves unsatisfactory.

**3A over 3B**: A named method (`emit_system_notification`) is discoverable and self-documenting. `ctx.events.emit_event(SystemNotificationEvent(...))` requires manual event construction (including `session_id`, `timestamp`) and is not discoverable. The method delegates to `event_bus.publish()` — same mechanism, better API.

**3A over 3C**: `AgentRunContext` has `event_bus` directly (line 106). `AgentContext` would access it via `self.run_ctx.event_bus` (indirect). `complete_background_task()` is on `AgentRunContext` — the emission from there needs `self.emit_system_notification()`, which works naturally if the method is on the same class.

### Accepted Trade-offs

1. **ToolPart may render as a tool call**: Acceptable because `metadata.system_notification=True` flags it for TUI recognition, and the `TextPart` fallback is spec'd as a requirement (not optional). Empirical verification (task 6.7) confirms or triggers the fallback.

2. **Steer/followup notifications deferred**: Acceptable because `steer()`/`followup()` are synchronous and can't `await` an async emission. The primary use case (background task completion via `complete_background_task()`, which IS async) is covered. Steer/followup visibility is a smaller gap — a follow-up RFC can address it.

3. **ACP users don't get lifecycle fallback**: The fallback mapping is in the OpenCode `EventProcessor` only. ACP's `event_converter.py` still silently drops `CompactionEvent`/`PlanUpdateEvent`/`SessionResumeEvent`. Acceptable because ACP mapping requires extension method design (`_agentpool/notification`), which is a separate concern. A follow-up RFC can add ACP mapping.

4. **No journal persistence**: Notifications are lost on crash. Acceptable for point-in-time signals — the information is also visible in logs. Journaling would cause duplicate delivery on crash recovery replay (since `ProtocolChannel.publish()` with `_replaying=True` still publishes to `EventBus`).

### Conditions

- Empirical verification of `ToolPart(tool="system")` rendering MUST be performed before finalizing the mapping. If rendering is broken, the `TextPart` fallback MUST be implemented.
- All ~20 `match event:` sites MUST be audited for exhaustive handling.
- No changes to `steer()`/`followup()` signatures (deferred to follow-up).

---

## Technical Design

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│ Tool / Capability / Hook                                    │
│   run_ctx.deps.emit_system_notification(                    │
│     level="info", source="custom", text="Analysis done"     │
│   )                                                         │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ AgentRunContext.emit_system_notification()                  │
│   event = SystemNotificationEvent(                          │
│     session_id=self.session_id, level=level, ...            │
│   )                                                         │
│   await self.event_bus.publish(self.session_id, event)      │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ EventBus                                                    │
│   (existing pub/sub, scoped by session)                     │
└──────────┬──────────────────────────────┬───────────────────┘
           │                              │
           ▼                              ▼
┌─────────────────────┐        ┌─────────────────────────────┐
│ TurnRunner          │        │ Protocol Server Consumers   │
│ (per-run subscriber)│        │ (ProtocolEventConsumerMixin)│
│ feeds back to stream│        │                             │
└─────────────────────┘        │  ┌───────────────────────┐  │
                               │  │ OpenCode EventProcessor│  │
                               │  │                       │  │
                               │  │ case SystemNotification│  │
                               │  │   → _render_system_*  │  │
                               │  │     → ToolPart(       │  │
                               │  │       tool="system",  │  │
                               │  │       state="completed"│  │
                               │  │     )                 │  │
                               │  │                       │  │
                               │  │ case CompactionEvent: │  │
                               │  │   → _render_system_*  │  │
                               │  │ case PlanUpdateEvent: │  │
                               │  │   → _render_system_*  │  │
                               │  │ case SessionResumeEvt:│  │
                               │  │   → _render_system_*  │  │
                               │  └───────────────────────┘  │
                               └─────────────────────────────┘
```

### Key Components

#### `SystemNotificationEvent` (new)

- **Responsibility**: Carry a system-level notification through the event stream.
- **Location**: `src/agentpool/agents/events/events.py`
- **Fields**: `session_id`, `level`, `source`, `title`, `text` (required), `ref_session_id`, `timestamp` (via `default_factory=time.time`).
- **Union membership**: Added to `RichAgentStreamEvent` PEP 695 `type` statement.

#### `AgentRunContext.emit_system_notification()` (new method)

- **Responsibility**: Public emission API for tools/capabilities/hooks.
- **Location**: `src/agentpool/agents/context.py`
- **Signature**: `async def emit_system_notification(self, *, level="info", source="system", text, title="", ref_session_id=None) -> None`
- **Implementation**: Constructs `SystemNotificationEvent`, publishes via `await self.event_bus.publish(self.session_id, event)`.
- **Error handling**: Best-effort — empty `text` logs warning and returns; `event_bus.publish` failure logs warning but does not raise.
- **Instrumentation**: `@logfire.instrument("agent.emit_system_notification {session_id}")`

#### `AgentRunContext.complete_background_task()` (modified)

- **Change**: After existing `steer_callback` call, emits `SystemNotificationEvent(source="background_task", ref_session_id=child_session_id, text=message)`.
- **Ordering**: Emission happens AFTER `steer_callback` (even if it failed) and BEFORE popping `child_done_events`.

#### `EventProcessor._render_system_notification()` (new sync helper)

- **Responsibility**: Convert a system notification to OpenCode SSE `PartUpdatedEvent` with `ToolPart`.
- **Location**: `src/agentpool_server/opencode_server/event_processor.py`
- **Signature**: `def _render_system_notification(self, ctx, level, source, title, text, ref_session_id=None) -> Iterator[Event]`
- **ToolPart construction**:
  - `id=identifier.ascending("part")` — unique, follows existing pattern
  - `message_id=ctx.assistant_msg_id` — required field
  - `session_id=ctx.session_id` — required field
  - `call_id=f"system-{uuid4().hex}"` — unique synthetic, avoids collision with real tool calls
  - `tool="system"` — synthetic tool name
  - `state="completed"` — point-in-time signal
  - `metadata={"system_notification": True}` — flag for TUI (follows `tool="elicitation"` precedent)
  - `output` — `f"[{level}] {title}: {text}"` if title, else `f"[{level}] {text}"`; append `f" (session: {ref_session_id})"` if set

#### `EventProcessor.process()` (modified — 4 new cases)

- `case SystemNotificationEvent(...)`: calls `_render_system_notification` helper
- `case CompactionEvent(trigger=trig, phase=ph)`: calls helper with `text=f"Context compacted ({trig}, {ph})"`, `source="lifecycle"`
- `case PlanUpdateEvent(entries=entries, ...)`: calls helper with `text=f"Plan updated ({len(entries)} entries)"`, `source="lifecycle"`
- `case SessionResumeEvent(resolved_call_count=count, source=src)`: calls helper with `text=f"Session resumed ({count} calls resolved, source={src})"`, `source="lifecycle"`

### Data Model

```python
@dataclass(kw_only=True)
class SystemNotificationEvent:
    session_id: str = ""
    level: Literal["info", "warning", "error", "success"] = "info"
    source: Literal["background_task", "system", "lifecycle", "custom"] = "system"
    title: str = ""
    text: str  # required, no default
    ref_session_id: str | None = None
    timestamp: float = field(default_factory=time.time)
```

### API Design

```python
# Emission from a tool
async def my_tool(ctx: RunContext[AgentContext], query: str) -> str:
    result = await analyze(query)
    await ctx.deps.run_ctx.emit_system_notification(
        level="success",
        source="custom",
        text=f"Analysis complete: {len(result)} findings",
    )
    return result

# Emission from complete_background_task (automatic)
# Already wired — no caller action needed
```

---

## Security Considerations

### Threat Analysis

| Threat | Impact | Likelihood | Mitigation |
|--------|--------|------------|------------|
| Notification content leaks to all EventBus subscribers | Medium | Low | `text` is user-authored (background task result) or system-generated (lifecycle). No credential leakage expected. For future steer/followup sources, use content-less text. |
| Synthetic `call_id` collides with real tool call | High | Low | `f"system-{uuid4().hex}"` — UUID makes collision astronomically unlikely |
| Rapid-fire notifications flood the TUI | Low | Low | No rate limiting in v1. Can add coalescing in follow-up. |

### Security Measures

- [ ] `call_id` uses UUID4 to prevent collision with real tool calls
- [ ] `text` field for `source="background_task"` is the task result message (user-authored, lower risk)
- [ ] No `text` truncation needed for v1 (background task messages are result summaries, not prompts)
- [ ] For future steer/followup sources: emit content-less text (`"System injected a message"`) to prevent prompt content leakage

---

## Implementation Plan

### Phases

#### Phase 1: Core Event Type + Emission API

- **Scope**: `SystemNotificationEvent` dataclass, union update, `emit_system_notification()` method, `complete_background_task()` wiring.
- **Deliverables**: Event type in union, emission API on `AgentRunContext`, background task completion emits notification.
- **Dependencies**: None.

#### Phase 2: OpenCode EventProcessor Mapping

- **Scope**: `_render_system_notification` helper, `SystemNotificationEvent` case, 3 lifecycle fallback cases, empirical TUI rendering verification.
- **Deliverables**: System notifications render in OpenCode TUI; lifecycle events no longer silently dropped.
- **Dependencies**: Phase 1.

#### Phase 3: Exhaustive Match Audit

- **Scope**: Grep all ~20 `match event:` sites, verify catch-all or explicit handling.
- **Deliverables**: All match sites handle or catch-all the new type.
- **Dependencies**: Phase 1.

#### Phase 4: Tests + Documentation

- **Scope**: Unit tests, integration tests, AGENTS.md updates.
- **Deliverables**: Test coverage for event type, emission API, EventProcessor mapping, lifecycle fallback; documentation updated.
- **Dependencies**: Phases 1-3.

### Milestones

| Milestone | Description | Target | Status |
|-----------|-------------|--------|--------|
| M1: Event type + emission | `SystemNotificationEvent` in union, `emit_system_notification()` working | Phase 1 complete | Not Started |
| M2: OpenCode mapping | `ToolPart(tool="system")` renders in TUI | Phase 2 complete | Not Started |
| M3: Lifecycle fallback | `CompactionEvent`/`PlanUpdateEvent`/`SessionResumeEvent` no longer dropped | Phase 2 complete | Not Started |
| M4: Audit complete | All match sites verified | Phase 3 complete | Not Started |
| M5: Tests + docs | Full test coverage, AGENTS.md updated | Phase 4 complete | Not Started |

### Rollback Strategy

Remove the `SystemNotificationEvent` class from the union, remove the `emit_system_notification()` method, remove the `EventProcessor` cases. No data migration needed — notifications are not journaled. Existing consumers with `case _:` catch-all are unaffected.

---

## Open Questions

1. **OpenCode `ToolPart` rendering of `tool="system"`**
   - Context: The TUI rendering code is in a separate repo (not in agentpool). We can't verify rendering without running the server.
   - Owner: Implementer (task 6.7)
   - Status: Open — empirical verification required. Fallback to `TextPart` with `[system]` prefix is spec'd as a requirement if rendering is broken.

2. **ACP lifecycle mapping**
   - Context: ACP's `event_converter.py` still silently drops `CompactionEvent`/`PlanUpdateEvent`/`SessionResumeEvent`. Should ACP also get the lifecycle fallback?
   - Owner: Follow-up RFC
   - Status: Open — deferred. ACP mapping requires `_agentpool/notification` extension method design.

3. **Steer/followup notification emission**
   - Context: `steer()`/`followup()` are sync; `emit_system_notification()` is async. How to bridge?
   - Owner: Follow-up RFC
   - Status: Open — deferred. Options: (a) make `steer()`/`followup()` async (breaking), (b) sync emission path, (c) emit from async caller.

4. **Rate limiting / coalescing**
   - Context: Rapid-fire `emit_system_notification()` calls could flood the TUI.
   - Owner: Future enhancement
   - Status: Open — deferred to v2 if it becomes a problem.

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

- [OpenSpec Change: add-system-notification-event](../../openspec/changes/add-system-notification-event/)
- [Spec: unified-event-routing](../../openspec/specs/unified-event-routing/spec.md) — event routing rules
- [Spec: steer-followup-api](../../openspec/specs/steer-followup-api/spec.md) — steer/followup API (sync methods)
- [RFC-0042: Unified Lifecycle Architecture](./draft/RFC-0042-unified-lifecycle-architecture.md) — CommChannel/EventBus foundation
- [RFC-0037: Unify Steer and Followup](./draft/RFC-0037-unify-steer-followup.md) — steer/followup constraints
- [RFC-0013: Subagent Event Unification](./implemented/RFC-0013-subagent-event-unification.md) — `SpawnSessionStart` precedent

### External Resources

- [ACP v2 Spec — SessionUpdate](https://agentclientprotocol.com/) — no native system notification type
- [OpenCode TUI — StreamCommit](https://github.com/sst/opencode) — `kind`/`source`/`phase` reduction pattern (client-side)

### Review History

| Reviewer | Date | Verdict | Key Findings |
|----------|------|---------|--------------|
| Oracle (Architecture) | 2026-07-19 | 3 blockers → resolved | `event_queue` doesn't exist + banned by spec; `steer()`/`followup()` are sync; journal semantics dead code |
| Metis (Pre-Planning) | 2026-07-19 | 3 blockers → resolved | Same `event_queue` + sync issues; lifecycle events have no `text`/`summary` field; `ToolPart` missing required fields |
| Momus (Plan Quality) | 2026-07-19 | OKAY | All file references verified; tasks have concrete targets |

### Revision History

| Revision | Date | Changes |
|----------|------|---------|
| 1 | 2026-07-19 | Initial draft (pre-review) |
| 2 | 2026-07-19 | Post-review: removed `event_queue` dual-path (impossible + spec-violating); deferred steer/followup emission (sync/async mismatch); dropped journal/CommChannel task (dead code); fixed lifecycle text derivation; fixed `ToolPart` construction (required fields, `identifier.ascending`, UUID `call_id`); extracted sync helper (no recursion); added exhaustive match audit; added `metadata.system_notification` flag |
| 3 | 2026-07-19 | Restored steer/followup emission via `asyncio.create_task()` (fire-and-forget — viable because `steer()` is always called from async context with running loop + active span); added `source="team"` enum value for PR #168 integration; added `ref_label` field for human-readable session references (e.g. `"member: researcher"` instead of UUID); restored `steer-followup-api` as Modified Capability; added team mode integration scenario |
