"""Cassette verification utilities for VCR cassette wire-level assertions.

Adapted from pydantic-ai's ``tests/cassette_utils.py``. ``CassetteContext``
reads a cassette YAML file from disk and provides methods for asserting on
the wire-level request/response bodies recorded in the cassette.

This is used by VCR tests that need to verify the exact bytes/JSON sent to
the model API (e.g. that a specific tool schema survived translation, or
that a streaming chunk ordering matches expectations).

Example:
    ```python
    def test_request_body(vcr_cassette_dir):
        ctx = CassetteContext(
            test_name="test_basic_completion",
            test_module="test_native_basic",
            cassettes_dir=vcr_cassette_dir,
        )
        ctx.verify_contains("model", "messages")
        ctx.verify_ordering('"role":"user"', '"role":"assistant"')
        body = ctx.get_request_body(0)
        assert body["model"] == "gpt-4o-mini"
    ```
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from pathlib import Path

    from vcr.cassette import Cassette


def _sanitize_cassette_filename(name: str, max_length: int = 240) -> str:
    """Sanitize a test name into a filesystem-safe cassette filename."""
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", name)
    return sanitized[:max_length]


def _pattern_in_bodies(pattern: str, bodies: list[str]) -> bool:
    """Check if a pattern appears in any body string."""
    return any(pattern in body for body in bodies)


def _get_cassette_bodies_from_yaml(path: Path, kind: str = "request") -> list[str]:
    """Read request or response bodies from a VCR cassette YAML file on disk.

    Args:
        path: Path to the cassette ``.yaml`` file.
        kind: Either ``"request"`` or ``"response"``.
    """
    import yaml

    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    bodies: list[str] = []
    for interaction in data.get("interactions", []):
        entry = interaction.get(kind, {})
        parsed_body = entry.get("parsed_body")
        if parsed_body is None:
            body = entry.get("body")
            if body is None:
                continue
            if isinstance(body, dict):
                # Response bodies may be under a 'string' field
                body = body.get("string", body)
            if isinstance(body, (dict, list)):
                bodies.append(json.dumps(body))
            elif isinstance(body, str) and body:
                bodies.append(body)
        elif isinstance(parsed_body, (dict, list)):
            bodies.append(json.dumps(parsed_body))
        elif isinstance(parsed_body, str) and parsed_body:
            bodies.append(parsed_body)
    return bodies


def _get_cassette_body_by_index_from_yaml(
    path: Path,
    index: int,
    kind: str,
) -> Any:
    """Return the parsed body at ``index`` from a cassette YAML file.

    Returns:
        Parsed JSON body (dict/list/str), or the raw body if not JSON.
    """
    import yaml

    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    interactions = data.get("interactions", [])
    if index >= len(interactions):
        raise IndexError(
            f"Cassette {path} has {len(interactions)} interactions; "
            f"cannot get {kind} body at index {index}",
        )
    entry = interactions[index].get(kind, {})
    parsed_body = entry.get("parsed_body")
    if parsed_body is not None:
        return parsed_body
    body = entry.get("body")
    if isinstance(body, dict):
        body = body.get("string", body)
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body
    return body


def _vcr_cassette_request_bodies(cassette: Cassette) -> list[str]:
    """Get all request bodies from a live VCR cassette object as strings."""
    bodies: list[str] = []
    for request in cassette.requests:  # type: ignore[attr-defined]
        raw_body = request.body  # type: ignore[union-attr]
        if raw_body:
            if isinstance(raw_body, bytes):
                body = raw_body.decode("utf-8", errors="ignore")
            else:
                body = str(raw_body)
            bodies.append(body)
    return bodies


def _vcr_cassette_response_bodies(cassette: Cassette) -> list[str]:
    """Get all response bodies from a live VCR cassette object as strings."""
    bodies: list[str] = []
    for response in cassette.responses:  # type: ignore[attr-defined]
        body_field = response.get("body")
        raw_body = body_field.get("string") if isinstance(body_field, dict) else body_field
        if raw_body is None:
            continue
        if isinstance(raw_body, bytes):
            body = raw_body.decode("utf-8", errors="ignore")
        else:
            body = str(raw_body)
        bodies.append(body)
    return bodies


@dataclass
class CassetteContext:
    """Wire-level cassette verification context.

    Reads a cassette YAML file from disk and provides methods for asserting
    on the recorded request/response bodies. Useful for VCR tests that need
    to verify the exact wire payload (e.g. tool schema compliance, streaming
    chunk ordering) beyond what the agent-level API exposes.

    Attributes:
        test_name: The test function name (determines cassette filename).
        test_module: The test module name without ``.py`` (determines subdirectory).
        cassettes_dir: Root cassettes directory (e.g. ``tests/cassettes/vcr``).
        vcr: Optional live VCR cassette object. When provided, bodies are read
            from the in-memory cassette; otherwise the YAML file on disk is used.
    """

    test_name: str
    test_module: str
    cassettes_dir: Path
    vcr: Cassette | None = None

    def _cassette_path(self) -> Path:
        """Return the on-disk cassette YAML path."""
        filename = _sanitize_cassette_filename(self.test_name)
        return self.cassettes_dir / self.test_module / f"{filename}.yaml"

    def _get_request_bodies(self) -> list[str]:
        """Get all request bodies from the cassette."""
        if self.vcr is not None:
            bodies = _vcr_cassette_request_bodies(self.vcr)
            if bodies:
                return bodies
        path = self._cassette_path()
        if path.exists():
            return _get_cassette_bodies_from_yaml(path, kind="request")
        return []

    def _get_response_bodies(self) -> list[str]:
        """Get all response bodies from the cassette."""
        if self.vcr is not None:
            bodies = _vcr_cassette_response_bodies(self.vcr)
            if bodies:
                return bodies
        path = self._cassette_path()
        if path.exists():
            return _get_cassette_bodies_from_yaml(path, kind="response")
        return []

    def _get_all_bodies(self) -> list[str]:
        """Get all request and response bodies interleaved."""
        return self._get_request_bodies() + self._get_response_bodies()

    def verify_contains(self, *patterns: str | tuple[str, ...]) -> None:
        """Verify that all patterns appear in cassette request or response bodies.

        Args:
            patterns: Patterns to search for. Each pattern can be a string (must
                appear verbatim) or a tuple (any one of the tuple elements
                matching is sufficient).

        Raises:
            AssertionError: If a pattern is not found.
        """
        bodies = self._get_all_bodies()
        if not bodies:
            return
        for pattern in patterns:
            if isinstance(pattern, tuple):
                assert any(_pattern_in_bodies(p, bodies) for p in pattern), (
                    f"Expected one of {pattern} in cassette but none found"
                )
            else:
                assert _pattern_in_bodies(pattern, bodies), (
                    f'Expected "{pattern}" in cassette but not found'
                )

    def verify_ordering(self, *patterns: str | tuple[str, ...]) -> None:
        """Verify that patterns appear in cassette bodies in the given order.

        Args:
            patterns: Patterns that must appear in order. Each pattern can be a
                string or a tuple (any one of the tuple elements is used for
                position checking).

        Raises:
            AssertionError: If ordering is violated or a pattern is not found.
        """
        bodies = self._get_all_bodies()
        if not bodies:
            return
        content = "".join(bodies)
        last_index = -1
        for pattern in patterns:
            if isinstance(pattern, tuple):
                indices = [content.find(p) for p in pattern]
                valid_indices = [i for i in indices if i != -1]
                assert valid_indices, f"Expected one of {pattern} in cassette but none found"
                current_index = min(valid_indices)
            else:
                current_index = content.find(pattern)
                assert current_index != -1, f'Expected "{pattern}" in cassette but not found'
            assert current_index > last_index, (
                f'Pattern "{pattern}" found at index {current_index}, '
                f"but expected after index {last_index} (ordering violation)"
            )
            last_index = current_index

    def get_request_body(self, index: int = 0) -> Any:
        """Return the parsed request body at ``index`` from the cassette.

        Args:
            index: Zero-based interaction index.

        Returns:
            Parsed JSON body (dict/list), or the raw body string if not JSON.

        Raises:
            IndexError: If ``index`` is out of range.
            FileNotFoundError: If the cassette YAML file does not exist.
        """
        path = self._cassette_path()
        if not path.exists():
            raise FileNotFoundError(f"Cassette not found: {path}")
        return _get_cassette_body_by_index_from_yaml(path, index, kind="request")

    def get_response_body(self, index: int = 0) -> Any:
        """Return the parsed response body at ``index`` from the cassette.

        Args:
            index: Zero-based interaction index.

        Returns:
            Parsed JSON body (dict/list), or the raw body string if not JSON.

        Raises:
            IndexError: If ``index`` is out of range.
            FileNotFoundError: If the cassette YAML file does not exist.
        """
        path = self._cassette_path()
        if not path.exists():
            raise FileNotFoundError(f"Cassette not found: {path}")
        return _get_cassette_body_by_index_from_yaml(path, index, kind="response")


__all__ = ("CassetteContext",)
