#!/usr/bin/env python3
"""Utility to list files in a directory sorted by size."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


def find_largest_files(
    directory: Path, recursive: bool = True
) -> Tuple[List[Tuple[int, Path]], List[Path]]:
    """Return ``(size, path)`` tuples for files under ``directory`` sorted by size, and list of error paths."""
    file_sizes: List[Tuple[int, Path]] = []
    error_paths: List[Path] = []
    pattern = directory.rglob("*") if recursive else directory.glob("*")
    file_count = 0
    for path in pattern:
        if path.is_file():
            try:
                file_sizes.append((path.stat().st_size, path))
                file_count += 1
                # Print progress every 1000 files
                if file_count % 1000 == 0:
                    print(f"Scanned {file_count} files...", flush=True)
            except OSError:
                # Track files we cannot access
                error_paths.append(path)
                continue
    if file_count > 0:
        print(f"Scanned {file_count} files total. Sorting...", flush=True)
    file_sizes.sort(key=lambda pair: pair[0], reverse=True)
    return file_sizes, error_paths


def format_size(size: int) -> str:
    """Format file size in human-readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}PB"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="List all files in a directory in descending size order.",
        epilog="""
Examples:
  %(prog)s                    # List all files in current directory (recursive)
  %(prog)s /path/to/dir       # List all files in specified directory (recursive)
  %(prog)s -n 10              # Show only top 10 largest files
  %(prog)s --human-readable   # Show sizes in human-readable format
  %(prog)s --no-recursive     # Scan only the directory itself, not subdirectories
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory to scan (default: current directory)",
    )
    parser.add_argument(
        "-n",
        "--num",
        type=int,
        default=None,
        metavar="N",
        help="Show only the top N largest files",
    )
    parser.add_argument(
        "-H",
        "--human-readable",
        action="store_true",
        help="Display file sizes in human-readable format (KB, MB, GB, etc.)",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Only scan the specified directory, not subdirectories (default: recursive)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        metavar="FILE",
        help="Save results to a file instead of printing to stdout",
    )
    return parser.parse_args(argv)


def print_error_report(
    unicode_errors: List[Tuple[int, Path]],
    error_paths: List[Path],
    args: argparse.Namespace,
) -> None:
    # Print error report
    print("\n" + "=" * 60)
    print("ERROR REPORT")
    print("=" * 60)

    if error_paths:
        print(
            f"\n{len(error_paths)} file(s) could not be accessed (permission denied or other OS error):"
        )
        for path in error_paths:
            try:
                print(f"  - {path}")
            except UnicodeEncodeError:
                path_bytes = str(path).encode("utf-8", errors="surrogateescape")
                path_safe = path_bytes.decode("utf-8", errors="replace")
                print(f"  - {path_safe}")

    if unicode_errors:
        print(
            f"\n{len(unicode_errors)} file(s) with invalid Unicode characters in filename:"
        )
        for size, path in unicode_errors:
            path_bytes = str(path).encode("utf-8", errors="surrogateescape")
            path_safe = path_bytes.decode("utf-8", errors="replace")
            if args.human_readable:
                size_str = format_size(size)
                print(f"  - {size_str:>10}\t{path_safe}")
            else:
                print(f"  - {size}\t{path_safe}")
            # Also show the raw representation for debugging
            print(f"    Raw: {repr(str(path))}")


def print_file_sizes(
    file_sizes: List[Tuple[int, Path]], args: argparse.Namespace
) -> Tuple[List[Tuple[int, Path]], List[Path]]:
    unicode_errors: List[Tuple[int, Path]] = []
    error_paths: List[Path] = []
    
    # Determine output file handle
    output_file = None
    if args.output:
        try:
            output_file = open(args.output, "w", encoding="utf-8")
        except IOError as e:
            print(f"Error: Could not open output file '{args.output}': {e}")
            return unicode_errors, error_paths
    
    def write_line(line: str) -> None:
        if output_file:
            output_file.write(line + "\n")
        else:
            print(line)
    
    write_line(f"File Size\tPath")
    for size, path in file_sizes:
        # Handle paths with invalid Unicode characters
        try:
            path_str = str(path)
        except Exception:
            path_str = repr(path)

        try:
            if args.human_readable:
                size_str = format_size(size)
                write_line(f"{size_str:>10}\t{path_str}")
            else:
                write_line(f"{size}\t{path_str}")
        except UnicodeEncodeError:
            # Track files with Unicode errors
            unicode_errors.append((size, path))
            # Use error handling to display problematic filenames
            path_bytes = str(path).encode("utf-8", errors="surrogateescape")
            path_safe = path_bytes.decode("utf-8", errors="replace")
            if args.human_readable:
                size_str = format_size(size)
                write_line(f"{size_str:>10}\t{path_safe}")
            else:
                write_line(f"{size}\t{path_safe}")
    
    if output_file:
        output_file.close()
        print(f"Results saved to '{args.output}'")
    
    return unicode_errors, error_paths

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Script entry point."""
    args = parse_args(argv)
    directory = Path(args.directory)

    if not directory.is_dir():
        print(f"Error: '{directory}' is not a directory")
        return 1

    file_sizes, error_paths = find_largest_files(
        directory, recursive=not args.no_recursive
    )

    # Limit results if requested
    if args.num is not None:
        file_sizes = file_sizes[: args.num]

    if not file_sizes:
        print(f"No files found in '{directory}'")
        return 0

    unicode_errors, error_paths = print_file_sizes(file_sizes, args)
    
    if error_paths or unicode_errors:
        print_error_report(unicode_errors, error_paths, args)
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
