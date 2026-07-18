"""L4a smoke E2E test for dynamic team mode via ``agentpool serve-opencode``.

Spawns a real ``agentpool serve-opencode`` subprocess with a YAML config
that enables ``team_mode`` and uses ``model: test`` (pydantic-ai
TestModel — no API key needed). Verifies the server starts, accepts a
prompt, and emits SSE events.

This is an **L4a smoke test** (``@pytest.mark.e2e``, NOT
``@pytest.mark.slow``). It runs in ~30s and catches "server won't start
with team_mode" regressions. L4b full tests (multi-turn, tool calls,
blackboard) belong in a separate ``@pytest.mark.slow`` test file.

See ``tests/AGENTS.md`` § "L4 Sub-layers" and
``openspec/changes/layered-testing-infrastructure/design.md`` D17.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import yaml

from tests.e2e.conftest import SKIP_NO_BINARY, SKIP_WINDOWS, _spawn_server


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from tests.e2e.conftest import ProcessRegistry, SubprocessServer


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(SKIP_NO_BINARY, reason="agentpool binary not on PATH"),
    pytest.mark.skipif(SKIP_WINDOWS, reason="Windows subprocess issues"),
]


# ---------------------------------------------------------------------------
# Team-mode config (model: test — no API key needed)
# ---------------------------------------------------------------------------


_TEAM_MODE_CONFIG = {
    "agents": {
        "team_lead": {
            "type": "native",
            "model": "test",
            "system_prompt": "You are a team lead. Use team tools to coordinate.",
        },
        "team_member": {
            "type": "native",
            "model": "test",
            "system_prompt": "You are a team member.",
        },
    },
    "team_mode": {
        "enabled": True,
        "lead_eligible": ["team_lead"],
        "member_eligible": ["team_lead", "team_member"],
    },
}


# ---------------------------------------------------------------------------
# Fixture: team-mode subprocess server
# ---------------------------------------------------------------------------


@pytest.fixture
async def team_mode_server(
    tmp_path: Path,
    process_registry: ProcessRegistry,
    allow_model_requests: Any,
) -> AsyncIterator[SubprocessServer]:
    """Spawn ``agentpool serve-opencode`` with a team-mode config (TestModel).

    Writes the team-mode YAML config to ``tmp_path``, spawns the server
    via the shared ``_spawn_server`` helper from ``tests/e2e/conftest.py``,
    and yields a ``SubprocessServer`` handle. Teardown is handled by
    ``_spawn_server`` (SIGTERM → wait → SIGKILL fallback).
    """
    config_path = tmp_path / "team_mode_e2e.yml"
    config_path.write_text(yaml.dump(_TEAM_MODE_CONFIG, default_flow_style=False))

    async for server in _spawn_server(
        "serve-opencode",
        config_path,
        process_registry=process_registry,
        is_stdio=False,
        health_path="/session",
        health_timeout=15.0,
    ):
        yield server


# ---------------------------------------------------------------------------
# L4a smoke test
# ---------------------------------------------------------------------------


async def test_team_mode_serve_opencode_smoke(
    team_mode_server: SubprocessServer,
) -> None:
    """L4a smoke: serve-opencode with team_mode + TestModel → basic prompt works.

    Given: a YAML config with ``team_mode`` enabled and ``model: test``.

    When: ``agentpool serve-opencode`` is spawned as a subprocess and a
        basic prompt is sent via the async prompt endpoint.

    Then:
    - The server starts and responds to HTTP health checks.
    - A session can be created.
    - The async prompt endpoint accepts a message (204 No Content).
    - SSE events are emitted on the ``/event`` stream.
    - The server shuts down cleanly on teardown.
    """
    base_url = team_mode_server.base_url

    # --- 1. Verify server is alive ----------------------------------------
    assert team_mode_server.process.returncode is None, "serve-opencode process exited early"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # --- 2. Create a session ------------------------------------------
        resp = await client.post(f"{base_url}/session", json={})
        assert resp.status_code in (200, 201), (
            f"Failed to create session: {resp.status_code} {resp.text}"
        )
        session_data = resp.json()
        session_id = session_data.get("id") or session_data.get("sessionID")
        assert session_id, f"Expected session ID in response: {session_data}"

        # --- 3. Start SSE event stream (background) -----------------------
        # Open the SSE /event endpoint in a streaming connection so we
        # can capture events emitted when the prompt is processed.
        events_received: list[dict[str, Any]] = []

        async def _collect_sse_events() -> None:
            """Consume SSE events from /event until cancelled or max reached."""
            async with (
                httpx.AsyncClient(timeout=60.0) as sse_client,
                sse_client.stream("GET", f"{base_url}/event") as response,
            ):
                event_type: str | None = None
                event_data: str = ""
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_type = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        event_data = line[len("data:") :].strip()
                    elif line == "":
                        # End of SSE event block.
                        if event_type is not None:
                            events_received.append({"type": event_type, "data": event_data})
                            # Stop after collecting enough events —
                            # this is a smoke test.
                            if len(events_received) >= 5:
                                return
                        event_type = None
                        event_data = ""

        sse_task = asyncio.create_task(_collect_sse_events())

        # Give the SSE connection a moment to establish.
        await asyncio.sleep(0.5)

        # --- 4. Send a basic prompt via async endpoint --------------------
        message_payload: dict[str, Any] = {
            "parts": [{"type": "text", "text": "Hello, team lead!"}],
        }
        resp = await client.post(
            f"{base_url}/session/{session_id}/prompt_async",
            json=message_payload,
        )
        assert resp.status_code == 204, (
            f"Expected 204 from prompt_async, got {resp.status_code} {resp.text}"
        )

        # --- 5. Wait for SSE events ---------------------------------------
        # The TestModel responds quickly. Wait up to 10 seconds for events.
        try:
            await asyncio.wait_for(asyncio.shield(sse_task), timeout=10.0)
        except TimeoutError:
            sse_task.cancel()
            with contextlib.suppress(BaseException):
                await sse_task

        # --- 6. Assert events were received -------------------------------
        assert len(events_received) > 0, (
            "Expected at least one SSE event from /event stream after "
            "sending a prompt. Server may not be emitting events."
        )

    # --- 7. Verify process is still alive (clean state) -------------------
    assert team_mode_server.process.returncode is None, (
        "serve-opencode process died during the test"
    )
    # The team_mode_server fixture handles graceful shutdown on teardown.
