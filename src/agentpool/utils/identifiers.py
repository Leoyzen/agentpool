"""Identifier generation utilities.

Generates IDs that are lexicographically sortable by creation time.
Format: {prefix}_{hex_timestamp}{random_base62}

The timestamp portion uses 8 bytes (64 bits) encoded as 16 hex chars,
combining ``timestamp_ms * 0x1000 + counter`` (fixes C1: float truncation
and 48-bit overflow for 2025+ timestamps).
"""

from __future__ import annotations

import secrets
from typing import Literal

from agentpool.utils.time_utils import now_ms


PrefixType = Literal["session", "message", "permission", "user", "part", "pty", "call"]

PREFIXES: dict[PrefixType, str] = {
    "session": "ses",
    "message": "msg",
    "permission": "per",
    "user": "usr",
    "part": "prt",
    "pty": "pty",
    "call": "cal",
}

BASE62_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
ID_LENGTH = 30  # Characters after prefix (16 hex + 14 base62)

# State for monotonic ID generation
_last_timestamp = 0
_counter = 0


def _random_base62(length: int) -> str:
    """Generate random base62 string."""
    return "".join(secrets.choice(BASE62_CHARS) for _ in range(length))


def ascending(prefix: PrefixType, given: str | None = None) -> str:
    """Generate an ascending (chronologically sortable) ID.

    Args:
        prefix: The type prefix for the ID
        given: If provided, validate and return this ID instead of generating

    Returns:
        A sortable ID with the format {prefix}_{hex_timestamp}{random}

    Raises:
        ValueError: If given ID doesn't start with expected prefix
    """
    if given is not None:
        expected_prefix = PREFIXES[prefix]
        if not given.startswith(expected_prefix):
            msg = f"ID {given} does not start with {expected_prefix}"
            raise ValueError(msg)
        return given

    return _create(prefix, descending=False)


def descending(prefix: PrefixType) -> str:
    """Generate a descending (reverse chronologically sortable) ID.

    Args:
        prefix: The type prefix for the ID

    Returns:
        A reverse-sortable ID
    """
    return _create(prefix, descending=True)


def _create(prefix: PrefixType, *, descending: bool = False) -> str:
    """Create a new ID with timestamp encoding.

    Args:
        prefix: The type prefix
        descending: If True, invert the timestamp for reverse sorting

    Returns:
        A new ID string
    """
    global _last_timestamp, _counter  # noqa: PLW0603

    current_timestamp = now_ms()  # milliseconds, integer (no float truncation)

    if current_timestamp != _last_timestamp:
        _last_timestamp = current_timestamp
        _counter = 0
    _counter += 1

    # Combine timestamp and counter
    now = current_timestamp * 0x1000 + _counter

    if descending:
        now = ~now & 0xFFFFFFFFFFFFFFFF  # Invert for descending order (64 bits)

    # Encode as 8 bytes (64 bits), big-endian
    time_bytes = now.to_bytes(8, "big")

    time_hex = time_bytes.hex()

    # Add random suffix (14 chars for 30 total after prefix)
    random_suffix = _random_base62(ID_LENGTH - 16)

    return f"{PREFIXES[prefix]}_{time_hex}{random_suffix}"


def generate_session_id() -> str:
    """Generate a unique, chronologically sortable session ID.

    Convenience function for the common case.

    Returns:
        A session ID like 'ses_00000663513f9001ZHcn6VSpkaBcHi'
    """
    return ascending("session")
