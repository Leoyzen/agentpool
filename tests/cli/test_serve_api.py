from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any, Self

from agentpool_cli import serve_api


def test_api_command_manages_pool_lifecycle(monkeypatch) -> None:
    events: list[str] = []
    pools: list[FakePool] = []

    class FakeManifest:
        def __init__(self) -> None:
            self.agents: dict[str, Any] = {}
            self.teams: dict[str, Any] = {}

        @classmethod
        def from_file(cls, config_path: str) -> FakeManifest:
            return cls()

        def model_copy(self, *, update: dict[str, Any]) -> FakeManifest:
            return self

    class FakePool:
        def __init__(self, manifest: FakeManifest) -> None:
            self.manifest = manifest
            self.entered = False
            pools.append(self)

        async def __aenter__(self) -> Self:
            self.entered = True
            events.append("enter")
            return self

        async def __aexit__(self, *args: object) -> None:
            self.entered = False
            events.append("exit")

    class FakeConfigContextManager:
        def __init__(self, config_path: str) -> None:
            self.config_path = config_path

        def __enter__(self) -> None:
            return None

        def __exit__(self, *args: object) -> None:
            return None

    class FakeOpenAIAPIServer:
        def __init__(self, pool: FakePool, *, cors: bool, docs: bool) -> None:
            self.app = object()

    class FakeUvicornConfig:
        def __init__(self, app: object, **kwargs: object) -> None:
            self.app = app

    class FakeUvicornServer:
        def __init__(self, config: FakeUvicornConfig) -> None:
            self.config = config

        async def serve(self) -> None:
            assert pools[-1].entered
            events.append("serve")

    uvicorn = ModuleType("uvicorn")
    uvicorn.Config = FakeUvicornConfig  # type: ignore[attr-defined]
    uvicorn.Server = FakeUvicornServer  # type: ignore[attr-defined]
    uvicorn.run = lambda *args, **kwargs: events.append("run")  # type: ignore[attr-defined]

    agentpool = ModuleType("agentpool")
    agentpool.AgentPool = FakePool  # type: ignore[attr-defined]
    agentpool.AgentsManifest = FakeManifest  # type: ignore[attr-defined]

    config_context = ModuleType("agentpool_config.context")
    config_context.ConfigContextManager = FakeConfigContextManager  # type: ignore[attr-defined]

    server_module = ModuleType("agentpool_server.openai_api_server.server")
    server_module.OpenAIAPIServer = FakeOpenAIAPIServer  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    monkeypatch.setitem(sys.modules, "agentpool", agentpool)
    monkeypatch.setitem(sys.modules, "agentpool_config.context", config_context)
    monkeypatch.setitem(sys.modules, "agentpool_server.openai_api_server.server", server_module)
    monkeypatch.setattr(serve_api, "resolve_agent_config", lambda config: "agents.yml")

    serve_api.api_command(SimpleNamespace(obj={"log_level": "info"}), "agents.yml")  # type: ignore[arg-type]

    assert events == ["enter", "serve", "exit"]
