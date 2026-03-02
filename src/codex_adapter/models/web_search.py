from __future__ import annotations

from typing import Literal

from codex_adapter.models.base import CodexBaseModel


class WebSearchActionSearch(CodexBaseModel):
    """Web search action - search."""

    type: Literal["search"] = "search"
    query: str | None = None
    queries: list[str] | None = None


class WebSearchActionOpenPage(CodexBaseModel):
    """Web search action - open page."""

    type: Literal["openPage"] = "openPage"
    url: str | None = None


class WebSearchActionFindInPage(CodexBaseModel):
    """Web search action - find in page."""

    type: Literal["findInPage"] = "findInPage"
    url: str | None = None
    pattern: str | None = None


class WebSearchActionOther(CodexBaseModel):
    """Web search action - other."""

    type: Literal["other"] = "other"


WebSearchAction = (
    WebSearchActionSearch
    | WebSearchActionOpenPage
    | WebSearchActionFindInPage
    | WebSearchActionOther
)
