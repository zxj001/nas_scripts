#!/usr/bin/env python3
"""Utility to list files in a directory sorted by size."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


def find_largest_files(directory: Path, recursive: bool = True) -> List[Tuple[int, Path]]:
    """Return ``(size, path)`` tuples for files under ``directory`` sorted by size."""
    file_sizes: List[Tuple[int, Path]] = []
    pattern = directory.rglob('*') if recursive else directory.glob('*')
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
                # Skip files we cannot access
                continue
    if file_count > 0:
        print(f"Scanned {file_count} files total. Sorting...", flush=True)
    file_sizes.sort(key=lambda pair: pair[0], reverse=True)
    return file_sizes


def format_size(size: int) -> str:
    """Format file size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
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
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "directory", nargs="?", default=".", help="Directory to scan (default: current directory)"
    )
    parser.add_argument(
        "-n", "--num", type=int, default=None, metavar="N",
        help="Show only the top N largest files"
    )
    parser.add_argument(
        "-H", "--human-readable", action="store_true",
        help="Display file sizes in human-readable format (KB, MB, GB, etc.)"
    )
    parser.add_argument(
        "--no-recursive", action="store_true",
        help="Only scan the specified directory, not subdirectories (default: recursive)"
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Script entry point."""
    args = parse_args(argv)
    directory = Path(args.directory)

    if not directory.is_dir():
        print(f"Error: '{directory}' is not a directory")
        return 1

    file_sizes = find_largest_files(directory, recursive=not args.no_recursive)
    
    # Limit results if requested
    if args.num is not None:
        file_sizes = file_sizes[:args.num]
    
    if not file_sizes:
        print(f"No files found in '{directory}'")
        return 0
    print(f"File Size\tPath")
    for size, path in file_sizes:
        # Handle paths with invalid Unicode characters
        try:
            path_str = str(path)
        except Exception:
            path_str = repr(path)
        
        try:
            if args.human_readable:
                size_str = format_size(size)
                print(f"{size_str:>10}\t{path_str}")
            else:
                print(f"{size}\t{path_str}")
        except UnicodeEncodeError:
            # Use error handling to display problematic filenames
            path_bytes = str(path).encode('utf-8', errors='surrogateescape')
            path_safe = path_bytes.decode('utf-8', errors='replace')
            if args.human_readable:
                size_str = format_size(size)
                print(f"{size_str:>10}\t{path_safe}")
            else:
                print(f"{size}\t{path_safe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
