"""Stub dataclasses for HostContext future expansion.

These are intentionally empty placeholders that will be filled in
later waves with model capability caching, provider registry, and
model instance caching logic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CapabilityCache:
    """Placeholder for future model capability caching."""


@dataclass
class ModelRegistry:
    """Placeholder for future model provider registry."""


@dataclass
class ModelCache:
    """Placeholder for future model instance caching."""
