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

    Stub implementation for compatibility.
    TODO: Implement actual usage limit checking.

    Args:
        message: AssistantMessage to check for usage limits.

    Returns:
        None

    Raises:
        SomeError: If usage limit has been reached (not implemented).
    """
    pass
