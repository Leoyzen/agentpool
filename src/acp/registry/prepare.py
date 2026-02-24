"""Resolve a registry agent to a runnable command.

For uvx/npx distributions the command is built directly.
For binary distributions the archive is downloaded and extracted if needed.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import shutil
import tempfile
from typing import TYPE_CHECKING

import httpx

from acp.registry.archive import extract_binary
from acp.registry.model import (
    BinaryDistribution,
    NpxDistribution,
    UvxDistribution,
)


if TYPE_CHECKING:
    from acp.registry.model import RegistryAgent


logger = logging.getLogger(__name__)

DEFAULT_BIN_DIR = Path.home() / ".local" / "bin"


def _find_program(*candidates: str) -> str | None:
    """Return the first candidate that is found on PATH, or ``None``."""
    for name in candidates:
        if shutil.which(name):
            return name
    return None


def _prepare_npx(dist: NpxDistribution, extra_args: list[str]) -> list[str]:
    """Build command list for an npx/bunx distribution."""
    runner = _find_program("bunx", "npx")
    if runner is None:
        msg = "Neither bunx nor npx found on PATH. Install bun or node."
        raise RuntimeError(msg)
    base = [runner]
    if runner == "npx":
        base.append("-y")
    return [*base, dist.package, *dist.args, *extra_args]


def _prepare_uvx(dist: UvxDistribution, extra_args: list[str]) -> list[str]:
    """Build command list for a uvx distribution."""
    if not shutil.which("uvx"):
        msg = "uvx not found on PATH. Install uv."
        raise RuntimeError(msg)
    return ["uvx", "--python", "3.13", dist.package, *dist.args, *extra_args]


async def _prepare_binary(
    dist: BinaryDistribution,
    extra_args: list[str],
    bin_dir: Path,
) -> list[str]:
    """Download + extract a binary distribution if not already cached."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    bin_path = bin_dir / Path(dist.cmd).name
    cmd = [str(bin_path), *dist.args, *extra_args]

    if bin_path.exists():
        logger.info("Binary already cached at %s", bin_path)
        return cmd

    logger.info("Downloading binary from %s", dist.archive)
    with tempfile.NamedTemporaryFile(delete=True) as tmp:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0),
            follow_redirects=True,
        ) as client:
            response = await client.get(dist.archive)
            response.raise_for_status()
            tmp.write(response.content)
            tmp.flush()

        await asyncio.to_thread(
            extract_binary,
            archive_path=Path(tmp.name),
            binary_name=Path(dist.cmd).name,
            dest_dir=bin_dir,
        )

    bin_path.chmod(0o755)
    logger.info("Binary installed to %s", bin_path)
    return cmd


async def prepare_agent(
    agent: RegistryAgent,
    extra_args: list[str] | None = None,
    *,
    bin_dir: Path = DEFAULT_BIN_DIR,
) -> list[str]:
    """Resolve a registry agent to a runnable command list.

    For uvx/npx this just builds the command. For binary distributions
    this downloads and extracts the binary if not already present.

    Returns:
        A command list suitable for ``subprocess.Popen`` / ``anyio.open_process``.
    """
    args = extra_args or []
    dist = agent.dist
    match dist:
        case NpxDistribution():
            return _prepare_npx(dist, args)
        case UvxDistribution():
            return _prepare_uvx(dist, args)
        case BinaryDistribution():
            return await _prepare_binary(dist, args, bin_dir)
