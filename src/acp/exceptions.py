"""ACP-related exceptions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self


if TYPE_CHECKING:
    from acp.schema.common import AuthMethod


class RequestError(Exception):
    """Raised when a JSON-RPC request fails."""

    def __init__(
        self,
        code: int,
        message: str,
        data: Any | None = None,
        auth_methods: list[AuthMethod] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data
        self.auth_methods: list[AuthMethod] = auth_methods or []

    @classmethod
    def parse_error(cls, data: dict[str, Any] | None = None) -> Self:
        return cls(-32700, "Parse error", data)

    @classmethod
    def invalid_request(cls, data: dict[str, Any] | None = None) -> Self:
        return cls(-32600, "Invalid request", data)

    @classmethod
    def method_not_found(cls, method: str) -> Self:
        return cls(-32601, "Method not found", {"method": method})

    @classmethod
    def invalid_params(cls, data: dict[str, Any] | None = None) -> Self:
        return cls(-32602, "Invalid params", data)

    @classmethod
    def internal_error(cls, data: dict[str, Any] | None = None) -> Self:
        return cls(-32603, "Internal error", data)

    @classmethod
    def resource_not_found(cls, uri: str | None = None) -> Self:
        data = {"uri": uri} if uri is not None else None
        return cls(-32002, "Resource not found", data)

    @classmethod
    def auth_required(
        cls,
        data: dict[str, Any] | None = None,
        auth_methods: list[AuthMethod] | None = None,
    ) -> Self:
        return cls(-32000, "Authentication required", data, auth_methods=auth_methods)

    def to_error_obj(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": str(self), "data": self.data}
        if self.auth_methods:
            from acp.schema.common import (
                AuthMethodAgent,
                AuthMethodEnvVar,
                AuthMethodTerminal,
            )

            result["authMethods"] = [
                m.model_dump(mode="json", by_alias=True, exclude_none=True)
                for m in self.auth_methods
                if isinstance(m, AuthMethodAgent | AuthMethodEnvVar | AuthMethodTerminal)
            ]
        return result
