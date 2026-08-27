"""Validate the exact wheel and sdist before any release publication.

Portable member identity applies Unicode NFKC normalization, Unicode casefolding,
and final NFKC stabilization to every path segment. Case-distinct archive paths are
therefore intentionally rejected because the bundle must be safe on insensitive
filesystems.
"""

from __future__ import annotations

import argparse
import re
import stat
import sys
import tarfile
import unicodedata
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

MAX_METADATA_BYTES = 64 * 1024
MAX_ARCHIVE_MEMBERS = 256
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
WINDOWS_DEVICE_STEMS = frozenset(
    {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


class PackageValidationError(RuntimeError):
    """The distribution archive set could not be proven safe and canonical."""


def _member_parts(name: str) -> tuple[str, ...]:
    candidate = name[:-1] if name.endswith("/") else name
    parts = tuple(candidate.split("/"))
    if (
        not candidate
        or "\\" in candidate
        or candidate.startswith("/")
        or re.match(r"^[A-Za-z]:", candidate)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise PackageValidationError("package archive member path is unsafe")
    return parts


def _portable_segment_key(segment: str) -> str:
    normalized = unicodedata.normalize("NFKC", segment)
    if (
        not normalized
        or normalized in {".", ".."}
        or normalized.endswith((" ", "."))
        or any(character in '<>:"/\\|?*' for character in normalized)
        or any(unicodedata.category(character).startswith("C") for character in normalized)
    ):
        raise PackageValidationError("package archive member segment is not portable")
    folded = unicodedata.normalize("NFKC", normalized.casefold())
    if folded.split(".", 1)[0] in WINDOWS_DEVICE_STEMS:
        raise PackageValidationError("package archive member uses a Windows device name")
    return folded


def _portable_member_key(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_portable_segment_key(part) for part in parts)


def _verify_portable_member_topology(
    paths: list[tuple[tuple[str, ...], bool]],
) -> None:
    files: dict[tuple[str, ...], tuple[str, ...]] = {}
    directories: dict[tuple[str, ...], tuple[str, ...]] = {}
    explicit_directories: set[tuple[str, ...]] = set()
    for parts, is_directory in paths:
        key = _portable_member_key(parts)
        for depth in range(1, len(key)):
            parent_key = key[:depth]
            parent_parts = parts[:depth]
            if parent_key in files:
                raise PackageValidationError("package archive file is also a path prefix")
            observed_parent = directories.get(parent_key)
            if observed_parent is not None and observed_parent != parent_parts:
                raise PackageValidationError("package archive contains portable directory aliases")
            directories.setdefault(parent_key, parent_parts)

        if is_directory:
            if key in files:
                raise PackageValidationError("package archive path is both a file and directory")
            if key in explicit_directories:
                raise PackageValidationError(
                    "package archive contains duplicate portable directories"
                )
            observed_directory = directories.get(key)
            if observed_directory is not None and observed_directory != parts:
                raise PackageValidationError("package archive contains portable directory aliases")
            directories.setdefault(key, parts)
            explicit_directories.add(key)
            continue

        if key in files:
            raise PackageValidationError("package archive contains portable file aliases")
        if key in directories:
            raise PackageValidationError("package archive path is both a file and directory")
        files[key] = parts


def _verify_wheel_members(members: list[zipfile.ZipInfo]) -> None:
    if not members or len(members) > MAX_ARCHIVE_MEMBERS:
        raise PackageValidationError("wheel archive member count is invalid")
    names = [member.filename for member in members]
    if len(names) != len(set(names)):
        raise PackageValidationError("wheel archive contains duplicate members")
    total_size = 0
    paths: list[tuple[tuple[str, ...], bool]] = []
    for member in members:
        parts = _member_parts(member.filename)
        member_type = stat.S_IFMT(member.external_attr >> 16)
        if member_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise PackageValidationError("wheel archive member type is unsafe")
        is_directory = member.is_dir()
        if (member_type == stat.S_IFDIR and not is_directory) or (
            member_type == stat.S_IFREG and is_directory
        ):
            raise PackageValidationError("wheel archive member type is inconsistent")
        paths.append((parts, is_directory))
        if member.flag_bits & 0x1 or member.compress_type not in {
            zipfile.ZIP_STORED,
            zipfile.ZIP_DEFLATED,
        }:
            raise PackageValidationError("wheel archive compression is invalid")
        if member.file_size < 0 or member.file_size > MAX_MEMBER_BYTES:
            raise PackageValidationError("wheel archive member size is invalid")
        if member.file_size > MAX_COMPRESSION_RATIO * max(member.compress_size, 1):
            raise PackageValidationError("wheel archive compression ratio is unsafe")
        total_size += member.file_size
    _verify_portable_member_topology(paths)
    if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise PackageValidationError("wheel archive expanded size is invalid")


def _verify_sdist_members(members: list[tarfile.TarInfo], root: str, archive_size: int) -> None:
    if not members or len(members) > MAX_ARCHIVE_MEMBERS:
        raise PackageValidationError("sdist archive member count is invalid")
    names = [member.name for member in members]
    if len(names) != len(set(names)):
        raise PackageValidationError("sdist archive contains duplicate members")
    total_size = 0
    paths: list[tuple[tuple[str, ...], bool]] = []
    for member in members:
        parts = _member_parts(member.name)
        paths.append((parts, member.isdir()))
        if parts[0] != root:
            raise PackageValidationError("sdist archive root is invalid")
        if not (member.isfile() or member.isdir()):
            raise PackageValidationError("sdist archive member type is unsafe")
        if member.size < 0 or member.size > MAX_MEMBER_BYTES:
            raise PackageValidationError("sdist archive member size is invalid")
        total_size += member.size
    _verify_portable_member_topology(paths)
    if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise PackageValidationError("sdist archive expanded size is invalid")
    if total_size > MAX_COMPRESSION_RATIO * max(archive_size, 1):
        raise PackageValidationError("sdist archive compression ratio is unsafe")


def _verify_metadata(payload: bytes, version: str) -> None:
    if not payload or len(payload) > MAX_METADATA_BYTES:
        raise PackageValidationError("package metadata size is invalid")
    metadata = BytesParser(policy=policy.default).parsebytes(payload)
    if metadata.defects:
        raise PackageValidationError("package metadata is invalid")
    if metadata.get_all("Name") != ["dcc-mcp-tiled"]:
        raise PackageValidationError("package project identity is invalid")
    if metadata.get_all("Version") != [version]:
        raise PackageValidationError("package version identity is invalid")


def validate_distribution_archives(dist: Path, version: str) -> None:
    wheel = dist / f"dcc_mcp_tiled-{version}-py3-none-any.whl"
    sdist = dist / f"dcc_mcp_tiled-{version}.tar.gz"
    try:
        wheel_size = wheel.stat().st_size
        sdist_size = sdist.stat().st_size
        if not (0 < wheel_size <= MAX_ARCHIVE_BYTES and 0 < sdist_size <= MAX_ARCHIVE_BYTES):
            raise PackageValidationError("package archive size is invalid")
        with zipfile.ZipFile(wheel) as archive:
            members = archive.infolist()
            _verify_wheel_members(members)
            if archive.testzip() is not None:
                raise PackageValidationError("wheel archive data is invalid")
            metadata_path = f"dcc_mcp_tiled-{version}.dist-info/METADATA"
            metadata_members = [
                member for member in members if member.filename.endswith(".dist-info/METADATA")
            ]
            if [member.filename for member in metadata_members] != [metadata_path]:
                raise PackageValidationError("wheel metadata identity is ambiguous")
            _verify_metadata(archive.read(metadata_members[0]), version)
        with tarfile.open(sdist, mode="r:gz") as archive:
            members = archive.getmembers()
            root = f"dcc_mcp_tiled-{version}"
            _verify_sdist_members(members, root, sdist_size)
            for member in members:
                if not member.isfile():
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    raise PackageValidationError("sdist archive member is unavailable")
                observed_size = 0
                while chunk := stream.read(65536):
                    observed_size += len(chunk)
                if observed_size != member.size:
                    raise PackageValidationError("sdist archive member size changed")
            metadata_path = f"dcc_mcp_tiled-{version}/PKG-INFO"
            metadata_members = [member for member in members if member.name.endswith("/PKG-INFO")]
            if [member.name for member in metadata_members] != [metadata_path]:
                raise PackageValidationError("sdist metadata identity is ambiguous")
            stream = archive.extractfile(metadata_members[0])
            if stream is None:
                raise PackageValidationError("sdist metadata is unavailable")
            _verify_metadata(stream.read(), version)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise PackageValidationError("package archive is invalid") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version) is None:
        raise PackageValidationError("package version is not canonical")
    validate_distribution_archives(args.dist, args.version)
    print("package archive semantics verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackageValidationError as exc:
        print(f"package archive verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
