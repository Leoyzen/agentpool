"""Codex app-server client."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from codex_adapter.models import (
    CommandExecutionRequestApprovalParams,
    CommandExecutionRequestApprovalResponse,
    DynamicToolCallParams,
    DynamicToolCallResponse,
    FileChangeRequestApprovalParams,
    FileChangeRequestApprovalResponse,
    ToolRequestUserInputParams,
    ToolRequestUserInputResponse,
)


# Server request method constants
SERVER_REQUEST_COMMAND_APPROVAL = "item/commandExecution/requestApproval"
SERVER_REQUEST_FILE_CHANGE_APPROVAL = "item/fileChange/requestApproval"
SERVER_REQUEST_USER_INPUT = "item/tool/requestUserInput"
SERVER_REQUEST_DYNAMIC_TOOL_CALL = "item/tool/call"

# Type for server request parameter models
ServerRequestParams = (
    CommandExecutionRequestApprovalParams
    | FileChangeRequestApprovalParams
    | ToolRequestUserInputParams
    | DynamicToolCallParams
)

# Type for server request response models
ServerRequestResponse = (
    CommandExecutionRequestApprovalResponse
    | FileChangeRequestApprovalResponse
    | ToolRequestUserInputResponse
    | DynamicToolCallResponse
)

# Server request handler callback type
ServerRequestHandler = Callable[[ServerRequestParams], Awaitable[ServerRequestResponse]]

# Typed handler callbacks for each server request kind
CommandApprovalHandler = Callable[
    [CommandExecutionRequestApprovalParams],
    Awaitable[CommandExecutionRequestApprovalResponse],
]
FileChangeApprovalHandler = Callable[
    [FileChangeRequestApprovalParams],
    Awaitable[FileChangeRequestApprovalResponse],
]
UserInputHandler = Callable[[ToolRequestUserInputParams], Awaitable[ToolRequestUserInputResponse]]
DynamicToolCallHandler = Callable[[DynamicToolCallParams], Awaitable[DynamicToolCallResponse]]

# Map from wire method names to param/response model types
SERVER_REQUEST_TYPES: dict[str, tuple[type[ServerRequestParams], type[ServerRequestResponse]]] = {
    SERVER_REQUEST_COMMAND_APPROVAL: (
        CommandExecutionRequestApprovalParams,
        CommandExecutionRequestApprovalResponse,
    ),
    SERVER_REQUEST_FILE_CHANGE_APPROVAL: (
        FileChangeRequestApprovalParams,
        FileChangeRequestApprovalResponse,
    ),
    SERVER_REQUEST_USER_INPUT: (ToolRequestUserInputParams, ToolRequestUserInputResponse),
    SERVER_REQUEST_DYNAMIC_TOOL_CALL: (DynamicToolCallParams, DynamicToolCallResponse),
}


def create_auto_approve_dict() -> dict[str, ServerRequestHandler]:
    """Register default handlers that auto-approve all server requests.

    Convenience method for non-interactive use cases where all approvals
    should be automatically granted and tool calls return empty results.
    """

    async def auto_approve_command(
        _params: ServerRequestParams,
    ) -> ServerRequestResponse:
        return CommandExecutionRequestApprovalResponse(decision="allow")

    async def auto_approve_file_change(
        _params: ServerRequestParams,
    ) -> ServerRequestResponse:
        return FileChangeRequestApprovalResponse(decision="allow")

    async def auto_approve_user_input(
        _params: ServerRequestParams,
    ) -> ServerRequestResponse:
        return ToolRequestUserInputResponse(answers={})

    async def auto_approve_dynamic_tool(
        _params: ServerRequestParams,
    ) -> ServerRequestResponse:
        return DynamicToolCallResponse(content_items=[], success=False)

    dct: dict[str, ServerRequestHandler] = {}
    dct[SERVER_REQUEST_COMMAND_APPROVAL] = auto_approve_command
    dct[SERVER_REQUEST_FILE_CHANGE_APPROVAL] = auto_approve_file_change
    dct[SERVER_REQUEST_USER_INPUT] = auto_approve_user_input
    dct[SERVER_REQUEST_DYNAMIC_TOOL_CALL] = auto_approve_dynamic_tool
    return dct
