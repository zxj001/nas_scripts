#!/usr/bin/env python3
"""List the largest files under the Plex folders (or any directories), biggest first."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from typing import List, NamedTuple, Optional, Sequence, Tuple

from nas_common import (
    MAIN_DIRECTORIES,
    display_path,
    existing_directories,
    human_size,
)

# Print a progress line every this many files.
PROGRESS_EVERY = 1000


class FileEntry(NamedTuple):
    size: int
    path: str


def scan(
    directories: Sequence[str], recursive: bool = True
) -> Tuple[List[FileEntry], List[str]]:
    """Return every file under ``directories``, and the paths that couldn't be read."""
    files: List[FileEntry] = []
    errors: List[str] = []

    for directory in directories:
        print(f"Scanning: {directory}", file=sys.stderr)
        files.extend(scan_directory(directory, recursive, errors))

    return files, errors


def scan_directory(
    directory: str, recursive: bool, errors: List[str]
) -> List[FileEntry]:
    """Return the regular files under ``directory``, adding unreadable paths to ``errors``.

    Symlinks are skipped so the same file isn't counted twice.
    """
    files: List[FileEntry] = []

    for root, subdirs, names in os.walk(
        directory, onerror=lambda error: errors.append(error.filename)
    ):
        if not recursive:
            subdirs.clear()

        for name in names:
            path = os.path.join(root, name)

            try:
                info = os.lstat(path)
            except OSError:
                errors.append(path)
                continue

            if not stat.S_ISREG(info.st_mode):
                continue

            files.append(FileEntry(info.st_size, path))

            # Progress goes to stderr so the list itself can be piped.
            if len(files) % PROGRESS_EVERY == 0:
                print(
                    f"\rScanned {len(files):,} files...",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )

    if len(files) >= PROGRESS_EVERY:
        print(file=sys.stderr)

    return files


def largest(
    files: Sequence[FileEntry], top: int = 0, min_size: int = 0
) -> List[FileEntry]:
    """Files of at least ``min_size`` bytes, biggest first. A ``top`` of 0 keeps all."""
    result = sorted(
        (entry for entry in files if entry.size >= min_size),
        key=lambda entry: (-entry.size, entry.path),
    )
    return result[:top] if top > 0 else result


def format_table(files: Sequence[FileEntry], exact_bytes: bool = False) -> List[str]:
    """Lines of a SIZE / FILE table for ``files``."""
    width = 15 if exact_bytes else 10
    lines = [f"{'SIZE':>{width}}  FILE", f"{'-' * width}  {'-' * 40}"]

    for entry in files:
        size = str(entry.size) if exact_bytes else human_size(entry.size)
        lines.append(f"{size:>{width}}  {display_path(entry.path)}")

    return lines


def print_problems(listed: Sequence[FileEntry], errors: Sequence[str]) -> None:
    """Report unreadable paths, and listed files whose names aren't valid UTF-8."""
    if errors:
        print(f"\n{len(errors)} path(s) could not be read:", file=sys.stderr)
        for path in errors:
            print(f"  {display_path(path)}", file=sys.stderr)

    bad_names = [entry.path for entry in listed if display_path(entry.path) != entry.path]

    if bad_names:
        print(
            f"\n{len(bad_names)} listed file(s) have names that aren't valid UTF-8:",
            file=sys.stderr,
        )
        for path in bad_names:
            # The raw bytes show exactly what needs renaming.
            print(
                f"  {display_path(path)}\n    raw: {os.fsencode(path)!r}",
                file=sys.stderr,
            )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="List the largest files, biggest first.",
        epilog="""
Examples:
  %(prog)s                      Top 20 files across the Plex folders
  %(prog)s ~/Downloads -n 50    Top 50 files in ~/Downloads
  %(prog)s --min-size 10        Only files of 10 GB or more
  %(prog)s -n 0 -o files.txt    Save every file to files.txt
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "directories",
        nargs="*",
        metavar="directory",
        help="Directories to scan (default: the Plex folders)",
    )
    parser.add_argument(
        "-n",
        "--top",
        type=int,
        default=20,
        metavar="N",
        help="Number of files to show, 0 for all (default: 20)",
    )
    parser.add_argument(
        "--min-size",
        type=float,
        default=0,
        metavar="GB",
        help="Only show files at least this many GB",
    )
    parser.add_argument(
        "--bytes",
        action="store_true",
        help="Show exact sizes in bytes instead of KiB/MiB/GiB",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Only scan the directories themselves, not subdirectories",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="Save the list to FILE instead of printing it",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Script entry point."""
    args = parse_args(argv)

    directories = existing_directories(args.directories or MAIN_DIRECTORIES)

    if not directories:
        print("Error: no directories to scan.", file=sys.stderr)
        return 1

    files, errors = scan(directories, recursive=not args.no_recursive)
    listed = largest(files, top=args.top, min_size=int(args.min_size * 1024**3))

    if not listed:
        print("No files found.")
    else:
        table = "\n".join(format_table(listed, exact_bytes=args.bytes))

        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8") as output:
                    output.write(table + "\n")
            except OSError as error:
                print(
                    f"Error: could not write {args.output}: {error.strerror}",
                    file=sys.stderr,
                )
                return 1
            print(f"Saved {len(listed)} file(s) to {args.output}", file=sys.stderr)
        else:
            print(table)

    print_problems(listed, errors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
