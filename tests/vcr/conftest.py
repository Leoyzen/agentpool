"""Shared fixtures for L3 VCR tests.

The ``vcr_pool`` fixture builds a real ``AgentPool`` from inline YAML config
with a single native agent using the ``openai:gpt-4o-mini`` model. VCR
intercepts model API HTTP calls — the pool, agents, capabilities, EventBus,
SessionController, and protocol stacks all run for real in-process.

See ``tests/AGENTS.md`` for the VCR recording workflow and
``openspec/changes/layered-testing-infrastructure/design.md`` for design D6
(VCR scope: model API HTTP only) and D15 (``vcr_pool`` fixture).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yamling

from agentpool import AgentPool, AgentsManifest


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# Inline YAML config used by the vcr_pool fixture. Uses openai:gpt-4o-mini
# because that is the reference model for cassette recording (D14). The
# `remap_hardcoded_test_models` session fixture in tests/conftest.py will
# transparently remap this to TEST_MODEL_OVERRIDE if that env var is set,
# so custom endpoints without gpt-4o access still work for recording.
VCR_POOL_CONFIG = """\
agents:
  test_agent:
    type: native
    model: openai:gpt-4o-mini
    system_prompt: "You are a helpful test assistant for VCR cassette replay."
"""

# Config with a single agent exposing a tool (used by tool-call tests).
VCR_POOL_CONFIG_WITH_TOOL = """\
agents:
  test_agent:
    type: native
    model: openai:gpt-4o-mini
    system_prompt: "You are a helpful test assistant. Use tools when asked."
    tools:
      - name: echo
        enabled: true
"""

# Config with a coordinator + worker agent for subagent delegation tests.
VCR_POOL_CONFIG_WITH_SUBAGENT = """\
agents:
  coordinator:
    type: native
    model: openai:gpt-4o-mini
    system_prompt: "You coordinate tasks. Delegate to the worker when helpful."
    tools:
      - type: subagent
  worker:
    type: native
    model: openai:gpt-4o-mini
    system_prompt: "You are a worker agent. Complete tasks concisely."
"""

# Config with team_mode enabled for dynamic team mode VCR tests.
VCR_POOL_CONFIG_TEAM_MODE = """\
agents:
  team_lead:
    type: native
    model: openai:gpt-4o-mini
    system_prompt: "You are a team lead. Use team tools to coordinate members."
  team_member:
    type: native
    model: openai:gpt-4o-mini
    system_prompt: "You are a team member. Follow instructions from the lead."

team_mode:
  enabled: true
  lead_eligible:
    - team_lead
  member_eligible:
    - team_lead
    - team_member
"""


def _build_manifest(yaml_text: str) -> AgentsManifest:
    """Parse inline YAML into an ``AgentsManifest``."""
    raw = yamling.load_yaml(yaml_text, verify_type=dict)
    return AgentsManifest.model_validate(raw)


@pytest.fixture
async def vcr_pool() -> AsyncIterator[AgentPool]:
    """Real ``AgentPool`` with VCR-replayed model responses.

    The pool, agents, capabilities, EventBus, SessionController are all real.
    Only the model API HTTP calls are intercepted by VCR (design D6). Use
    ``--record-mode=once`` with ``OPENAI_API_KEY`` set to record a cassette;
    CI replays the cassette with no network access.
    """
    manifest = _build_manifest(VCR_POOL_CONFIG)
    async with AgentPool(manifest) as pool:
        yield pool


@pytest.fixture
async def vcr_pool_with_tool() -> AsyncIterator[AgentPool]:
    """Real ``AgentPool`` with a single agent that exposes the ``echo`` tool.

    Used by tool-call VCR tests (P2 pattern). The ``echo`` tool is provided
    programmatically by the test (not via the YAML tool config) because the
    inline config cannot define a Python callable. Tests that need the tool
    attach it to the agent after pool construction.
    """
    manifest = _build_manifest(VCR_POOL_CONFIG_WITH_TOOL)
    async with AgentPool(manifest) as pool:
        yield pool


@pytest.fixture
async def vcr_pool_with_subagent() -> AsyncIterator[AgentPool]:
    """Real ``AgentPool`` with a coordinator + worker for delegation tests."""
    manifest = _build_manifest(VCR_POOL_CONFIG_WITH_SUBAGENT)
    async with AgentPool(manifest) as pool:
        yield pool


@pytest.fixture
async def vcr_team_pool() -> AsyncIterator[AgentPool]:
    """Real ``AgentPool`` with ``team_mode`` enabled for VCR team-mode tests.

    Two agents: ``team_lead`` (lead-eligible) and ``team_member``
    (member-eligible). VCR intercepts model API HTTP calls — the pool,
    agents, capabilities, EventBus, SessionController all run for real.
    """
    manifest = _build_manifest(VCR_POOL_CONFIG_TEAM_MODE)
    async with AgentPool(manifest) as pool:
        yield pool


@pytest.fixture
def vcr_config_path(tmp_path: Path) -> Path:
    """Path to a minimal YAML config file suitable for VCR recording.

    Writes the inline ``VCR_POOL_CONFIG`` to a temp file so protocol-server
    tests that load config from a path (e.g. ``ACPServer.from_config``) have
    a file to point at.
    """
    config_path = tmp_path / "vcr_test_config.yml"
    config_path.write_text(VCR_POOL_CONFIG)
    return config_path


@pytest.fixture
def vcr_cassettes_dir() -> Path:
    """Root directory holding VCR cassettes (``tests/cassettes/vcr/``)."""
    return Path(__file__).parent.parent / "cassettes" / "vcr"


def cassette_exists(test_module_stem: str, test_name: str) -> bool:
    """Check if a VCR cassette exists for the given test.

    Cassettes follow the convention
    ``tests/cassettes/vcr/<test_module_stem>/<test_name>.yaml`` (see
    ``tests/AGENTS.md``). Tests that have not yet had their cassette
    recorded ([HUMAN-REQUIRED]) use this to skip gracefully in CI.
    """
    cassette_path = (
        Path(__file__).parent.parent / "cassettes" / "vcr" / test_module_stem / f"{test_name}.yaml"
    )
    return cassette_path.exists()
