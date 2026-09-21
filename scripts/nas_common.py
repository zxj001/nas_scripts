"""Settings and helpers shared by the NAS scripts."""

import os
import sys

# Plex library folders the scripts scan when no directory is given.
MAIN_DIRECTORIES = [
    "/media/jasonz001/Drive1/Plex1/Disney",
    "/media/jasonz001/Drive1/Plex1/Movies",
    "/media/jasonz001/Drive2/Plex2/Anime",
    "/media/jasonz001/Drive2/Plex2/Anime_Movies",
    "/media/jasonz001/Drive2/Plex2/TV_Shows",
]

# Shared folders that used to live on the retired TrueNAS box (192.168.1.201).
# That server is gone; these paths only resolve where the same mount points still
# exist. existing_directories() warns and skips any that are missing, so leaving
# them listed is harmless. Update these once the replacement share is set up.
NAS_DIRECTORIES = [
    "/mnt/Media/family",
    "/mnt/Media/windows",
]


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


def existing_directories(directories):
    """Return the directories that exist, warning about the ones that don't."""
    found = []

    for directory in directories:
        if os.path.isdir(directory):
            found.append(directory)
        else:
            print(f"Warning: skipping, not a directory: {directory}", file=sys.stderr)

    return found
