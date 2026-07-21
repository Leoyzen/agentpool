"""Tests for AgentPool manifest-based config access."""

from __future__ import annotations

from pydantic import BaseModel
import pytest

from agentpool import AgentPool, AgentsManifest


pytestmark = pytest.mark.integration


class ConversationOutput(BaseModel):
    """Test output for conversation flow."""

    message: str
    conversation_index: int


def make_response(prompt: str) -> ConversationOutput:
    """Callback that tracks conversation order."""
    # Track what message we're on in the conversation
    make_response.count = getattr(make_response, "count", 0) + 1  # type: ignore
    return ConversationOutput(
        message=f"Response to: {prompt}",
        conversation_index=make_response.count,  # type: ignore
    )


TEST_CONFIG = f"""\
responses:
  ConversationOutput:
    response_schema:
        type: inline
        description: Output with conversation tracking
        fields:
            message:
                type: str
                description: Response message
            conversation_index:
                type: int
                description: Position in conversation

agents:
  test_agent:
    type: native
    display_name: Test Agent
    description: Agent for testing conversation flow
    model:
      type: function
      function: {__name__}.make_response
    output_type: ConversationOutput
    system_prompt: You are a test agent

  error_agent:
    display_name: Error Agent
    description: Agent that always raises errors
    model: test
    system_prompt: You are an error agent
"""


if __name__ == "__main__":
    pytest.main([__file__, "-vv"])
