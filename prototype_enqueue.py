"""Prototype script: Test PydanticAI enqueue() drain behavior with agent.iter() + next().

This script is NOT committed to production code. It verifies:
1. PendingMessageDrainCapability is auto-injected outermost
2. Bare `async for node in agent_run:` skips after_node_run hooks and fails
3. `agent_run.next()` drains `asap` before next ModelRequestNode
4. `agent_run.next()` drains `when_idle` after all pending tool calls resolve

Run: uv run python prototype_enqueue.py
"""

from __future__ import annotations

import asyncio

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import Tool


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str]] = []


def test(name: str, condition: bool) -> None:
    status = PASS if condition else FAIL
    results.append((name, status))
    print(f"  [{status}] {name}")


# ---------------------------------------------------------------------------
# Dummy tool that enqueues messages during execution
# ---------------------------------------------------------------------------


async def enqueue_test_tool(ctx: RunContext) -> str:
    """Tool that enqueues messages with both priorities."""
    ctx.enqueue("when_idle_message", priority="when_idle")
    ctx.enqueue("asap_message", priority="asap")
    return f"tool_result_{ctx.run_step}"


# ---------------------------------------------------------------------------
# Test 1: Capability auto-injection
# ---------------------------------------------------------------------------


async def test_capability_auto_injected() -> None:
    print("\n=== Test 1: PendingMessageDrainCapability auto-injected ===")
    agent = Agent(model=TestModel(), tools=[Tool(enqueue_test_tool)])

    # _root_capability is a CombinedCapability
    root = agent._root_capability
    capability_types = [type(c).__name__ for c in root.capabilities]

    print(f"  Capabilities: {capability_types}")
    test(
        "PendingMessageDrainCapability is present",
        "PendingMessageDrainCapability" in capability_types,
    )
    test(
        "PendingMessageDrainCapability is outermost (last in list)",
        capability_types[-1] == "PendingMessageDrainCapability",
    )


# ---------------------------------------------------------------------------
# Test 2: Bare async for fails on undrained when_idle
# ---------------------------------------------------------------------------


async def test_bare_async_for_fails() -> None:
    print("\n=== Test 2: Bare `async for` fails with undrained messages ===")
    agent = Agent(model=TestModel(), tools=[Tool(enqueue_test_tool)])

    try:
        async with agent.iter("hello") as run:
            async for _node in run:
                pass  # Bare iteration skips after_node_run hooks
        test("Bare async for raises UndrainedPendingMessagesError", False)
    except Exception as e:  # noqa: BLE001
        test(
            "Bare async for raises UndrainedPendingMessagesError",
            "UndrainedPendingMessagesError" in type(e).__name__,
        )


# ---------------------------------------------------------------------------
# Test 3: next() drains asap before next ModelRequestNode
# ---------------------------------------------------------------------------


async def test_asap_drained_before_next_model_request() -> None:
    print("\n=== Test 3: asap drained before next ModelRequestNode ===")
    agent = Agent(model=TestModel(), tools=[Tool(enqueue_test_tool)])

    async with agent.iter("hello") as run:
        node = run.next_node
        asap_seen = False
        asap_drained_at: str | None = None
        last_node: str | None = None

        while True:
            node_name = type(node).__name__

            if hasattr(node, "stream"):
                async with node.stream(run.ctx) as stream:
                    async for _event in stream:
                        pass

            pending = [m.priority for m in run.pending_messages]
            if "asap" in pending:
                asap_seen = True
            if asap_seen and "asap" not in pending and asap_drained_at is None:
                asap_drained_at = last_node

            last_node = node_name
            node = await run.next(node)

            if type(node).__name__ == "End":
                break

        print(f"  asap drained at transition from: {asap_drained_at}")
        test(
            "asap drained immediately after CallToolsNode",
            asap_drained_at == "CallToolsNode",
        )


# ---------------------------------------------------------------------------
# Test 4: next() drains when_idle after all tool calls resolve
# ---------------------------------------------------------------------------


async def test_when_idle_drained_after_tool_calls() -> None:
    print("\n=== Test 4: when_idle drained after all pending tool calls ===")
    agent = Agent(model=TestModel(), tools=[Tool(enqueue_test_tool)])

    async with agent.iter("hello") as run:
        node = run.next_node
        when_idle_seen = False
        when_idle_drained_at: str | None = None
        last_node: str | None = None

        while True:
            node_name = type(node).__name__

            if hasattr(node, "stream"):
                async with node.stream(run.ctx) as stream:
                    async for _event in stream:
                        pass

            pending = [m.priority for m in run.pending_messages]
            if "when_idle" in pending:
                when_idle_seen = True
            if when_idle_seen and "when_idle" not in pending and when_idle_drained_at is None:
                when_idle_drained_at = last_node

            last_node = node_name
            node = await run.next(node)

            if type(node).__name__ == "End":
                break

        print(f"  when_idle drained at transition from: {when_idle_drained_at}")
        # when_idle should be drained at after_node_run of a CallToolsNode,
        # after all tool calls for that node have resolved.
        test(
            "when_idle drained at after_node_run of CallToolsNode",
            when_idle_drained_at == "CallToolsNode",
        )


# ---------------------------------------------------------------------------
# Test 5: Event mapping reference — document what PydanticAI yields
# ---------------------------------------------------------------------------


async def test_event_mapping() -> None:
    print("\n=== Test 5: PydanticAI node event mapping ===")
    agent = Agent(model=TestModel(), tools=[Tool(enqueue_test_tool)])

    event_log: list[tuple[str, str]] = []

    async with agent.iter("hello") as run:
        node = run.next_node
        while True:
            node_name = type(node).__name__

            if hasattr(node, "stream"):
                async with node.stream(run.ctx) as stream:
                    event_log.extend([(node_name, type(event).__name__) async for event in stream])

            node = await run.next(node)
            if type(node).__name__ == "End":
                break

    print("  Event mapping (node -> event type):")
    for node_name, event_name in event_log:
        print(f"    {node_name:<20} -> {event_name}")

    # Verify expected events exist
    events_by_node: dict[str, set[str]] = {}
    for node_name, event_name in event_log:
        events_by_node.setdefault(node_name, set()).add(event_name)

    test(
        "ModelRequestNode yields PartStartEvent / PartEndEvent",
        {"PartStartEvent", "PartEndEvent"}.issubset(events_by_node.get("ModelRequestNode", set())),
    )
    test(
        "CallToolsNode yields FunctionToolCallEvent / FunctionToolResultEvent",
        {"FunctionToolCallEvent", "FunctionToolResultEvent"}.issubset(
            events_by_node.get("CallToolsNode", set())
        ),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("=" * 70)
    print("PydanticAI enqueue() drain behavior prototype")
    print("=" * 70)

    await test_capability_auto_injected()
    await test_bare_async_for_fails()
    await test_asap_drained_before_next_model_request()
    await test_when_idle_drained_after_tool_calls()
    await test_event_mapping()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    passed = sum(1 for _, s in results if s == PASS)
    failed = sum(1 for _, s in results if s == FAIL)
    for name, status in results:
        print(f"  [{status}] {name}")
    print(f"\nTotal: {passed} passed, {failed} failed")

    if failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
