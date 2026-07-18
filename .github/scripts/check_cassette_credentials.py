#!/usr/bin/env python3
"""Scan VCR cassettes for leaked credentials in HTTP headers.

This script scans all ``.yaml`` and ``.yml`` files under
``tests/cassettes/`` for ``authorization`` (and similar) header values
that have not been redacted. A cassette is considered clean when every
authorization-style header value is either absent or matches the
``REDACTED`` placeholder pattern used by the VCR recording configuration.

Exit codes:
    0 — all cassettes are clean (or no cassettes exist yet)
    1 — one or more cassettes contain un-redacted credentials

Intended for use as a CI hygiene step and as a pre-commit hook.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple

# Headers whose values must be redacted in cassettes.
SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "x-api-key",
        "api-key",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-auth-token",
        "x-openai-api-key",
        "anthropic-api-key",
    }
)

# Values that are considered safe (already redacted placeholders).
# Matches: REDACTED, <REDACTED>, REDACTED-..., DUMMY, test-*, fake-*, etc.
REDACTED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*<?\s*REDACTED", re.IGNORECASE),
    re.compile(r"^\s*DUMMY\b", re.IGNORECASE),
    re.compile(r"^\s*FAKE\b", re.IGNORECASE),
    re.compile(r"^\s*test[-_]?", re.IGNORECASE),
    re.compile(r"^\s*example[-_]?", re.IGNORECASE),
    re.compile(r"^\s*placeholder\b", re.IGNORECASE),
    re.compile(r"^\s*$"),  # empty / whitespace-only
)

# Matches YAML header keys like "authorization:" or "Authorization:".
HEADER_KEY_RE = re.compile(
    r"^[ \t-]*([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.+?)\s*$",
    re.MULTILINE,
)


class Finding(NamedTuple):
    """A single un-redacted credential finding."""

    file: Path
    line_number: int
    header: str
    value: str


def is_redacted(value: str) -> bool:
    """Return True if ``value`` looks like a redacted placeholder."""
    return any(pattern.match(value) for pattern in REDACTED_PATTERNS)


def scan_file(path: Path) -> list[Finding]:
    """Scan a single cassette file for un-redacted credentials.

    Args:
        path: Path to the cassette ``.yaml`` file.

    Returns:
        List of findings (empty if clean).
    """
    findings: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"WARNING: could not read {path}: {exc}", file=sys.stderr)
        return findings

    for match in HEADER_KEY_RE.finditer(text):
        header_name = match.group(1)
        if header_name.lower() not in SENSITIVE_HEADERS:
            continue
        value = match.group(2)
        if is_redacted(value):
            continue
        line_number = text.count("\n", 0, match.start()) + 1
        findings.append(Finding(path, line_number, header_name, value))
    return findings


def main() -> int:
    """Scan all cassettes and report findings.

    Returns:
        0 if clean, 1 if any un-redacted credentials found.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    cassettes_dir = repo_root / "tests" / "cassettes"

    if not cassettes_dir.is_dir():
        print(f"INFO: {cassettes_dir} does not exist — nothing to scan.")
        return 0

    cassette_files = sorted(
        [*cassettes_dir.rglob("*.yaml"), *cassettes_dir.rglob("*.yml")]
    )
    if not cassette_files:
        print(f"INFO: no cassette files found in {cassettes_dir}.")
        return 0

    all_findings: list[Finding] = []
    for cassette in cassette_files:
        all_findings.extend(scan_file(cassette))

    if not all_findings:
        print(f"OK: scanned {len(cassette_files)} cassette(s) — no leaked credentials.")
        return 0

    print(
        f"ERROR: found {len(all_findings)} un-redacted credential(s) "
        f"in {len({f.file for f in all_findings})} cassette file(s):",
        file=sys.stderr,
    )
    for finding in all_findings:
        rel = finding.file.relative_to(repo_root)
        print(
            f"  {rel}:{finding.line_number}: {finding.header}: "
            f"{finding.value[:40]}{'...' if len(finding.value) > 40 else ''}",
            file=sys.stderr,
        )
    print(
        "\nFix: ensure VCR config filters these headers before recording:\n"
        "  filter_headers=['authorization', 'x-api-key', 'cookie', 'set-cookie']",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
