"""LSP (Language Server Protocol) routes.

Provides endpoints for LSP server status and diagnostics,
compatible with OpenCode's LSP API.
"""

from __future__ import annotations

from contextlib import suppress
import os

from fastapi import APIRouter

from agentpool_server.opencode_server.dependencies import StateDep
from opencode_sdk.models import FormatterStatus, LspStatus


router = APIRouter(tags=["lsp"])


@router.get("/lsp")
async def list_lsp_servers(state: StateDep) -> list[LspStatus]:
    """List all active LSP servers.

    Returns the status of all running LSP servers, including their
    connection state and workspace root.

    Returns:
        List of LSP server status objects.
    """
    servers: list[LspStatus] = []
    for server_id, server_state in state.lsp_manager._servers.items():
        # Get relative root path
        root_uri = server_state.root_uri or ""
        if root_uri.startswith("file://"):
            root_path = root_uri[7:]  # Remove file:// prefix
            # Make path relative to working directory
            with suppress(ValueError):
                root_path = os.path.relpath(root_path, state.working_dir)
        else:
            root_path = root_uri
        status = "connected" if server_state.initialized else "error"
        servers.append(LspStatus(id=server_id, name=server_id, root=root_path, status=status))
    return servers


# NOTE: The following routes are agentpool extensions that don't exist in upstream OpenCode.
# Commented out until we confirm they're needed.

# @router.post("/lsp/start")
# async def start_lsp_server(
#     state: StateDep,
#     server_id: str = Query(..., description="LSP server ID (e.g., 'pyright', 'rust-analyzer')"),
#     root_uri: str | None = Query(None, description="Workspace root URI"),
# ) -> LspStatus:
#     """Start an LSP server."""
#     from fastapi import HTTPException
#     from opencode_sdk.models import LspUpdatedEvent
#
#     if root_uri is None:
#         root_uri = f"file://{state.working_dir}"
#
#     try:
#         server_state = await state.lsp_manager.start_server(server_id, root_uri)
#     except ValueError as e:
#         raise HTTPException(status_code=404, detail=str(e)) from e
#     except RuntimeError as e:
#         raise HTTPException(status_code=500, detail=str(e)) from e
#
#     await state.broadcast_event(LspUpdatedEvent())
#     root_path = root_uri
#     if root_uri.startswith("file://"):
#         root_path = root_uri[7:]
#         with suppress(ValueError):
#             root_path = os.path.relpath(root_path, state.working_dir)
#     status = "connected" if server_state.initialized else "error"
#     return LspStatus(id=server_id, name=server_id, root=root_path, status=status)


# @router.post("/lsp/stop")
# async def stop_lsp_server(
#     state: StateDep,
#     server_id: str = Query(..., description="LSP server ID to stop"),
# ) -> dict[str, str]:
#     """Stop an LSP server."""
#     from opencode_sdk.models import LspUpdatedEvent
#
#     await state.lsp_manager.stop_server(server_id)
#     await state.broadcast_event(LspUpdatedEvent())
#     return {"status": "ok", "message": f"Server {server_id} stopped"}


# @router.get("/lsp/diagnostics")
# async def get_diagnostics(
#     state: StateDep,
#     path: str | None = Query(None, description="File path to get diagnostics for"),
# ) -> dict[str, list[Diagnostic]]:
#     """Get diagnostics from all active LSP servers."""
#     from opencode_sdk.models import Diagnostic, DiagnosticRange
#     from opencode_sdk.models.diagnostics import SeverityLevel
#
#     def _severity_to_lsp(severity: str) -> SeverityLevel:
#         mapping: dict[str, SeverityLevel] = {"error": 1, "warning": 2, "info": 3, "hint": 4}
#         return mapping.get(severity.lower(), 1)
#
#     results: dict[str, list[Diagnostic]] = {}
#     if path:
#         if not os.path.isabs(path):
#             path = os.path.join(state.working_dir, path)
#         server_info = state.lsp_manager.get_server_for_file(path)
#         if server_info and server_info.has_cli_diagnostics:
#             try:
#                 result = await state.lsp_manager.run_cli_diagnostics(server_info.id, [path])
#                 if result.success and result.diagnostics:
#                     for diag in result.diagnostics:
#                         file_path = diag.file or path
#                         if file_path not in results:
#                             results[file_path] = []
#                         rng = DiagnosticRange.create(
#                             start_line=max(0, diag.line - 1),
#                             start_char=max(0, diag.column - 1),
#                             end_line=max(0, (diag.end_line or diag.line) - 1),
#                             end_char=max(0, (diag.end_column or diag.column) - 1),
#                         )
#                         results[file_path].append(
#                             Diagnostic(
#                                 range=rng,
#                                 message=diag.message,
#                                 severity=_severity_to_lsp(diag.severity),
#                                 code=diag.code,
#                                 source=diag.source or server_info.id,
#                             )
#                         )
#             except Exception:
#                 pass
#     return results


# @router.get("/lsp/servers")
# async def list_available_servers(state: StateDep) -> list[dict[str, object]]:
#     """List all registered (available) LSP servers."""
#     return [
#         {
#             "id": server_id,
#             "extensions": config.extensions,
#             "running": server_id in state.lsp_manager._servers,
#         }
#         for server_id, config in state.lsp_manager._server_configs.items()
#     ]


# =============================================================================
# Formatter Routes
# =============================================================================


@router.get("/formatter")
async def list_formatters(state: StateDep) -> list[FormatterStatus]:
    """List all active formatters.

    Returns the status of all running formatters, including their
    connection state and workspace root.

    Note: This is currently a stub that returns an empty list.
    Formatter support can be added in the future.

    Returns:
        List of formatter status objects.
    """
    _ = state  # Reserved for future use
    return []
