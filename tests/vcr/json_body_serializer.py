"""Custom YAML serializer for VCR cassettes.

Adapted from pydantic-ai's ``tests/json_body_serializer.py``. This serializer:

- Decompresses gzip/brotli response bodies so cassettes are human-readable.
- Normalizes smart quotes and special Unicode characters to ASCII equivalents
  so snapshot assertions remain stable across platforms.
- Scrubs credential patterns (API key prefixes like ``sk-``) from bodies.
- Stores JSON bodies as ``parsed_body`` dicts for readable cassette files.
- Filters transient headers (``cf-ray``, ``x-amz-request-id``, ``date``, etc.)
  so cassettes are deterministic across replays.

The serializer is registered with VCR via the ``pytest_recording_configure``
hook in ``tests/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
import gzip
import json
import re
from typing import TYPE_CHECKING, Any
import unicodedata
import urllib.parse
import zlib

import brotli
import yaml


# Smart quote and special character normalization.
#
# LLM APIs sometimes return smart quotes and special Unicode characters in
# responses. These are captured in cassettes, which then populate snapshots
# which in turn cause linter complaints about non-ASCII characters. Normalizing
# to ASCII equivalents ensures consistent, portable cassette files and stable
# snapshots.
SMART_CHAR_MAP: dict[str, str] = {
    "\u2018": "'",  # LEFT SINGLE QUOTATION MARK
    "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK
    "\u201c": '"',  # LEFT DOUBLE QUOTATION MARK
    "\u201d": '"',  # RIGHT DOUBLE QUOTATION MARK
    "\u2013": "-",  # EN DASH
    "\u2014": "--",  # EM DASH
    "\u2026": "...",  # HORIZONTAL ELLIPSIS
}
SMART_CHAR_TRANS: dict[int, str | None] = str.maketrans(SMART_CHAR_MAP)

# Credential patterns scrubbed from request/response bodies.
_CREDENTIAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # OpenAI / Anthropic / generic ``sk-`` API keys
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk-REDACTED"),
    # Bearer tokens in Authorization headers embedded in bodies
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"), "Bearer REDACTED"),
]

if TYPE_CHECKING:
    from yaml import Dumper, SafeLoader
else:
    try:
        from yaml import CDumper as Dumper, CSafeLoader as SafeLoader
    except ImportError:  # pragma: no cover
        from yaml import Dumper, SafeLoader

# ---------------------------------------------------------------------------
# Header filtering
# ---------------------------------------------------------------------------

# Headers stripped from cassettes entirely (lowercase match).
FILTERED_HEADERS: set[str] = {
    "authorization",
    "cookie",
    "date",
    "openai-organization",
    "openai-project",
    "request-id",
    "server",
    "user-agent",
    "via",
    "set-cookie",
    "api-key",
    "x-api-key",
}

# Headers stripped by prefix (lowercase match).
FILTERED_HEADER_PREFIXES: list[str] = ["anthropic-", "cf-", "x-amz-", "x-"]

# Headers preserved despite prefix filtering (e.g. provider-specific required headers).
ALLOWED_HEADER_PREFIXES: set[str] = set()
ALLOWED_HEADERS: set[str] = set()


# ---------------------------------------------------------------------------
# Smart character normalization
# ---------------------------------------------------------------------------


def normalize_smart_chars(text: str) -> str:
    """Normalize smart quotes and special characters to ASCII equivalents."""
    text = text.translate(SMART_CHAR_TRANS)
    return unicodedata.normalize("NFKC", text)


def normalize_body(obj: Any) -> Any:
    """Recursively normalize smart characters in all strings within a data structure."""
    if isinstance(obj, str):
        return normalize_smart_chars(obj)
    if isinstance(obj, dict):
        return {k: normalize_body(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_body(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Credential scrubbing
# ---------------------------------------------------------------------------


def scrub_credentials(text: str) -> str:
    """Scrub credential patterns from a text string."""
    for pattern, replacement in _CREDENTIAL_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def scrub_body_credentials(obj: Any) -> Any:
    """Recursively scrub credential patterns from all strings in a data structure."""
    if isinstance(obj, str):
        return scrub_credentials(obj)
    if isinstance(obj, dict):
        return {k: scrub_body_credentials(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_body_credentials(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# YAML literal block presenter
# ---------------------------------------------------------------------------


class LiteralDumper(Dumper):
    """A custom dumper that represents multi-line strings as literal blocks."""


def _str_presenter(dumper: Dumper, data: str) -> Any:
    """If the string contains newlines, represent it as a literal block."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


LiteralDumper.add_representer(str, _str_presenter)


# ---------------------------------------------------------------------------
# Content-type helpers
# ---------------------------------------------------------------------------


def _content_type_startswith(content_type: Sequence[str | bytes], prefix: str) -> bool:
    def _to_str(h: str | bytes | None) -> str:
        if isinstance(h, str):
            return h
        if isinstance(h, bytes):
            return h.decode("utf-8")
        return ""

    return any(_to_str(h).startswith(prefix) for h in content_type)


def _scrub_form_credentials(data: dict[str, Any], content_type: list[str]) -> None:
    """Redact credentials from ``application/x-www-form-urlencoded`` request bodies."""
    if not _content_type_startswith(content_type, "application/x-www-form-urlencoded"):
        return
    body = data.get("body")
    if not isinstance(body, str):
        return
    query_params = urllib.parse.parse_qs(body)
    for key in ("assertion", "client_id", "client_secret", "refresh_token"):
        if key in query_params:
            query_params[key] = ["scrubbed"]
            data["body"] = urllib.parse.urlencode(query_params, doseq=True)


# ---------------------------------------------------------------------------
# JSON body storage
# ---------------------------------------------------------------------------


def _store_json_body(
    kind: str,
    data: dict[str, Any],
    body: str,
    headers: dict[str, list[str]],
) -> None:
    """Replace an ``application/json`` body with a normalized, scrubbed ``parsed_body``.

    Some endpoints send a non-JSON body under an ``application/json`` content-type;
    keep the raw body rather than crashing the serializer.
    """
    try:
        parsed: Any = json.loads(body)
    except json.JSONDecodeError:
        data["body"] = {"string": body} if kind == "response" else body
        if "content-length" in headers:
            headers["content-length"] = [str(len(body.encode("utf-8")))]
        return

    parsed = normalize_body(parsed)
    parsed = scrub_body_credentials(parsed)

    if isinstance(parsed, dict):
        if "access_token" in parsed:
            parsed["access_token"] = "scrubbed"
        if "id_token" in parsed:
            parsed["id_token"] = "scrubbed"

    data["parsed_body"] = parsed
    del data["body"]

    # Update content-length to match the body that will be produced during deserialize.
    # Decompression changes body size, and some clients verify content-length on replay.
    if "content-length" in headers:
        new_body = json.dumps(parsed)
        headers["content-length"] = [str(len(new_body.encode("utf-8")))]


# ---------------------------------------------------------------------------
# VCR serializer entry points
# ---------------------------------------------------------------------------


def deserialize(cassette_string: str) -> Any:
    """Deserialize a cassette YAML string, expanding ``parsed_body`` back to ``body``."""
    cassette_dict: dict[str, Any] = yaml.load(cassette_string, Loader=SafeLoader)
    for interaction in cassette_dict["interactions"]:
        for kind, data in interaction.items():
            parsed_body = data.pop("parsed_body", None)
            if parsed_body is not None:
                dumped_body = json.dumps(parsed_body)
                data["body"] = {"string": dumped_body} if kind == "response" else dumped_body
    return cassette_dict


def serialize(cassette_dict: Any) -> str:
    """Serialize a cassette dict to YAML, filtering headers and storing JSON as ``parsed_body``."""
    for interaction in cassette_dict["interactions"]:
        for kind, data in interaction.items():
            headers: dict[str, list[str]] = data.get("headers", {})
            # Lowercase header keys
            headers = {k.lower(): v for k, v in headers.items()}
            # Filter by exact name
            headers = {k: v for k, v in headers.items() if k not in FILTERED_HEADERS}
            # Filter by prefix, preserving allowed headers
            headers = {
                k: v
                for k, v in headers.items()
                if not any(k.startswith(prefix) for prefix in FILTERED_HEADER_PREFIXES)
                or k in ALLOWED_HEADERS
                or any(k.startswith(prefix) for prefix in ALLOWED_HEADER_PREFIXES)
            }
            data["headers"] = headers

            content_type = headers.get("content-type", [])
            is_json = any(
                isinstance(header, str) and header.startswith("application/json")
                for header in content_type
            )
            if is_json:
                body = data.get("body")
                if body is None:
                    continue
                if isinstance(body, dict):
                    # Response bodies are under a 'string' field
                    body = body.get("string")
                if not body:
                    continue
                if isinstance(body, bytes):
                    content_encoding = headers.get("content-encoding", [])
                    # Decompress so httpx doesn't re-decompress on replay
                    if "br" in content_encoding:
                        body = brotli.decompress(body)
                        headers.pop("content-encoding", None)
                    elif "gzip" in content_encoding or (len(body) > 2 and body[:2] == b"\x1f\x8b"):
                        try:
                            body = gzip.decompress(body)
                            headers.pop("content-encoding", None)
                        except (gzip.BadGzipFile, zlib.error):
                            pass
                    body = body.decode("utf-8")
                _store_json_body(kind, data, body, headers)

            _scrub_form_credentials(data, content_type)

    return yaml.dump(cassette_dict, Dumper=LiteralDumper, allow_unicode=True, width=120)


__all__ = (
    "FILTERED_HEADERS",
    "FILTERED_HEADER_PREFIXES",
    "LiteralDumper",
    "deserialize",
    "normalize_body",
    "normalize_smart_chars",
    "scrub_body_credentials",
    "scrub_credentials",
    "serialize",
)
