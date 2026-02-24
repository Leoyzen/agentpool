"""Data models for ACP registry entries."""

from __future__ import annotations

from abc import ABC, abstractmethod
from functools import cached_property
from pathlib import Path
import platform
from typing import Any

from pydantic import BaseModel, Field


def get_platform_key() -> str:
    """Return a ``{system}-{arch}`` key for the current platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    match machine:
        case "x86_64" | "amd64":
            arch = "x86_64"
        case "arm64" | "aarch64":
            arch = "aarch64"
        case _:
            arch = machine

    return f"{system}-{arch}"


class BaseDistribution(BaseModel, ABC):
    """Base class for agent distribution methods."""

    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    @abstractmethod
    def format_cmd(self) -> str: ...

    @abstractmethod
    def format_args(self) -> tuple[str, ...]: ...


class NpxDistribution(BaseDistribution):
    """Distribution via npx / bunx."""

    package: str

    def format_cmd(self) -> str:
        return "bunx"

    def format_args(self) -> tuple[str, ...]:
        return (self.package, *self.args)


class UvxDistribution(BaseDistribution):
    """Distribution via uvx."""

    package: str

    def format_cmd(self) -> str:
        return "uvx"

    def format_args(self) -> tuple[str, ...]:
        return ("--python", "3.13", self.package, *self.args)


class BinaryDistribution(BaseDistribution):
    """Distribution via a pre-built binary archive."""

    archive: str
    cmd: str

    def format_cmd(self) -> str:
        return Path(self.cmd).name

    def format_args(self) -> tuple[str, ...]:
        return (*self.args,)


Distribution = NpxDistribution | UvxDistribution | BinaryDistribution


class DistributionUnion(BaseModel):
    """Container that holds the possible distribution variants for an agent."""

    npx: NpxDistribution | None = None
    uvx: UvxDistribution | None = None
    binary: dict[str, BinaryDistribution] | None = None


class RegistryAgent(BaseModel):
    """A single agent entry in the ACP registry."""

    id: str
    name: str
    version: str
    description: str
    repository: str | None = None
    authors: list[str] = Field(default_factory=list)
    license: str
    icon: str | None = None
    distribution: DistributionUnion

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"

    @cached_property
    def dist(self) -> Distribution:
        """Resolve the best distribution for the current platform."""
        match self.distribution:
            case DistributionUnion(npx=NpxDistribution() as npx):
                return npx
            case DistributionUnion(uvx=UvxDistribution() as uvx):
                return uvx
            case DistributionUnion(binary=dict() as binaries):
                platform_key = get_platform_key()
                binary_distro: BinaryDistribution | None = binaries.get(platform_key)
                if binary_distro is not None:
                    return binary_distro
                raise ValueError(f"No binary distribution found for platform {platform_key!r}")
            case _:
                raise ValueError("Unsupported distribution type.")


class Registry(BaseModel):
    """Top-level ACP registry response."""

    version: str
    agents: list[RegistryAgent]
    extensions: list[Any] = Field(default_factory=list)
