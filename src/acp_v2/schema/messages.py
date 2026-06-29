"""ACP v2 method name enums.

v2 changes from v1:
- auth/login replaces authenticate
- auth/logout replaces logout (now required, no capability marker)
"""

from __future__ import annotations

from enum import Enum


class ClientMethod(str, Enum):
    """Methods the client can call on the agent."""

    INITIALIZE = "initialize"
    AUTH_LOGIN = "auth/login"
    AUTH_LOGOUT = "auth/logout"
    SESSION_NEW = "session/new"
    SESSION_LOAD = "session/load"
    SESSION_LIST = "session/list"
    SESSION_PROMPT = "session/prompt"
    SESSION_CANCEL = "session/cancel"
    SESSION_CLOSE = "session/close"
    SESSION_FORK = "session/fork"
    SESSION_RESUME = "session/resume"
    SESSION_DELETE = "session/delete"
    SESSION_SET_CONFIG_OPTION = "session/set_config_option"
    PROVIDERS_LIST = "providers/list"
    PROVIDERS_SET = "providers/set"
    PROVIDERS_DISABLE = "providers/disable"


class AgentMethod(str, Enum):
    """Methods the agent can call on the client."""

    SESSION_UPDATE = "session/update"
    SESSION_REQUEST_PERMISSION = "session/request_permission"
    ELICITATION_CREATE = "elicitation/create"
    ELICITATION_COMPLETE = "elicitation/complete"
