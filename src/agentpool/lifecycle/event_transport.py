"""EventTransport implementation: InProcessTransport.

The EventTransport dimension abstracts the wire protocol between RunLoop
and external consumers. ``InProcessTransport`` is the default implementation
using in-process ``asyncio.Queue`` — it requires zero infrastructure and
passes Python objects directly (no serialization).

For MQ-backed or gRPC transports, see future milestones beyond M2.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.lifecycle.types import EventEnvelope


class InProcessTransport:
    """In-process EventTransport using per-topic ``asyncio.Queue``.

    Events are delivered as Python objects without serialization. An
    optional replay buffer allows late subscribers to receive previously
    published events.

    Attributes:
        _replay_buffer_size: Maximum number of events retained per topic.
            ``0`` disables the replay buffer entirely.
        _queues: Per-topic asyncio queues for delivering new events.
        _replay_buffers: Per-topic lists of retained envelopes (only
            populated when ``_replay_buffer_size > 0``).
        _closed: Whether ``close()`` has been called.
    """

    def __init__(self, replay_buffer_size: int = 0) -> None:
        """Initialize the transport.

        Args:
            replay_buffer_size: Maximum events retained per topic for
                late-subscriber replay. ``0`` disables replay (default).
        """
        self._replay_buffer_size: int = replay_buffer_size
        self._queues: dict[str, asyncio.Queue[EventEnvelope]] = {}
        self._replay_buffers: dict[str, list[EventEnvelope]] = {}
        self._closed: bool = False

    def _get_queue(self, topic: str) -> asyncio.Queue[EventEnvelope]:
        """Return the queue for *topic*, creating it if necessary.

        Args:
            topic: The topic identifier.

        Returns:
            The asyncio queue for the topic.
        """
        if topic not in self._queues:
            self._queues[topic] = asyncio.Queue()
        return self._queues[topic]

    def _get_replay_buffer(self, topic: str) -> list[EventEnvelope]:
        """Return the replay buffer list for *topic*.

        Args:
            topic: The topic identifier.

        Returns:
            The list of retained envelopes for the topic.
        """
        if topic not in self._replay_buffers:
            self._replay_buffers[topic] = []
        return self._replay_buffers[topic]

    async def publish(self, envelope: EventEnvelope) -> None:
        """Publish an envelope to the transport.

        Pushes the envelope to the per-topic queue and appends it to
        the replay buffer (if configured). The topic is derived from
        ``envelope.session_id``.

        Args:
            envelope: The envelope to publish.

        Raises:
            RuntimeError: If the transport has been closed.
        """
        if self._closed:
            msg = "Cannot publish on a closed InProcessTransport."
            raise RuntimeError(msg)

        topic = envelope.session_id
        queue = self._get_queue(topic)
        await queue.put(envelope)

        if self._replay_buffer_size > 0:
            buffer = self._get_replay_buffer(topic)
            buffer.append(envelope)
            if len(buffer) > self._replay_buffer_size:
                del buffer[0]

    def subscribe(self, topic: str, from_seq: int = 0) -> AsyncIterator[EventEnvelope]:
        """Return an async iterator of envelopes for *topic*.

        If ``from_seq > 0`` and a replay buffer exists, replayed events
        with ``seq >= from_seq`` are yielded first, then new events from
        the queue as they arrive. If no replay buffer is configured or
        ``from_seq`` is 0, only new events are yielded.

        Args:
            topic: Topic identifier (typically ``session_id``).
            from_seq: Replay from this sequence number. Events with
                ``seq >= from_seq`` are replayed first.

        Returns:
            Async iterator yielding ``EventEnvelope`` objects.

        Raises:
            RuntimeError: If the transport has been closed.
        """
        if self._closed:
            msg = "Cannot subscribe on a closed InProcessTransport."
            raise RuntimeError(msg)

        return self._iterate(topic, from_seq)

    async def _iterate(self, topic: str, from_seq: int) -> AsyncIterator[EventEnvelope]:
        """Async generator yielding replayed then new envelopes.

        Args:
            topic: Topic identifier.
            from_seq: Minimum sequence number for replay.

        Yields:
            ``EventEnvelope`` objects, replayed first then live.
        """
        if from_seq > 0 and self._replay_buffer_size > 0:
            buffer = self._replay_buffers.get(topic, [])
            for envelope in buffer:
                if envelope.seq is not None and envelope.seq >= from_seq:
                    yield envelope

        queue = self._get_queue(topic)
        while True:
            envelope = await queue.get()
            if envelope is None:  # sentinel from close()
                break
            yield envelope
            queue.task_done()

    def ack(self, seq: int) -> None:
        """Acknowledge that an event has been processed.

        For in-process transport, this is a no-op. MQ-backed transports
        would commit the consumer offset.

        Args:
            seq: The sequence number to acknowledge.
        """

    def close(self) -> None:
        """Release all transport resources.

        Sets ``_closed = True`` and puts a ``None`` sentinel on each
        per-topic queue to wake blocked consumers. Subsequent calls to
        ``publish()`` or ``subscribe()`` raise ``RuntimeError``.
        """
        self._closed = True
        for queue in self._queues.values():
            queue.put_nowait(None)  # type: ignore[arg-type]  # sentinel from close()


__all__ = ["InProcessTransport"]
