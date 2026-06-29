"""ACP protocol version negotiator.

Routes incoming connections to v1 or v2 protocol paths based on the
``protocolVersion`` field in the ``initialize`` request.

Reference: ACP v2 RFD — initialize negotiation semantics.
"""

from __future__ import annotations

from typing import Literal

from acp.exceptions import RequestError


ProtocolVersion = Literal[1, 2]

_SUPPORTED_VERSIONS: frozenset[int] = frozenset({1, 2})


class VersionNegotiator:
    """Negotiate ACP protocol version from client's initialize request.

    The negotiator picks the highest mutually supported version.
    v1 clients always get v1; v2+ clients get v2.

    Example:
        >>> negotiator = VersionNegotiator()
        >>> negotiator.negotiate(1)
        1
        >>> negotiator.negotiate(2)
        2
        >>> negotiator.negotiate(0)
        Traceback (most recent call last):
            ...
        acp.exceptions.RequestError: Unsupported protocol version: 0
    """

    @staticmethod
    def negotiate(requested: int) -> ProtocolVersion:
        """Return the negotiated protocol version.

        Args:
            requested: The ``protocolVersion`` from the client's
                ``initialize`` request.

        Returns:
            The negotiated version (1 or 2).

        Raises:
            RequestError: If the requested version is not supported.
        """
        if requested == 1:
            return 1
        if requested >= 2:
            return 2
        msg = f"Unsupported protocol version: {requested}"
        raise RequestError(-32602, msg, {"protocolVersion": requested})

    @staticmethod
    def is_supported(version: int) -> bool:
        """Check if a protocol version is supported.

        Args:
            version: The protocol version to check.

        Returns:
            True if the version is supported, False otherwise.
        """
        return version in _SUPPORTED_VERSIONS
