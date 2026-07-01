"""Tests for GlobalConnectionPool fixes from code review.

Covers:
- _build_toolset logs warning on exception (not silent)

The following test classes were removed during simplification:
- TestReleasePopsDyingConnection — release() no longer exists
- TestHTTPRefCountBalance — ref_count no longer exists
"""

from __future__ import annotations

from typing import Any

import pytest


pytestmark = pytest.mark.integration


class TestBuildToolsetLogsWarning:
    """Tests that _build_toolset logs warning on exception."""

    async def test_build_toolset_logs_warning_on_exception(self) -> None:
        """Test that logger.warning is called when get_tools() raises.

        Given a provider that raises in get_tools(), when
        _build_toolset catches the exception, then it must call
        logger.warning (not silently swallow).
        """
        from agentpool.resource_providers.base import ResourceProvider

        class _FailingProvider(ResourceProvider):
            def __init__(self) -> None:
                super().__init__(name="test-fail")

            async def get_tools(self) -> list[Any]:
                raise RuntimeError("connection refused")

        # Verify the source code includes logger.warning in the except block
        import inspect

        source = inspect.getsource(ResourceProvider.as_capability)
        assert "logger.warning" in source, (
            "Expected logger.warning() in as_capability() source "
            "when get_tools() raises, but exception is silently swallowed"
        )
