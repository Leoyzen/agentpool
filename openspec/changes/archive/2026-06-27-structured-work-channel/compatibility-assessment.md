# Compatibility Assessment: Structured Work Channel vs ACP PR #1261 & ACP v2

## ACP PR #1261: `session/inject` (queue and steer)

**Status: OPEN** — RFD proposing `session/inject` with `mode: queue | steer`

### Requirements extracted from the RFD

| Requirement | Mode | Description |
|-------------|------|-------------|
| `session/inject` queue | Queue | Buffer content, deliver when `state_change: idle` fires |
| `session/inject` steer | Steer | Deliver at next safe break-point (after tool call, mid-stream interrupt/finish) |
| `messageId` | Both | Agent-assigned, returned synchronously in inject response |
| `session/revoke_inject` | Both | Cancel a pending inject by `messageId` before delivery |
| `session/replace_inject` | Both | Replace content while preserving `messageId` and queue position (opt-in) |
| `user_message` echo | Both | Delivery notification with same `messageId` |
| FIFO within controller | Queue | Multi-client queue preserves insertion order |
| survive `session/cancel` | Both | Pending injects are not dropped by session cancel |
| `steer_in_stream` | Steer | Agent declares `["interrupt"]` / `["finish"]` for mid-stream handling |
| Sub-agent propagation | Both | Root session only; agent decides inner topology |

### Mapping to Structured Work Channel

```
session/inject (queue)
  └─ TurnRunner handler assigns messageId
    └─ writes FollowupItem(message, messageId) → work_send
    └─ adds to pending_injects[messageId]
      └─ run_loop consumer dequeues at state_change: idle
        └─ checks pending_injects → not cancelled, not replaced
          └─ delivers as user_message notification
          └─ removes from pending_injects

session/inject (steer)
  └─ TurnRunner handler assigns messageId  
    └─ writes SteerItem(message, messageId) → work_send
    └─ adds to pending_injects[messageId]
      └─ run_loop consumer dequeues at next break-point
        └─ same check/deliver flow

session/revoke_inject
  └─ marks pending_injects[messageId].cancelled = True
    └─ consumer skips on dequeue

session/replace_inject
  └─ updates pending_injects[messageId].content
    └─ consumer uses updated content on dequeue
```

### Gap: MemoryObjectStream items cannot be removed mid-stream

MemoryObjectStream is a FIFO buffer — once `send_nowait(WorkItem)` is called, the item is in the stream and will be consumed in order. Revocation cannot remove an item from the stream; it must mark it externally.

**Solution:** Add `pending_injects: dict[str, PendingInject]` to `SessionState`, where `PendingInject` carries `cancelled: bool` and `content_override: ContentBlock[] | None`. The work stream carries `WorkItem` with `messageId`. The consumer checks `pending_injects` before processing:

```python
match item:
    case QueuedItem(message=msg, messageId=mid):
        state = pending_injects.get(mid)
        if state is None or state.cancelled:
            continue  # revoked before delivery
        if state.content_override is not None:
            msg = state.content_override  # replaced before delivery
        await deliver_as_user_message(mid, msg)
        del pending_injects[mid]
```

**Extensibility cost:** ~10 lines for `PendingInject` dataclass, ~10 lines for consumer check. The existing work stream scaffolding is unchanged.

### Gap: `steer_in_stream` capability declarations

PR #1261 requires agents to declare how they handle a steer that arrives mid-LLM-stream (no tool call pending): `["interrupt"]`, `["finish"]`, or both. This is orthogonal to the work stream — it's an agent capability flag, not a work-stream feature. The `TurnState` machine tracks whether the agent is in state `RUNNING` + mid-stream vs mid-tool-call, and `steer()` dispatches accordingly.

**Extensibility cost:** 0 work-stream changes needed. Agent capabilities are a separate concern.

### Forward-compatibility verdict for PR #1261

| Requirement | Supported by work stream? | Work needed |
|---|---|---|
| Queue mode | ✅ Direct match (`FollowupItem`) | None |
| Steer mode | ✅ Direct match (`SteerItem`) | None |
| `messageId` | ✅ Via `pending_injects` dict | Small addition |
| Revoke | ✅ Via `pending_injects.cancelled` | Small addition |
| Replace | ✅ Via `pending_injects.content_override` | Small addition |
| `user_message` echo | ✅ Triggered by consumer | Protocol adapter concern |
| FIFO order | ✅ `MemoryObjectStream` natural order | None |
| survive cancel | ✅ Work stream persists through cancel | None |
| `steer_in_stream` | ⬜ Separate capability flag | Agent-level |
| Sub-agent propagation | ⬜ Agent choice, not stream | None |

**Verdict: Forward-compatible with small extensions.** The MemoryObjectStream + TurnState machine provides exactly the right abstraction. PR #1261's `queue` and `steer` modes map directly to `FollowupItem` and `SteerItem`. Adding `pending_injects` dict with `cancelled`/`content_override` fields gives revoke/replace at the cost of ~20 lines. No architectural change needed.

---

## ACP v2 Protocol

### Requirements extracted from v2 unstable schema

| Area | Methods | Relation to work stream |
|------|---------|------------------------|
| NES | `nes/start`, `nes/suggest`, `nes/accept`, `nes/reject`, `nes/close` | 🔲 Orthogonal — structured elicitation protocol |
| Document events | `document/didOpen`, `didChange`, `didClose`, `didSave`, `didFocus` | 🔲 Orthogonal — client→agent notifications |
| MCP tunneling | `mcp/connect`, `mcp/message`, `mcp/disconnect` | 🔲 Orthogonal — separate transport |
| Elicitation | `elicitation/create`, `elicitation/complete` | 🔲 Orthogonal — agent→client requests |
| Cancel | `$/cancel_request` | 🔲 Orthogonal — JSON-RPC level |
| Session fork | `session/fork` | ⚠️ Partially related — fork must not copy work stream |
| Auth | `authenticate`, `logout` | 🔲 Orthogonal |
| Prompt lifecycle | `session/prompt` (v2) | ⚠️ Related — uses `state_change: idle` which work stream respects |
| Session resume | `session/resume` | ✅ Related — work stream can help with pending state |

### Session fork (`session/fork`)

Fork creates a copy of a session at a point in time. The work stream state must be handled:
- **Pending injects belong to the parent session's running turn** — they should NOT appear in the fork
- The fork should start with an empty work stream
- `MemoryObjectStream` is naturally fork-safe: items consumed from the stream are gone; pending items in the stream are queued for the parent session's turn

**Verdict:** No action needed. Fork will naturally start with an empty work stream because `SessionState` is created fresh for the fork.

### Prompt lifecycle (v2)

The v2 prompt lifecycle defines `state_change: idle` as the signal that a turn has completed. The work stream's consumer respects this:
- On `state_change: idle`, `run_loop` enters the work stream consume loop
- If no work items are queued, the timeout triggers `Idle` → `state_change: idle` is sent
- If a `QueueItem` is consumed, a new turn starts → `state_change: running` is sent

**Verdict:** The work stream's timeout-based consume loop naturally integrates with the v2 state change lifecycle.

### Session resume (`session/resume`)

Session resume restores a session and allows the client to reconnect to a running turn. The work stream state:
- Pending injects (`pending_injects` dict) that haven't been delivered yet need to survive resume
- The work stream's `MemoryObjectStream` is in-memory — it's lost on process restart
- For durability, `pending_injects` would need to be persisted (stored with session state)

This is a known limitation of the in-memory stream, but it tracks with ACP's design:
- The v2 prompt lifecycle doesn't require pending injects to survive agent restart
- If the agent crashes mid-turn, pending injects are best-effort, same as the turn itself

**Verdict:** The in-memory work stream is sufficient for the common case (live session, no restart). Durability is a separate concern that applies equally to the current dict-based approach.

### Forward-compatibility verdict for ACP v2

| Requirement | Supported by work stream? | Work needed |
|---|---|---|
| NES | 🔲 Orthogonal | None |
| Document events | 🔲 Orthogonal | None |
| MCP tunneling | 🔲 Orthogonal | None |
| Elicitation | 🔲 Orthogonal | None |
| Cancel | 🔲 Orthogonal | None |
| Session fork | ✅ Fork naturally starts empty | None |
| Auth | 🔲 Orthogonal | None |
| Prompt lifecycle v2 | ✅ Timeout-based consume respects state_change | None |
| Session resume | ⚠️ Pending injects not persisted | Persistence concern, not architectural |

**Verdict: No blocking issues.** ACP v2 additions are orthogonal to the work stream. The work stream handles the internal routing of messages between `steer()`/`followup()` and `run_loop()`, while ACP v2 defines the protocol surface. They operate at different layers and compose naturally.

---

## Summary

```
                    ACP v1           ACP v2 (unstable)     PR #1261
                    ─────────        ────────────────     ─────────
session/prompt ─────► run_loop ────► _run_turn_unlocked
                                        │
                    session/inject ─────┤ queue ──► FollowupItem ──► work_send
                    (PR #1261)          │ steer ──► SteerItem   ──► work_send
                                        │ revoke ──► pending_injects.cancelled
                                        │ replace ─► pending_injects.content_override
                                        │
                    nes/*  ─────────────┤ (orthogonal — separate handler)
                    document/did* ──────┤ (orthogonal — separate handler)
                    mcp/* ──────────────┤ (orthogonal — separate handler)
                    elicitation/* ──────┤ (orthogonal — separate handler)
                    $/cancel_request ───┤ (orthogonal — JSON-RPC level)
                    session/fork ───────┤ (orthogonal — fork skips work stream)
                                        │
                                        ▼
                                   TurnState machine
                                   (IDLE → BOOTING → RUNNING → TEARDOWN → IDLE)
                                        │
                                        ▼
                                   run_loop consume loop
                                   (anyio.move_on_after(timeout))
                                        │
                                        ▼
                                   _run_turn_unlocked(next item)
```

**Bottom line:** The structured work channel (B+D) is the right abstraction for both ACP v2 and PR #1261. The MemoryObjectStream provides natural FIFO ordering, typed items, and backpressure — exactly what `session/inject` (queue) needs. The TurnState machine eliminates the TOCTOU class of bugs that would be amplified by PR #1261's multi-client steer scenario. The only gap is `pending_injects` dict for revoke/replace, which is a ~20 line extension — not an architectural change.
