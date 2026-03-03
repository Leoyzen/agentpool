"""Binary archive extraction utilities."""

from __future__ import annotations

from pathlib import Path
import shutil
import tarfile
import zipfile


def _extract_from_zip(archive_path: Path, binary_name: str, dest_path: Path) -> Path:
    """Extract a binary from a ZIP archive."""
    with zipfile.ZipFile(archive_path, "r") as zip_ref:
        for member in zip_ref.infolist():
            if member.is_dir():
                continue
            if Path(member.filename).name != binary_name:
                continue
            with zip_ref.open(member, "r") as source, dest_path.open("wb") as target:
                shutil.copyfileobj(source, target)
            return dest_path
    raise FileNotFoundError(f"Binary {binary_name!r} not found in ZIP archive {archive_path!r}")


def _extract_from_tar(archive_path: Path, binary_name: str, dest_path: Path) -> Path:
    """Extract a binary from a TAR archive."""
    with tarfile.open(archive_path, "r:*") as tar_ref:
        for member in tar_ref.getmembers():
            if not member.isfile():
                continue
            if Path(member.name).name != binary_name:
                continue
            source = tar_ref.extractfile(member)
            if source is None:
                continue
            with source, dest_path.open("wb") as target:
                shutil.copyfileobj(source, target)
            return dest_path
    raise FileNotFoundError(f"Binary {binary_name!r} not found in TAR archive {archive_path!r}")


def extract_binary(archive_path: Path, binary_name: str, dest_dir: Path) -> Path:
    """Extract a named binary from an archive, or copy it if not an archive.

    Supports ZIP and TAR (gz/bz2/xz) archives. If *archive_path* is not a
    recognised archive format the file is copied directly to *dest_dir*.

    Returns the path to the extracted/copied binary.
    """
    dest_path = dest_dir / binary_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    if zipfile.is_zipfile(archive_path):
        return _extract_from_zip(archive_path, binary_name, dest_path)

    if tarfile.is_tarfile(archive_path):
        return _extract_from_tar(archive_path, binary_name, dest_path)

    # Not an archive — copy directly
    shutil.copy(archive_path, dest_path)
    return dest_path
