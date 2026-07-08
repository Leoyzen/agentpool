"""Tests for ContextInjectionProxy — injects AGENTS.md and skill instructions.

Covers: AGENTS.md injection, skill instruction injection, missing file
passthrough, non-prompt method passthrough.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from acp.proxy.impls.context_injection import ContextInjectionProxy
from acp.proxy.protocol import Proxy


if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Proxy protocol compliance
# ---------------------------------------------------------------------------


def test_context_injection_implements_proxy_protocol(
    tmp_path: Path,
) -> None:
    """ContextInjectionProxy satisfies the runtime_checkable Proxy protocol."""
    proxy = ContextInjectionProxy(agents_md_path=str(tmp_path / "AGENTS.md"))
    assert isinstance(proxy, Proxy)


# ---------------------------------------------------------------------------
# AGENTS.md injection
# ---------------------------------------------------------------------------


async def test_context_injection_prepends_agents_md(tmp_path: Path) -> None:
    """AGENTS.md content is prepended to session/prompt content list."""
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Project Rules\n\nBe helpful.", encoding="utf-8")
    proxy = ContextInjectionProxy(agents_md_path=str(agents_md))
    params: dict[str, Any] = {
        "content": [{"type": "text", "text": "hello agent"}],
    }
    meta: dict[str, Any] = {"response": False}
    result = await proxy.proxy_successor("session/prompt", params, meta)
    content = result["content"]
    assert isinstance(content, list)
    assert len(content) == 2
    injected = content[0]
    assert injected["type"] == "text"
    assert "# Project Rules" in injected["text"]
    assert "Be helpful." in injected["text"]
    assert content[1] == {"type": "text", "text": "hello agent"}


async def test_context_injection_prepends_skill_instructions(
    tmp_path: Path,
) -> None:
    """Skill instructions are prepended alongside AGENTS.md content."""
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Rules", encoding="utf-8")
    proxy = ContextInjectionProxy(
        agents_md_path=str(agents_md),
        skill_instructions=["<skill>Use uv</skill>", "<skill>Write tests</skill>"],
    )
    params: dict[str, Any] = {
        "content": [{"type": "text", "text": "do work"}],
    }
    meta: dict[str, Any] = {"response": False}
    result = await proxy.proxy_successor("session/prompt", params, meta)
    content = result["content"]
    assert len(content) == 2
    injected_text = content[0]["text"]
    assert "# Rules" in injected_text
    assert "<skill>Use uv</skill>" in injected_text
    assert "<skill>Write tests</skill>" in injected_text


# ---------------------------------------------------------------------------
# Missing AGENTS.md
# ---------------------------------------------------------------------------


async def test_context_injection_missing_agents_md(tmp_path: Path) -> None:
    """When AGENTS.md does not exist, no injection occurs."""
    proxy = ContextInjectionProxy(
        agents_md_path=str(tmp_path / "nonexistent.md"),
    )
    params: dict[str, Any] = {
        "content": [{"type": "text", "text": "original"}],
    }
    meta: dict[str, Any] = {"response": False}
    result = await proxy.proxy_successor("session/prompt", params, meta)
    assert result["content"] == [{"type": "text", "text": "original"}]


# ---------------------------------------------------------------------------
# Non-prompt passthrough
# ---------------------------------------------------------------------------


async def test_context_injection_passthrough_non_prompt(tmp_path: Path) -> None:
    """Non-prompt methods pass through unchanged."""
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Rules", encoding="utf-8")
    proxy = ContextInjectionProxy(agents_md_path=str(agents_md))
    params: dict[str, Any] = {"sessionId": "abc123", "cwd": "/tmp"}
    meta: dict[str, Any] = {"response": False}
    result = await proxy.proxy_successor("session/new", params, meta)
    assert result is params


async def test_context_injection_passthrough_response(tmp_path: Path) -> None:
    """session/prompt responses are not injected (only requests)."""
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Rules", encoding="utf-8")
    proxy = ContextInjectionProxy(agents_md_path=str(agents_md))
    params: dict[str, Any] = {"result": {"text": "response"}}
    meta: dict[str, Any] = {"response": True}
    result = await proxy.proxy_successor("session/prompt", params, meta)
    assert result is params


async def test_context_injection_string_content(tmp_path: Path) -> None:
    """When content is a string, context is prepended as string."""
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Rules", encoding="utf-8")
    proxy = ContextInjectionProxy(agents_md_path=str(agents_md))
    params: dict[str, Any] = {"content": "hello"}
    meta: dict[str, Any] = {"response": False}
    result = await proxy.proxy_successor("session/prompt", params, meta)
    content = result["content"]
    assert isinstance(content, str)
    assert "# Rules" in content
    assert "hello" in content
