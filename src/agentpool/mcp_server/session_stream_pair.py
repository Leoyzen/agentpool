"""Per-session memory stream pair for MCP-over-ACP transport reuse.

Each ``connect_session()`` call on a shared ``AcpMcpTransport`` creates
an independent ``SessionStreamPair`` so multiple ``ClientSession``
instances can coexist without stream contention.
"""

from __future__ import annotations

from dataclasses import dataclass

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream


@dataclass
class SessionStreamPair:
    """Per-session stream pair for a single ClientSession.

    Attributes:
        to_session_send: Write end for messages going TO the MCP session
            (responses from ACP client, server notifications).
        to_session_receive: Read end consumed by ClientSession._receive_loop.
        from_session_send: Write end used by ClientSession to send requests.
        from_session_receive: Read end consumed by the forwarder task.
    """

    to_session_send: MemoryObjectSendStream
    to_session_receive: MemoryObjectReceiveStream
    from_session_send: MemoryObjectSendStream
    from_session_receive: MemoryObjectReceiveStream

    async def close(self) -> None:
        """Close all four streams."""
        for stream in [
            self.to_session_send,
            self.to_session_receive,
            self.from_session_send,
            self.from_session_receive,
        ]:
            with anyio.move_on_after(0.1):
                await stream.aclose()
