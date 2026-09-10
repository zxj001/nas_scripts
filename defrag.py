#!/usr/bin/env python3

import argparse
import os
import re
import shutil
import subprocess
import sys


def human_size(num_bytes):
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(num_bytes)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024


def display_path(path):
    # Filenames that aren't valid UTF-8 come back from os.walk with
    # surrogate escapes, which can't be printed as-is.
    return path.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def find_tool(name):
    path = shutil.which(name)

    if path:
        return path

    # Debian commonly installs e2fsprogs tools here
    if os.path.isfile(f"/usr/sbin/{name}"):
        return f"/usr/sbin/{name}"

    print(
        f"Error: {name} not found.\n"
        "Install it with:\n"
        "  sudo apt install e2fsprogs",
        file=sys.stderr,
    )
    sys.exit(1)


def get_extents(filefrag, filename):
    try:
        result = subprocess.run(
            [filefrag, filename],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
            check=False,
        )
    except OSError:
        return None

    # Typical outputs:
    # filename: 82 extents found
    # filename: 1 extent found
    match = re.search(r":\s+(\d+)\s+extents?\s+found", result.stdout)

    if match:
        return int(match.group(1))

    return None


def scan_directory(directory, filefrag):
    results = []
    count = 0

    for root, dirs, files in os.walk(directory):
        for name in files:
            filename = os.path.join(root, name)

            try:
                size = os.path.getsize(filename)
            except OSError:
                continue

            # Ignore empty files
            if size == 0:
                continue

            extents = get_extents(filefrag, filename)

            if extents is None:
                continue

            avg_extent = size / extents

            results.append(
                {
                    "path": filename,
                    "size": size,
                    "extents": extents,
                    "avg_extent": avg_extent,
                }
            )

            count += 1

            # Progress goes to stderr so output can still be redirected.
            if count % 100 == 0:
                print(
                    f"\rScanned {count:,} files...",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )

    if count >= 100:
        print(file=sys.stderr)

    return results


def defrag_files(results, filefrag):
    e4defrag = find_tool("e4defrag")

    # Files with a single extent can't be improved.
    targets = [item for item in results if item["extents"] > 1]

    if not targets:
        print("\nNothing to defragment.")
        return

    print(f"\nDefragmenting {len(targets)} file(s) with e4defrag...")

    failed = 0

    for i, item in enumerate(targets, 1):
        path = item["path"]
        print(f"\n[{i}/{len(targets)}] {display_path(path)}")

        result = subprocess.run(
            [e4defrag, path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            check=False,
        )

        after = get_extents(filefrag, path)

        if result.returncode != 0 or after is None:
            failed += 1
            print(result.stdout.rstrip(), file=sys.stderr)
            continue

        print(f"  extents: {item['extents']:,} -> {after:,}")

        if after >= item["extents"]:
            print(
                "  (no improvement - e4defrag may need more contiguous "
                "free space, or root if you don't own the file)"
            )

    if failed:
        print(f"\n{failed} file(s) failed to defragment.", file=sys.stderr)

MAIN_DIRECTORIES = [
    "/media/jasonz001/Drive1/Plex1/Disney",
    "/media/jasonz001/Drive1/Plex1/Movies",
    "/media/jasonz001/Drive2/Plex2/Anime",
    "/media/jasonz001/Drive2/Plex2/Anime_Movies",
    "/media/jasonz001/Drive2/Plex2/TV_Shows",
]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Find the most fragmented files using filefrag."
    )

    parser.add_argument(
        "directories",
        nargs="*",
        metavar="directory",
        help="Directories to scan recursively (default: main directories)",
    )

    parser.add_argument(
        "-n",
        "--top",
        type=int,
        default=20,
        help="Number of results to display (default: 20)",
    )

    parser.add_argument(
        "--min-size",
        type=float,
        default=0,
        metavar="GB",
        help="Only show files at least this many GB",
    )

    parser.add_argument(
        "--defrag",
        action="store_true",
        help="Defragment the listed top -n files with e4defrag",
    )

    args = parser.parse_args(argv)

    directories = [
        os.path.abspath(d) for d in (args.directories or MAIN_DIRECTORIES)
    ]

    missing = [d for d in directories if not os.path.isdir(d)]

    for directory in missing:
        print(f"Warning: skipping, not a directory: {directory}", file=sys.stderr)

    directories = [d for d in directories if d not in missing]

    if not directories:
        print("Error: no directories to scan.", file=sys.stderr)
        sys.exit(1)

    filefrag = find_tool("filefrag")

    results = []

    for directory in directories:
        print(f"Scanning: {directory}", file=sys.stderr)
        results.extend(scan_directory(directory, filefrag))

    min_bytes = args.min_size * 1024**3

    results = [
        item for item in results
        if item["size"] >= min_bytes
    ]

    # Most fragmented first: highest number of extents, then smallest
    # average extent size as the tie-breaker.
    results.sort(
        key=lambda x: (-x["extents"], x["avg_extent"]),
    )

    results = results[:args.top]

    if not results:
        print("No files found.")
        return

    print()
    print(
        f"{'EXTENTS':>8}  "
        f"{'SIZE':>10}  "
        f"{'AVG EXTENT':>12}  "
        f"FILE"
    )

    print(
        f"{'-' * 8}  "
        f"{'-' * 10}  "
        f"{'-' * 12}  "
        f"{'-' * 40}"
    )

    for item in results:
        print(
            f"{item['extents']:>8,}  "
            f"{human_size(item['size']):>10}  "
            f"{human_size(item['avg_extent']):>12}  "
            f"{display_path(item['path'])}"
        )

    if args.defrag:
        defrag_files(results, filefrag)


if __name__ == "__main__":
    main()