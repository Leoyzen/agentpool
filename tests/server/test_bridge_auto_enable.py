"""Tests for protocol bridge auto-enable on server startup.

This module tests that all protocol servers (ACP, AG-UI, OpenCode)
automatically wire up their skill command bridges when the pool
has skill commands configured.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from upathtools import UPath

from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.skill import Skill
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.commands.skill_commands import ACPSkillBridge
from agentpool_server.agui_server.server import AGUIServer
from agentpool_server.agui_server.skill_tools import AGUISkillBridge
from agentpool_server.opencode_server.server import create_app


@pytest.fixture
def sample_skill() -> Skill:
    """Create a sample skill for testing."""
    return Skill(
        name="test-skill",
        description="A test skill for bridge testing",
        skill_path=UPath("/tmp/test-skill"),
    )


@pytest.fixture
def sample_command(sample_skill: Skill) -> SkillCommand:
    """Create a sample SkillCommand for testing."""
    return SkillCommand(
        name="test-skill",
        description="A test skill for bridge testing",
        skill=sample_skill,
        input_hint="Provide arguments",
    )


@pytest.fixture
def mock_pool_with_skills(sample_command: SkillCommand) -> MagicMock:
    """Create a mock pool with skill commands configured."""
    pool = MagicMock()
    pool.skill_commands = SkillCommandRegistry()
    pool.skill_commands.register("test-skill", sample_command)
    pool.manifest.agents = {}
    pool.manifest.config_file_path = "/test/config.yml"
    return pool


@pytest.fixture
def mock_pool_no_skills() -> MagicMock:
    """Create a mock pool without skill commands."""
    pool = MagicMock()
    pool.skill_commands = None
    pool.manifest.agents = {}
    pool.manifest.config_file_path = "/test/config.yml"
    return pool


class TestACPBridgeAutoEnable:
    """Test ACP server skill bridge auto-enable."""

    @pytest.fixture
    def mock_agent(self, mock_pool_with_skills: MagicMock) -> MagicMock:
        """Create a mock agent with pool reference."""
        agent = MagicMock()
        agent.name = "test_agent"
        agent.agent_pool = mock_pool_with_skills
        agent.model_name = "test-model"
        return agent

    def test_acp_wires_bridge_when_pool_has_skill_commands(
        self,
        mock_agent: MagicMock,
        mock_pool_with_skills: MagicMock,
    ) -> None:
        """Test ACP agent wires up bridge when pool has skill commands."""
        with patch("agentpool_server.acp_server.acp_agent.ACPSessionManager"):
            acp_agent = AgentPoolACPAgent(
                client=MagicMock(),
                default_agent=mock_agent,
            )

        assert acp_agent._skill_bridge is not None
        assert isinstance(acp_agent._skill_bridge, ACPSkillBridge)

    def test_acp_no_bridge_when_pool_has_no_skills(
        self,
        mock_pool_no_skills: MagicMock,
    ) -> None:
        """Test ACP agent does not wire bridge when pool has no skill commands."""
        agent = MagicMock()
        agent.name = "test_agent"
        agent.agent_pool = mock_pool_no_skills
        agent.model_name = "test-model"

        with patch("agentpool_server.acp_server.acp_agent.ACPSessionManager"):
            acp_agent = AgentPoolACPAgent(
                client=MagicMock(),
                default_agent=agent,
            )

        assert acp_agent._skill_bridge is None

    def test_acp_bridge_receives_commands_from_registry(
        self,
        mock_agent: MagicMock,
        mock_pool_with_skills: MagicMock,
        sample_command: SkillCommand,
    ) -> None:
        """Test ACP bridge receives commands from registry on setup."""
        with patch("agentpool_server.acp_server.acp_agent.ACPSessionManager"):
            acp_agent = AgentPoolACPAgent(
                client=MagicMock(),
                default_agent=mock_agent,
            )

        assert acp_agent._skill_bridge is not None
        # Bridge should receive existing commands from registry
        commands = acp_agent._skill_bridge.get_available_commands()
        assert len(commands) == 1
        assert commands[0].name == "test-skill"


class TestAGUIBridgeAutoEnable:
    """Test AG-UI server skill bridge auto-enable."""

    def test_agui_wires_bridge_when_pool_has_skill_commands(
        self,
        mock_pool_with_skills: MagicMock,
    ) -> None:
        """Test AG-UI server wires up bridge when pool has skill commands."""
        server = AGUIServer(mock_pool_with_skills)

        assert server._skill_bridge is not None
        assert isinstance(server._skill_bridge, AGUISkillBridge)

    def test_agui_no_bridge_when_pool_has_no_skills(
        self,
        mock_pool_no_skills: MagicMock,
    ) -> None:
        """Test AG-UI server does not wire bridge when pool has no skill commands."""
        server = AGUIServer(mock_pool_no_skills)

        assert server._skill_bridge is None

    def test_agui_bridge_receives_commands_from_registry(
        self,
        mock_pool_with_skills: MagicMock,
    ) -> None:
        """Test AG-UI bridge receives commands from registry on setup."""
        server = AGUIServer(mock_pool_with_skills)

        assert server._skill_bridge is not None
        # Bridge should receive existing commands from registry
        tools = server._skill_bridge.get_tools()
        assert len(tools) == 1
        assert tools[0].name == "skill__test-skill"


class TestOpenCodeBridgeAutoEnable:
    """Test OpenCode server skill bridge auto-enable."""

    @pytest.fixture
    def mock_agent_with_pool(self, mock_pool_with_skills: MagicMock) -> MagicMock:
        """Create a mock agent with pool reference."""
        agent = MagicMock()
        agent.name = "test_agent"
        agent.agent_pool = mock_pool_with_skills
        agent.model_name = "test-model"
        agent.env = MagicMock()
        agent.storage = MagicMock()
        return agent

    @pytest.fixture
    def mock_agent_no_skills(self, mock_pool_no_skills: MagicMock) -> MagicMock:
        """Create a mock agent without skills."""
        agent = MagicMock()
        agent.name = "test_agent"
        agent.agent_pool = mock_pool_no_skills
        agent.model_name = "test-model"
        agent.env = MagicMock()
        agent.storage = MagicMock()
        return agent

    @pytest.mark.anyio
    async def test_opencode_wires_bridge_when_pool_has_skill_commands(
        self,
        mock_agent_with_pool: MagicMock,
        mock_pool_with_skills: MagicMock,
    ) -> None:
        """Test OpenCode server wires up bridge when pool has skill commands."""
        with (
            patch("agentpool_server.opencode_server.server.logger") as mock_logger,
            patch("agentpool_server.opencode_server.server.ServerState") as mock_state_cls,
        ):
            mock_state = MagicMock()
            mock_state.pool = mock_pool_with_skills
            mock_state.agent = mock_agent_with_pool
            mock_state.working_dir = "/test"
            mock_state.sessions = {}
            mock_state.messages = {}
            mock_state.reverted_messages = {}
            mock_state.todos = {}
            mock_state.input_providers = {}
            mock_state.pending_questions = {}
            mock_state.event_subscribers = []
            mock_state.on_first_subscriber = None
            mock_state.background_tasks = set()
            mock_state.event_managers = {}
            mock_state.agent.env.get_fs.return_value = MagicMock()
            mock_state_cls.return_value = mock_state

            with patch("agentpool_server.opencode_server.state.LSPManager"):
                create_app(agent=mock_agent_with_pool)

        # Verify bridge was set up
        mock_logger.debug.assert_called_once()
        call_args = mock_logger.debug.call_args
        assert "OpenCode skill bridge setup complete" in str(call_args)

    @pytest.mark.anyio
    async def test_opencode_no_bridge_when_pool_has_no_skills(
        self,
        mock_agent_no_skills: MagicMock,
        mock_pool_no_skills: MagicMock,
    ) -> None:
        """Test OpenCode server does not wire bridge when pool has no skill commands."""
        with (
            patch("agentpool_server.opencode_server.server.logger") as mock_logger,
            patch("agentpool_server.opencode_server.server.ServerState") as mock_state_cls,
        ):
            mock_state = MagicMock()
            mock_state.pool = mock_pool_no_skills
            mock_state.agent = mock_agent_no_skills
            mock_state.working_dir = "/test"
            mock_state.sessions = {}
            mock_state.messages = {}
            mock_state.reverted_messages = {}
            mock_state.todos = {}
            mock_state.input_providers = {}
            mock_state.pending_questions = {}
            mock_state.event_subscribers = []
            mock_state.on_first_subscriber = None
            mock_state.background_tasks = set()
            mock_state.event_managers = {}
            mock_state.agent.env.get_fs.return_value = MagicMock()
            mock_state_cls.return_value = mock_state

            with patch("agentpool_server.opencode_server.state.LSPManager"):
                create_app(agent=mock_agent_no_skills)

        # Verify no bridge setup log was made
        for call in mock_logger.debug.call_args_list:
            assert "OpenCode skill bridge setup complete" not in str(call)


class TestBridgesReceiveCommands:
    """Test that bridges receive commands when skills are added to registry."""

    def test_acp_bridge_receives_new_commands(
        self,
        mock_pool_with_skills: MagicMock,
    ) -> None:
        """Test ACP bridge receives new commands added to registry."""
        agent = MagicMock()
        agent.name = "test_agent"
        agent.agent_pool = mock_pool_with_skills
        agent.model_name = "test-model"

        with patch("agentpool_server.acp_server.acp_agent.ACPSessionManager"):
            acp_agent = AgentPoolACPAgent(
                client=MagicMock(),
                default_agent=agent,
            )

        # Add a new command to the registry
        new_skill = Skill(
            name="new-skill",
            description="A new skill",
            skill_path=UPath("/tmp/new-skill"),
        )
        new_command = SkillCommand(
            name="new-skill",
            description="A new skill",
            skill=new_skill,
            input_hint="Arguments",
        )
        mock_pool_with_skills.skill_commands.register("new-skill", new_command)

        # Bridge should now have both commands
        assert acp_agent._skill_bridge is not None
        commands = acp_agent._skill_bridge.get_available_commands()
        assert len(commands) == 2
        command_names = {cmd.name for cmd in commands}
        assert command_names == {"test-skill", "new-skill"}

    def test_agui_bridge_receives_new_commands(
        self,
        mock_pool_with_skills: MagicMock,
    ) -> None:
        """Test AG-UI bridge receives new commands added to registry."""
        server = AGUIServer(mock_pool_with_skills)

        # Add a new command to the registry
        new_skill = Skill(
            name="new-skill",
            description="A new skill",
            skill_path=UPath("/tmp/new-skill"),
        )
        new_command = SkillCommand(
            name="new-skill",
            description="A new skill",
            skill=new_skill,
            input_hint="Arguments",
        )
        mock_pool_with_skills.skill_commands.register("new-skill", new_command)

        # Bridge should now have both tools
        assert server._skill_bridge is not None
        tools = server._skill_bridge.get_tools()
        assert len(tools) == 2
        tool_names = {tool.name for tool in tools}
        assert tool_names == {"skill__test-skill", "skill__new-skill"}
