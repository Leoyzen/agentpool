"""Standalone prototype validating pydantic-graph's builder-based API for AgentPool.

This script exercises:
- Parallel execution with Fork + Join and reduce_list_append
- Decision node routing based on input type
- Sequential chain of 2 Steps
- Graph.iter() streaming of intermediate steps
- Cancellation mid-stream
- Error handling in Fork branches
- DAG cycle detection (demonstrated via custom validation)
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any

from pydantic_graph import GraphBuilder, StepContext, TypeExpression
from pydantic_graph.graph_builder import EndMarker
from pydantic_graph.join import reduce_list_append
from pydantic_graph.paths import DestinationMarker


if TYPE_CHECKING:
    from pydantic_graph.id_types import NodeID
    from pydantic_graph.node_types import AnyNode


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class GraphState:
    """Shared state for graph execution."""

    log: list[str] = field(default_factory=list[str])
    counter: int = 0


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------


def build_parallel_graph() -> GraphBuilder[GraphState, None, None, list[str]]:
    """Build a graph with 3 parallel steps joined by reduce_list_append.

    Returns:
        GraphBuilder configured for parallel execution test.
    """
    g = GraphBuilder(state_type=GraphState, output_type=list[str])

    @g.step
    async def source(ctx: StepContext[GraphState, None, None]) -> str:
        """Emit the shared input for parallel branches."""
        return "hello"

    @g.step(node_id="branch_a")
    async def branch_a(ctx: StepContext[GraphState, None, str]) -> str:
        """First parallel branch."""
        ctx.state.log.append("branch_a")
        return f"{ctx.inputs}-A"

    @g.step(node_id="branch_b")
    async def branch_b(ctx: StepContext[GraphState, None, str]) -> str:
        """Second parallel branch."""
        ctx.state.log.append("branch_b")
        return f"{ctx.inputs}-B"

    @g.step(node_id="branch_c")
    async def branch_c(ctx: StepContext[GraphState, None, str]) -> str:
        """Third parallel branch."""
        ctx.state.log.append("branch_c")
        return f"{ctx.inputs}-C"

    collect = g.join(reduce_list_append, initial_factory=list[str], node_id="join_collect")

    g.add(
        g.edge_from(g.start_node).to(source),
        g.edge_from(source).to(branch_a, branch_b, branch_c),
        g.edge_from(branch_a, branch_b, branch_c).to(collect),
        g.edge_from(collect).to(g.end_node),
    )

    return g


def build_decision_graph() -> GraphBuilder[GraphState, None, int, str]:
    """Build a graph with a Decision node routing by input type.

    The graph takes an int input; positive values route to the int handler,
    non-positive values route to the str handler after a transform.

    Returns:
        GraphBuilder configured for decision routing test.
    """
    g = GraphBuilder(state_type=GraphState, input_type=int, output_type=str)

    @g.step
    async def emit_value(ctx: StepContext[GraphState, None, int]) -> int | str:
        """Return the input if positive, otherwise a string message."""
        if ctx.inputs > 0:
            return ctx.inputs
        return f"negative:{ctx.inputs}"

    @g.step
    async def handle_int(ctx: StepContext[GraphState, None, int]) -> str:
        """Branch taken when input is an int."""
        ctx.state.log.append("int_branch")
        return f"Got int: {ctx.inputs}"

    @g.step
    async def handle_str(ctx: StepContext[GraphState, None, str]) -> str:
        """Branch taken when input is a str."""
        ctx.state.log.append("str_branch")
        return f"Got str: {ctx.inputs}"

    g.add(
        g.edge_from(g.start_node).to(emit_value),
        g.edge_from(emit_value).to(
            g
            .decision(node_id="type_decision")
            .branch(g.match(TypeExpression[int]).to(handle_int))
            .branch(g.match(TypeExpression[str]).to(handle_str))
        ),
        g.edge_from(handle_int, handle_str).to(g.end_node),
    )

    return g


def build_sequential_graph() -> GraphBuilder[GraphState, None, None, str]:
    """Build a graph with a sequential chain of 2 steps.

    Returns:
        GraphBuilder configured for sequential chain test.
    """
    g = GraphBuilder(state_type=GraphState, output_type=str)

    @g.step
    async def step_one(ctx: StepContext[GraphState, None, None]) -> int:
        """First step in chain: produces an int."""
        ctx.state.log.append("step_one")
        return 10

    @g.step
    async def step_two(ctx: StepContext[GraphState, None, int]) -> str:
        """Second step in chain: consumes int, produces str."""
        ctx.state.log.append("step_two")
        return f"Result: {ctx.inputs * 3}"

    g.add(
        g.edge_from(g.start_node).to(step_one),
        g.edge_from(step_one).to(step_two),
        g.edge_from(step_two).to(g.end_node),
    )

    return g


def build_stream_graph() -> GraphBuilder[GraphState, None, None, str]:
    """Build a simple graph for streaming / iter() tests.

    Returns:
        GraphBuilder configured for streaming test.
    """
    g = GraphBuilder(state_type=GraphState, output_type=str)

    @g.step
    async def alpha(ctx: StepContext[GraphState, None, None]) -> int:
        """First step."""
        ctx.state.log.append("alpha")
        return 1

    @g.step
    async def beta(ctx: StepContext[GraphState, None, int]) -> str:
        """Second step."""
        ctx.state.log.append("beta")
        return f"final-{ctx.inputs}"

    g.add(
        g.edge_from(g.start_node).to(alpha),
        g.edge_from(alpha).to(beta),
        g.edge_from(beta).to(g.end_node),
    )

    return g


def build_error_graph() -> GraphBuilder[GraphState, None, None, list[str]]:
    """Build a graph where one fork branch raises an exception.

    Returns:
        GraphBuilder configured for error handling test.
    """
    g = GraphBuilder(state_type=GraphState, output_type=list[str])

    @g.step
    async def source(ctx: StepContext[GraphState, None, None]) -> str:
        """Emit shared input."""
        return "boom"

    @g.step(node_id="ok_branch")
    async def ok_branch(ctx: StepContext[GraphState, None, str]) -> str:
        """Branch that succeeds."""
        return f"ok-{ctx.inputs}"

    @g.step(node_id="fail_branch")
    async def fail_branch(ctx: StepContext[GraphState, None, str]) -> str:
        """Branch that raises an exception."""
        raise RuntimeError("Intentional fork branch failure")

    collect = g.join(reduce_list_append, initial_factory=list[str], node_id="error_join")

    g.add(
        g.edge_from(g.start_node).to(source),
        g.edge_from(source).to(ok_branch, fail_branch),
        g.edge_from(ok_branch, fail_branch).to(collect),
        g.edge_from(collect).to(g.end_node),
    )

    return g


# ---------------------------------------------------------------------------
# Test runners
# ---------------------------------------------------------------------------


def evidence_path(name: str) -> Path:
    """Return the path for an evidence file.

    Args:
        name: Base name of the evidence file.

    Returns:
        Path to the evidence file.
    """
    base = Path(".omo/evidence")
    base.mkdir(parents=True, exist_ok=True)
    return base / name


async def test_parallel() -> dict[str, Any]:
    """Test parallel execution: 3 steps run concurrently, Join collects results.

    Returns:
        Dictionary with test results and metadata.
    """
    g = build_parallel_graph()
    graph = g.build()
    state = GraphState()
    result = await graph.run(state=state)

    ok = sorted(result) == ["hello-A", "hello-B", "hello-C"]
    ok = ok and set(state.log) == {"branch_a", "branch_b", "branch_c"}

    return {
        "name": "parallel",
        "passed": ok,
        "result": result,
        "state_log": state.log,
    }


async def test_decision() -> dict[str, Any]:
    """Test decision routing: correct branch selected based on input type.

    Returns:
        Dictionary with test results and metadata.
    """
    g = build_decision_graph()
    graph = g.build()

    # Positive input -> int branch
    state1 = GraphState()
    result1 = await graph.run(state=state1, inputs=42)
    ok1 = result1 == "Got int: 42" and "int_branch" in state1.log

    # Non-positive input -> str branch (transformed to "negative:-5")
    state2 = GraphState()
    result2 = await graph.run(state=state2, inputs=-5)
    ok2 = result2 == "Got str: negative:-5" and "str_branch" in state2.log

    return {
        "name": "decision",
        "passed": ok1 and ok2,
        "result_int": result1,
        "result_str": result2,
    }


async def test_sequential() -> dict[str, Any]:
    """Test sequential chain: output of step 1 fed to step 2.

    Returns:
        Dictionary with test results and metadata.
    """
    g = build_sequential_graph()
    graph = g.build()
    state = GraphState()
    result = await graph.run(state=state)

    ok = result == "Result: 30"
    ok = ok and state.log == ["step_one", "step_two"]

    return {
        "name": "sequential",
        "passed": ok,
        "result": result,
        "state_log": state.log,
    }


async def test_stream() -> dict[str, Any]:
    """Test Graph.iter() yields intermediate steps.

    Returns:
        Dictionary with test results and metadata.
    """
    g = build_stream_graph()
    graph = g.build()
    state = GraphState()

    events: list[Any] = []
    async with graph.iter(state=state) as run:
        events.extend([event async for event in run])

    # Should see task lists and a final EndMarker
    task_events = [e for e in events if isinstance(e, list)]
    end_events = [e for e in events if isinstance(e, EndMarker)]

    ok = len(task_events) > 0 and len(end_events) == 1
    ok = ok and end_events[0].value == "final-1"  # pyright: ignore[reportUnknownMemberType]

    return {
        "name": "stream",
        "passed": ok,
        "event_count": len(events),
        "task_events": len(task_events),
        "end_events": len(end_events),
    }


async def test_cancel() -> dict[str, Any]:
    """Test cancellation mid-stream: task cancels cleanly without hanging.

    Returns:
        Dictionary with test results and metadata.
    """
    g = build_stream_graph()
    graph = g.build()
    state = GraphState()

    events: list[Any] = []
    async with graph.iter(state=state) as run:
        async for event in run:
            events.append(event)
            if len(events) >= 2:  # noqa: PLR2004
                break  # Cancel early

    # We broke out early; no exception should have been raised
    ok = len(events) >= 2  # noqa: PLR2004
    # Not all steps should have completed
    ok = ok and len(state.log) < 2  # noqa: PLR2004

    return {
        "name": "cancel",
        "passed": ok,
        "events_before_cancel": len(events),
        "state_log": state.log,
    }


async def test_error() -> dict[str, Any]:
    """Test error handling: exception in one fork branch propagates correctly.

    Returns:
        Dictionary with test results and metadata.
    """
    g = build_error_graph()
    graph = g.build()
    state = GraphState()

    raised: BaseException | None = None
    try:
        await graph.run(state=state)
    except Exception as exc:  # noqa: BLE001
        raised = exc

    ok = raised is not None
    ok = ok and isinstance(raised, RuntimeError)
    ok = ok and "Intentional fork branch failure" in str(raised)

    return {
        "name": "error",
        "passed": ok,
        "exception_type": type(raised).__name__ if raised else None,
        "exception_msg": str(raised) if raised else None,
    }


def _detect_cycles(
    nodes: dict[NodeID, AnyNode],
    edges_by_source: dict[NodeID, list[Any]],
) -> list[NodeID] | None:
    """Detect cycles in a graph via DFS.

    Args:
        nodes: All nodes in the graph.
        edges_by_source: Outgoing edges indexed by source node ID.

    Returns:
        A list of node IDs forming a cycle, or None if acyclic.
    """
    visited: set[NodeID] = set()
    rec_stack: set[NodeID] = set()

    def _neighbors(node_id: NodeID) -> list[NodeID]:
        """Extract destination IDs from outgoing paths."""
        return [
            item.destination_id
            for path in edges_by_source.get(node_id, [])
            for item in (path.items if hasattr(path, "items") else [])
            if isinstance(item, DestinationMarker)
        ]

    def _dfs(node_id: NodeID) -> list[NodeID] | None:
        visited.add(node_id)
        rec_stack.add(node_id)
        for nxt in _neighbors(node_id):
            if nxt not in visited:
                cycle = _dfs(nxt)
                if cycle is not None:
                    return cycle
            elif nxt in rec_stack:
                return [node_id, nxt]
        rec_stack.discard(node_id)
        return None

    for nid in nodes:
        if nid not in visited:
            cycle = _dfs(nid)
            if cycle is not None:
                return cycle
    return None


async def test_cycle() -> dict[str, Any]:
    """Test DAG cycle detection.

    pydantic-graph's GraphBuilder does **not** detect cycles at build time.
    This test validates that behavior and demonstrates a custom cycle
    detector that can be used by AgentPool if needed.

    Returns:
        Dictionary with test results and metadata.
    """
    g = GraphBuilder(output_type=str)

    @g.step
    async def a_step(ctx: StepContext[None, None, None]) -> str:
        return "a"

    @g.step
    async def b_step(ctx: StepContext[None, None, str]) -> str:
        return "b"

    # Normal forward edges
    g.add(
        g.edge_from(g.start_node).to(a_step),
        g.edge_from(a_step).to(b_step),
        g.edge_from(b_step).to(g.end_node),
    )

    # Add a backward edge to create a cycle
    g.add_edge(b_step, a_step)

    # pydantic-graph builds the graph without error
    graph = g.build()
    build_succeeded = graph is not None

    # Our custom cycle detector finds the cycle
    cycle = _detect_cycles(graph.nodes, graph.edges_by_source)
    cycle_found = cycle is not None

    ok = build_succeeded and cycle_found

    return {
        "name": "cycle",
        "passed": ok,
        "build_succeeded": build_succeeded,
        "cycle_detected": cycle_found,
        "cycle_nodes": cycle,
        "note": "pydantic-graph does not detect cycles; custom validation needed",
    }


# ---------------------------------------------------------------------------
# Main / CLI
# ---------------------------------------------------------------------------


TESTS = {
    "parallel": test_parallel,
    "decision": test_decision,
    "sequential": test_sequential,
    "stream": test_stream,
    "cancel": test_cancel,
    "error": test_error,
    "cycle": test_cycle,
}


async def run_single(name: str) -> dict[str, Any]:
    """Run a single test and persist evidence.

    Args:
        name: Test identifier.

    Returns:
        Test result dictionary.
    """
    result = await TESTS[name]()
    path = evidence_path(f"task-1-{name}.txt")
    path.write_text(
        f"Test: {name}\nPassed: {result['passed']}\nDetails: {result}\n",
        encoding="utf-8",
    )
    return result


async def main() -> int:
    """CLI entrypoint.

    Returns:
        Exit code (0 on success, 1 on failure).
    """
    parser = argparse.ArgumentParser(description="Validate pydantic-graph builder API")
    parser.add_argument("--test-parallel", action="store_true", help="Run parallel execution test")
    parser.add_argument("--test-decision", action="store_true", help="Run decision routing test")
    parser.add_argument("--test-sequential", action="store_true", help="Run sequential chain test")
    parser.add_argument("--test-stream", action="store_true", help="Run streaming test")
    parser.add_argument("--test-cancel", action="store_true", help="Run cancellation test")
    parser.add_argument("--test-error", action="store_true", help="Run error handling test")
    parser.add_argument("--test-cycle", action="store_true", help="Run cycle detection test")
    parser.add_argument("--test-all", action="store_true", help="Run all tests")
    args = parser.parse_args()

    selected = [name for name in TESTS if getattr(args, f"test_{name}") or args.test_all]

    if not selected:
        parser.print_help()
        return 1

    results: list[dict[str, Any]] = []
    for name in selected:
        result = await run_single(name)
        results.append(result)
        status = "PASS" if result["passed"] else "FAIL"
        print(f"  [{status}] {name}")

    # Append findings to learnings.md
    notepad = Path(".omo/notepads/migrate-to-pydantic-graph/learnings.md")
    notepad.parent.mkdir(parents=True, exist_ok=True)
    with notepad.open("a", encoding="utf-8") as fh:
        fh.write("\n## Prototype Validation Findings\n\n")
        fh.write("Date: 2026-06-03\n\n")
        for r in results:
            fh.write(f"- **{r['name']}**: {'PASS' if r['passed'] else 'FAIL'}\n")
            if not r["passed"]:
                fh.write(f"  - Details: {r}\n")
        fh.write("\n")

    all_passed = all(r["passed"] for r in results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
