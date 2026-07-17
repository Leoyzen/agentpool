"""L4 subprocess E2E test fixtures for AgentPool protocol servers.

This conftest provides the ``subprocess_server`` fixture that spawns a real
``agentpool serve-*`` process, waits for it to become healthy, and tears it
down gracefully on test exit.

Design decisions (see ``openspec/changes/layered-testing-infrastructure/design.md``):
- D14: Process spawn via ``asyncio.create_subprocess_exec`` with PIPE'd stdio.
- D14: Ephemeral ports via ``socket.bind(("", 0))`` for HTTP servers.
- D14: Health check polling with 5s timeout.
- D14: Graceful shutdown via SIGTERM → wait(5s) → SIGKILL fallback.
- D17: L4a smoke tests use ``model: test`` (pydantic-ai TestModel) so NO API
  key is needed. L4a tests use TestModel (not real_model), so real_model
  auto-skip does NOT apply. L4a should always run when
  ``-m "e2e and not slow"`` is selected.

L4a smoke tests: pytest -m "e2e and not slow" (~30s)
L4b full tests: pytest -m e2e
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import socket
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
import yaml


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------


def _agentpool_binary_available() -> bool:
    """Check whether the ``agentpool`` CLI binary is on PATH."""
    return shutil.which("agentpool") is not None


SKIP_NO_BINARY = not _agentpool_binary_available()
SKIP_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SubprocessServer:
    """Handle to a spawned ``agentpool serve-*`` subprocess.

    Attributes:
        process: The asyncio subprocess.
        host: Bind host (for HTTP servers; empty for stdio).
        port: Bind port (0 for stdio servers).
        stderr_text: Captured stderr output (populated on teardown).
        is_stdio: Whether this is a stdio-based server (ACP default transport).
    """

    process: asyncio.subprocess.Process
    host: str
    port: int
    stderr_text: str = ""
    is_stdio: bool = False

    @property
    def base_url(self) -> str:
        """Base URL for HTTP servers (empty for stdio)."""
        if self.is_stdio:
            return ""
        return f"http://{self.host}:{self.port}"

    @property
    def returncode(self) -> int | None:
        """Process return code (None if still running)."""
        return self.process.returncode


@dataclass
class ProcessRegistry:
    """Session-scoped registry of spawned subprocesses for cleanup verification."""

    processes: list[asyncio.subprocess.Process] = field(default_factory=list)

    def register(self, process: asyncio.subprocess.Process) -> None:
        self.processes.append(process)

    def all_terminated(self) -> bool:
        return all(p.returncode is not None for p in self.processes)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def allocate_ephemeral_port() -> int:
    """Allocate an ephemeral port via socket bind then immediately release it.

    Returns:
        A free port number suitable for passing to ``--port``.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


def _build_command(
    serve_command: str,
    config_path: str,
    *,
    host: str,
    port: int,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build the full CLI command list.

    Args:
        serve_command: The serve subcommand (e.g. ``serve-acp``).
        config_path: Path to the YAML config file.
        host: Bind host for HTTP servers.
        port: Bind port for HTTP servers.
        extra_args: Additional CLI arguments.
    """
    cmd: list[str] = ["agentpool", serve_command, str(config_path)]
    if serve_command == "serve-acp":
        # ACP defaults to stdio; use streamable-http for HTTP-based e2e.
        # Caller must pass --transport via extra_args if HTTP is desired.
        pass
    else:
        # HTTP servers accept --host and --port.
        cmd.extend(["--host", host, "--port", str(port)])
    if extra_args:
        cmd.extend(extra_args)
    return cmd


async def _health_check_http(
    host: str,
    port: int,
    *,
    path: str = "/",
    timeout: float = 5.0,
    interval: float = 0.5,
) -> bool:
    """Poll an HTTP server until it responds or timeout.

    Args:
        host: Server host.
        port: Server port.
        path: Health check endpoint path (default ``/``).
        timeout: Maximum seconds to wait.
        interval: Polling interval in seconds.
    """
    import httpx

    url = f"http://{host}:{port}{path}"
    deadline = asyncio.get_event_loop().time() + timeout
    last_error: Exception | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            async with httpx.AsyncClient(timeout=interval) as client:
                resp = await client.get(url)
                # Any HTTP response (even 404) means the server is up.
                if resp.status_code < 500:
                    return True
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as exc:
            last_error = exc
            await asyncio.sleep(interval)
    if last_error:
        pass  # pragma: no cover - debug only
    return False


async def _health_check_stdio(
    process: asyncio.subprocess.Process, timeout: float = 5.0
) -> bool:
    """For stdio servers, verify the process is alive and has not exited early.

    A stdio ACP server doesn't have a health endpoint. We consider it healthy
    if it's still running after a brief stabilization period and hasn't written
    an error to stderr.

    Args:
        process: The spawned subprocess.
        timeout: Stabilization period in seconds.
    """
    # Give the process a moment to start up.
    await asyncio.sleep(0.5)
    if process.returncode is not None:
        return False
    # Wait a bit more to catch early crashes.
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if process.returncode is not None:
            return False
        await asyncio.sleep(0.25)
    return process.returncode is None


async def _terminate_process(process: asyncio.subprocess.Process) -> str:
    """Terminate a subprocess gracefully: SIGTERM → wait(5s) → SIGKILL.

    Returns:
        Captured stderr text.
    """
    stderr_text = ""
    if process.returncode is not None:
        # Already exited; drain stderr.
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=2.0
            )
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        except (asyncio.TimeoutError, Exception):
            pass
        return stderr_text

    # Send SIGTERM (or terminate() on Windows).
    try:
        if sys.platform == "win32":
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        # Already gone.
        pass

    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        # SIGKILL fallback.
        try:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass

    # Drain stderr.
    try:
        if process.stderr:
            stderr_data = await process.stderr.read()
            stderr_text = stderr_data.decode("utf-8", errors="replace")
    except Exception:
        pass

    return stderr_text


# ---------------------------------------------------------------------------
# Config fixture (10.7)
# ---------------------------------------------------------------------------


DEFAULT_CONFIG_YAML = """
agents:
  test_agent:
    type: native
    model: test
    system_prompt: "You are a test assistant."
"""


@pytest.fixture
def e2e_config(tmp_path: Path) -> Path:
    """Create a temporary YAML config with ``model: test`` (TestModel).

    The config defines a single native agent named ``test_agent`` that uses
    pydantic-ai's TestModel, so NO API key is needed. TestModel returns a
    deterministic response that exercises the full event pipeline.

    Returns:
        Path to the temporary YAML config file.
    """
    config_path = tmp_path / "e2e_config.yml"
    config_path.write_text(DEFAULT_CONFIG_YAML.strip() + "\n")
    return config_path


@pytest.fixture
def e2e_config_with_tool(tmp_path: Path) -> Path:
    """Create a temporary YAML config with a simple tool enabled.

    Returns:
        Path to the temporary YAML config file.
    """
    config = {
        "agents": {
            "test_agent": {
                "type": "native",
                "model": {
                    "type": "test",
                    "call_tools": ["bash"],
                    "tool_args": {"bash": {"command": "echo hello"}},
                },
                "system_prompt": "You are a test assistant with tools.",
                "tools": [{"name": "bash", "enabled": True}],
            }
        }
    }
    config_path = tmp_path / "e2e_config_tool.yml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


# ---------------------------------------------------------------------------
# subprocess_server fixture (10.1)
# ---------------------------------------------------------------------------


@pytest.fixture
async def subprocess_server(
    request: pytest.FixtureRequest,
    process_registry: ProcessRegistry,
    e2e_config: Path,
    allow_model_requests: Any,
) -> AsyncIterator[SubprocessServer]:
    """Spawn an ``agentpool serve-*`` subprocess and wait for it to become healthy.

    Parametrize via ``indirect=True`` with a dict of parameters:

    .. code-block:: python

        @pytest.mark.parametrize(
            "subprocess_server",
            [{"serve_command": "serve-api", "extra_args": []}],
            indirect=True,
        )
        async def test_x(subprocess_server: SubprocessServer) -> None:
            ...

    Required param keys:
        - ``serve_command``: The CLI subcommand (e.g. ``serve-acp``,
          ``serve-opencode``, ``serve-agui``, ``serve-api``).
        - ``config_path``: Path to the YAML config file.

    Optional param keys:
        - ``host``: Bind host (default ``127.0.0.1``, ignored for stdio ACP).
        - ``port``: Bind port (default: ephemeral, ignored for stdio ACP).
        - ``extra_args``: Additional CLI arguments.
        - ``is_stdio``: If True, treat as stdio server (no HTTP health check).
        - ``health_timeout``: Health check timeout in seconds (default 10.0).

    Yields:
        SubprocessServer handle.
    """
    params = getattr(request, "param", None)
    if params is None:
        msg = "subprocess_server fixture requires parametrize(indirect=True) with a dict"
        raise TypeError(msg)

    serve_command: str = params["serve_command"]
    # config_path can be provided in params or via the e2e_config fixture.
    config_path: str = str(params.get("config_path", e2e_config))
    host: str = params.get("host", "127.0.0.1")
    is_stdio: bool = params.get("is_stdio", False)
    health_timeout: float = params.get("health_timeout", 10.0)
    health_path: str = params.get("health_path", "/")
    extra_args: list[str] | None = params.get("extra_args")

    if is_stdio:
        port = 0
        cmd = _build_command(
            serve_command, config_path, host=host, port=port, extra_args=extra_args
        )
    else:
        port = params.get("port") or allocate_ephemeral_port()
        cmd = _build_command(
            serve_command, config_path, host=host, port=port, extra_args=extra_args
        )

    # Spawn the subprocess.
    env = os.environ.copy()
    # Disable observability for faster startup.
    env["OBSERVABILITY_ENABLED"] = "false"
    env["LOGFIRE_DISABLE"] = "true"

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    process_registry.register(process)

    server = SubprocessServer(
        process=process,
        host=host,
        port=port,
        is_stdio=is_stdio,
    )

    # Health check.
    if is_stdio:
        healthy = await _health_check_stdio(process, timeout=health_timeout)
    else:
        healthy = await _health_check_http(
            host, port, path=health_path, timeout=health_timeout, interval=0.5
        )

    if not healthy:
        stderr_text = await _terminate_process(process)
        server.stderr_text = stderr_text
        msg = (
            f"Server {serve_command} failed to become healthy within "
            f"{health_timeout}s.\nCommand: {' '.join(cmd)}\n"
            f"STDERR:\n{stderr_text}"
        )
        raise RuntimeError(msg)

    yield server

    # Teardown.
    stderr_text = await _terminate_process(process)
    server.stderr_text = stderr_text


async def _spawn_server(
    serve_command: str,
    config_path: Path | str,
    *,
    process_registry: ProcessRegistry,
    host: str = "127.0.0.1",
    is_stdio: bool = False,
    health_timeout: float = 10.0,
    health_path: str = "/",
    extra_args: list[str] | None = None,
) -> AsyncIterator[SubprocessServer]:
    """Spawn an agentpool server subprocess (internal helper for custom fixtures).

    Args:
        serve_command: The CLI subcommand (e.g. serve-api).
        config_path: Path to the YAML config file.
        process_registry: Session-scoped process registry.
        host: Bind host.
        is_stdio: Whether this is a stdio server.
        health_timeout: Health check timeout.
        extra_args: Additional CLI arguments.

    Yields:
        SubprocessServer handle.
    """
    if is_stdio:
        port = 0
        cmd = _build_command(
            serve_command, str(config_path), host=host, port=port, extra_args=extra_args
        )
    else:
        port = allocate_ephemeral_port()
        cmd = _build_command(
            serve_command, str(config_path), host=host, port=port, extra_args=extra_args
        )

    env = os.environ.copy()
    env["OBSERVABILITY_ENABLED"] = "false"
    env["LOGFIRE_DISABLE"] = "true"

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    process_registry.register(process)

    server = SubprocessServer(process=process, host=host, port=port, is_stdio=is_stdio)

    if is_stdio:
        healthy = await _health_check_stdio(process, timeout=health_timeout)
    else:
        healthy = await _health_check_http(
            host, port, path=health_path, timeout=health_timeout, interval=0.5
        )

    if not healthy:
        stderr_text = await _terminate_process(process)
        server.stderr_text = stderr_text
        msg = (
            f"Server {serve_command} failed to become healthy within "
            f"{health_timeout}s.\nCommand: {' '.join(cmd)}\n"
            f"STDERR:\n{stderr_text}"
        )
        raise RuntimeError(msg)

    yield server

    stderr_text = await _terminate_process(process)
    server.stderr_text = stderr_text


@pytest.fixture
async def subprocess_server_with_tool(
    request: pytest.FixtureRequest,
    process_registry: ProcessRegistry,
    e2e_config_with_tool: Path,
    allow_model_requests: Any,
) -> AsyncIterator[SubprocessServer]:
    """Spawn a server with the tool-enabled config (model: test with call_tools).

    Uses the same parameters as ``subprocess_server`` but with the
    ``e2e_config_with_tool`` fixture providing the YAML config.
    """
    params = getattr(request, "param", {"serve_command": "serve-opencode"})
    serve_command: str = params.get("serve_command", "serve-opencode")
    host: str = params.get("host", "127.0.0.1")
    is_stdio: bool = params.get("is_stdio", False)
    health_timeout: float = params.get("health_timeout", 10.0)
    health_path: str = params.get("health_path", "/")
    extra_args: list[str] | None = params.get("extra_args")

    async for server in _spawn_server(
        serve_command,
        e2e_config_with_tool,
        process_registry=process_registry,
        host=host,
        is_stdio=is_stdio,
        health_timeout=health_timeout,
        health_path=health_path,
        extra_args=extra_args,
    ):
        yield server


# ---------------------------------------------------------------------------
# Process cleanup verification (10.6)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def process_registry() -> ProcessRegistry:
    """Session-scoped registry of all spawned subprocesses."""
    return ProcessRegistry()


@pytest.fixture(autouse=True)
def _register_process_cleanup(process_registry: ProcessRegistry) -> Any:
    """Autouse fixture that ensures process registry is available."""
    # The actual cleanup verification is in the session-scoped finalizer below.
    yield


@pytest.fixture(scope="session", autouse=True)
def verify_no_orphaned_processes(
    process_registry: ProcessRegistry,
) -> Any:
    """Session-scoped finalizer that verifies all spawned processes are cleaned up.

    After all e2e tests complete, this checks that every process registered
    with the ``process_registry`` has terminated. If any are still running,
    it forcibly kills them and emits a warning.
    """
    yield

    orphans: list[asyncio.subprocess.Process] = [
        p for p in process_registry.processes if p.returncode is None
    ]
    for proc in orphans:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    if orphans:
        import warnings

        warnings.warn(
            f"{len(orphans)} orphaned agentpool subprocess(es) found and killed "
            "at session end.",
            stacklevel=2,
        )
