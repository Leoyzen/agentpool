"""ClaudeCodeAgent Exceptions."""

from __future__ import annotations


class ThinkingModeAlreadyConfiguredError(ValueError):
    """Raised when attempting to change thinking mode when max_thinking_tokens is configured."""

    def __init__(self) -> None:
        msg = (
            "Cannot change thinking mode: max_thinking_tokens is configured. "
            "The envvar MAX_THINKING_TOKENS takes precedence over the 'ultrathink' keyword."
        )
        super().__init__(msg)


def raise_if_usage_limit_reached(message) -> None:
    """Check if usage limit has been reached.

    Note: This is currently a stub for compatibility. Usage limits
    are handled by the Claude Code SDK internally. This function exists
    to maintain API compatibility with other agent implementations.

    Args:
        message: AssistantMessage to check for usage limits.

    Returns:
        None

    Raises:
        UsageLimitExceeded: If usage limit has been reached (not implemented here).
    """
    # Usage limits are handled internally by the Claude Code SDK
    # This function is a stub for API compatibility
    pass
