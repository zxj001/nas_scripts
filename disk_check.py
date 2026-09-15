#!/usr/bin/env python3
"""Check disk space on the Plex folders' drives, and the health of every drive.

The NAS folders are checked too when they're available on this machine.

Exits with status 1 if anything needs attention, so it can run from cron.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, NamedTuple, Optional, Sequence

from find_largest_files import print_problems, scan_directory
from nas_common import (
    MAIN_DIRECTORIES,
    NAS_DIRECTORIES,
    display_path,
    existing_directories,
    human_size,
)

# Warn when a filesystem, or its inode table, is at least this full.
WARN_PERCENT = 90

# Warn when a drive runs hotter than this many degrees Celsius.
HDD_MAX_TEMP = 55
SSD_MAX_TEMP = 70

# Where the kernel keeps each ext4 filesystem's error count.
EXT4_SYSFS = "/sys/fs/ext4"

# Where Linux maps device numbers to block devices. FreeBSD, which the NAS
# runs, has no /sys.
SYS_DEV_BLOCK = "/sys/dev/block"

# udisks2 reads SMART data as root and shares it over D-Bus, so asking it
# works without sudo or smartctl, even for the drives in the USB enclosure.
UDISKS_COMMAND = [
    "busctl",
    "--system",
    "--json=short",
    "call",
    "org.freedesktop.UDisks2",
    "/org/freedesktop/UDisks2",
    "org.freedesktop.DBus.ObjectManager",
    "GetManagedObjects",
]
UDISKS_TIMEOUT = 60

BLOCK = "org.freedesktop.UDisks2.Block"
PARTITION = "org.freedesktop.UDisks2.Partition"
DRIVE = "org.freedesktop.UDisks2.Drive"
ATA = "org.freedesktop.UDisks2.Drive.Ata"
NVME = "org.freedesktop.UDisks2.NVMe.Controller"

# {object path: {interface: {property: value}}}
Objects = Dict[str, Dict[str, Dict[str, Any]]]


class FolderUsage(NamedTuple):
    path: str
    size: int
    files: int


class FilesystemUsage(NamedTuple):
    mount: str
    device: str
    total: int
    used: int
    free: int
    inodes_percent: int
    errors: Optional[int]


class DriveHealth(NamedTuple):
    device: str
    name: str
    temperature: Optional[int]
    hours: Optional[int]
    problems: List[str]
    has_smart: bool = True

    @property
    def status(self) -> str:
        if not self.has_smart:
            return "NO SMART"
        return "PROBLEM" if self.problems else "OK"


def percent(part: int, whole: int) -> int:
    """``part`` as a percentage of ``whole``, rounded up like df does."""
    return -(-100 * part // whole) if whole else 0


# Folders


def folder_usage(directories: Sequence[str], errors: List[str]) -> List[FolderUsage]:
    """Total size and file count of each directory, adding unreadable paths to ``errors``."""
    usage = []

    for directory in directories:
        print(f"Scanning: {directory}", file=sys.stderr)
        files = scan_directory(directory, True, errors)
        usage.append(
            FolderUsage(directory, sum(entry.size for entry in files), len(files))
        )

    return usage


def format_folders(usage: Sequence[FolderUsage]) -> List[str]:
    """Lines of a SIZE / FILES / FOLDER table, with a total when there's more than one."""
    rows = [(entry.size, entry.files, display_path(entry.path)) for entry in usage]

    if len(usage) > 1:
        rows.append(
            (sum(entry.size for entry in usage), sum(entry.files for entry in usage), "Total")
        )

    lines = [f"{'SIZE':>10}  {'FILES':>9}  FOLDER", f"{'-' * 10}  {'-' * 9}  {'-' * 40}"]
    for size, files, label in rows:
        lines.append(f"{human_size(size):>10}  {files:>9,}  {label}")
    return lines


# Filesystems


def mount_point(path: str) -> str:
    """The mount point of the filesystem holding ``path``."""
    path = os.path.realpath(path)
    while not os.path.ismount(path):
        path = os.path.dirname(path)
    return path


def block_device(path: str) -> Optional[str]:
    """Kernel name of the partition holding ``path``, like "sda1".

    None for filesystems with no block device, such as tmpfs, and on systems
    without /sys.
    """
    # Checked first because on the NAS, ZFS device numbers come back
    # negative, and os.major() raises OverflowError on them.
    if not os.path.isdir(SYS_DEV_BLOCK):
        return None

    dev = os.stat(path).st_dev
    sys_path = f"{SYS_DEV_BLOCK}/{os.major(dev)}:{os.minor(dev)}"

    if not os.path.exists(sys_path):
        return None

    return os.path.basename(os.path.realpath(sys_path))


def ext4_errors(device: str) -> Optional[int]:
    """How many errors the kernel has recorded on ext4 ``device``, or None if it isn't ext4."""
    try:
        with open(
            os.path.join(EXT4_SYSFS, device, "errors_count"), encoding="ascii"
        ) as f:
            return int(f.read())
    except (OSError, ValueError):
        return None


def filesystem_usage(mount: str) -> FilesystemUsage:
    """Space, inode and error figures for the filesystem mounted at ``mount``."""
    info = os.statvfs(mount)
    device = block_device(mount)

    return FilesystemUsage(
        mount=mount,
        device=f"/dev/{device}" if device else "-",
        total=info.f_blocks * info.f_frsize,
        used=(info.f_blocks - info.f_bfree) * info.f_frsize,
        # What ordinary users can still write; root has a reserve on top.
        free=info.f_bavail * info.f_frsize,
        inodes_percent=percent(info.f_files - info.f_ffree, info.f_files),
        errors=ext4_errors(device) if device else None,
    )


def used_percent(fs: FilesystemUsage) -> int:
    # Like df, reserved blocks count as neither used nor free.
    return percent(fs.used, fs.used + fs.free)


def filesystem_problems(fs: FilesystemUsage, warn_percent: int) -> List[str]:
    """Anything about ``fs`` that needs attention."""
    problems = []
    used = used_percent(fs)

    if used >= warn_percent:
        problems.append(f"{fs.mount} is {used}% full ({human_size(fs.free)} free)")

    if fs.inodes_percent >= warn_percent:
        problems.append(f"{fs.mount} has used {fs.inodes_percent}% of its inodes")

    if fs.errors:
        problems.append(
            f"{fs.mount}: the kernel has recorded {fs.errors} ext4 error(s) on "
            f"{fs.device} - check dmesg, then run e2fsck on it while unmounted"
        )

    return problems


def format_filesystems(filesystems: Sequence[FilesystemUsage]) -> List[str]:
    """Lines of a df-style table for ``filesystems``."""
    lines = [
        f"{'USE%':>4}  {'SIZE':>10}  {'USED':>10}  {'FREE':>10}  "
        f"{'INODES':>6}  {'ERRORS':>6}  {'DEVICE':<14}  MOUNTED ON",
        f"{'-' * 4}  {'-' * 10}  {'-' * 10}  {'-' * 10}  "
        f"{'-' * 6}  {'-' * 6}  {'-' * 14}  {'-' * 24}",
    ]

    for fs in filesystems:
        errors = "-" if fs.errors is None else str(fs.errors)
        lines.append(
            f"{used_percent(fs):>3}%  {human_size(fs.total):>10}  "
            f"{human_size(fs.used):>10}  {human_size(fs.free):>10}  "
            f"{fs.inodes_percent:>5}%  {errors:>6}  {fs.device:<14}  "
            f"{display_path(fs.mount)}"
        )

    return lines


# Drives


def read_udisks() -> Objects:
    """Every object udisks knows about, as {path: {interface: {property: value}}}.

    Raises OSError if udisks can't be asked.
    """
    try:
        result = subprocess.run(
            UDISKS_COMMAND,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=UDISKS_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise OSError(f"no answer from udisks after {UDISKS_TIMEOUT} seconds") from None

    if result.returncode != 0:
        raise OSError(
            result.stderr.strip() or f"busctl exited with status {result.returncode}"
        )

    try:
        objects = json.loads(result.stdout)["data"][0]
        # busctl wraps every property value as {"type": ..., "data": ...}.
        return {
            path: {
                interface: {name: value["data"] for name, value in properties.items()}
                for interface, properties in interfaces.items()
            }
            for path, interfaces in objects.items()
        }
    except (ValueError, LookupError, TypeError, AttributeError):
        raise OSError("could not understand busctl's output") from None


def drive_devices(objects: Objects) -> Dict[str, str]:
    """Map each drive's udisks path to its whole-disk device, like "/dev/sda"."""
    devices = {}

    for interfaces in objects.values():
        block = interfaces.get(BLOCK)

        if block and PARTITION not in interfaces and block.get("Drive", "/") != "/":
            # Device is a NUL-terminated byte string.
            devices[block["Drive"]] = (
                bytes(block["Device"]).rstrip(b"\0").decode(errors="replace")
            )

    return devices


def drive_health(objects: Objects) -> List[DriveHealth]:
    """Health of every drive udisks knows about, sorted by device."""
    devices = drive_devices(objects)
    drives = []

    for path, interfaces in objects.items():
        drive = interfaces.get(DRIVE)

        # Empty bays in the USB enclosure show up as drives with no size.
        if not drive or not drive.get("Size"):
            continue

        drives.append(check_drive(devices.get(path, "-"), interfaces))

    return sorted(drives, key=lambda drive: drive.device)


def check_drive(device: str, interfaces: Dict[str, Dict[str, Any]]) -> DriveHealth:
    """Health of one drive from its udisks ``interfaces``."""
    drive = interfaces[DRIVE]
    name = drive.get("Model") or drive.get("Id", "")
    if drive.get("Serial"):
        name += f" ({drive['Serial']})"

    ata = interfaces.get(ATA, {})
    nvme = interfaces.get(NVME, {})

    # SmartUpdated stays 0 until udisks has managed to read SMART data.
    if ata.get("SmartUpdated"):
        temperature = celsius(ata.get("SmartTemperature"))
        hours = ata.get("SmartPowerOnSeconds", 0) // 3600
        problems = ata_problems(ata)
    elif nvme.get("SmartUpdated"):
        temperature = celsius(nvme.get("SmartTemperature"))
        hours = nvme.get("SmartPowerOnHours")
        problems = nvme_problems(nvme)
    else:
        return DriveHealth(device, name, None, None, [], has_smart=False)

    # A RotationRate of 0 means an SSD; -1 means spinning at an unknown speed.
    max_temp = HDD_MAX_TEMP if drive.get("RotationRate") else SSD_MAX_TEMP

    if temperature is not None and temperature > max_temp:
        problems.append(f"running hot at {temperature}°C (limit {max_temp}°C)")

    return DriveHealth(device, name, temperature, hours, problems)


def celsius(kelvin: Optional[float]) -> Optional[int]:
    # udisks reports Kelvin, or 0 when the drive doesn't say.
    return round(kelvin - 273.15) if kelvin else None


def ata_problems(smart: Dict[str, Any]) -> List[str]:
    """Problems in an ATA drive's SMART data."""
    problems = []

    if smart.get("SmartFailing"):
        problems.append(
            "SMART predicts the drive will fail soon - back it up and replace it"
        )

    for key, label in [
        ("SmartNumBadSectors", "bad sector(s)"),
        ("SmartNumAttributesFailing", "SMART attribute(s) failing now"),
        ("SmartNumAttributesFailedInThePast", "SMART attribute(s) failed in the past"),
    ]:
        # udisks uses -1 for unknown.
        if smart.get(key, 0) > 0:
            problems.append(f"{smart[key]:,} {label}")

    problems.extend(self_test_problems(smart.get("SmartSelftestStatus", "")))
    return problems


def nvme_problems(smart: Dict[str, Any]) -> List[str]:
    """Problems in an NVMe drive's SMART data."""
    problems = []
    warnings = smart.get("SmartCriticalWarning") or []

    if warnings:
        problems.append("NVMe critical warning: " + ", ".join(warnings))

    problems.extend(self_test_problems(smart.get("SmartSelftestStatus", "")))
    return problems


def self_test_problems(status: str) -> List[str]:
    """A problem if the last self-test found a fault.

    Tests that were aborted or interrupted, or never run, don't count.
    """
    if "fail" in status or "fatal" in status or status.startswith("error"):
        return [f"last SMART self-test result: {status}"]
    return []


def format_drives(drives: Sequence[DriveHealth]) -> List[str]:
    """Lines of a STATUS / TEMP / HOURS / DEVICE / DRIVE table."""
    lines = [
        f"{'STATUS':<8}  {'TEMP':>5}  {'HOURS':>7}  {'DEVICE':<12}  DRIVE",
        f"{'-' * 8}  {'-' * 5}  {'-' * 7}  {'-' * 12}  {'-' * 32}",
    ]

    for drive in drives:
        temperature = "-" if drive.temperature is None else f"{drive.temperature}°C"
        hours = "-" if drive.hours is None else f"{drive.hours:,}"
        lines.append(
            f"{drive.status:<8}  {temperature:>5}  {hours:>7}  "
            f"{drive.device:<12}  {drive.name}"
        )

    return lines


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Check disk space on the Plex folders' drives, and the health "
        "of every drive.",
        epilog="""
Exits with status 1 if a problem is found, so it can run from cron.

Examples:
  %(prog)s                     Check the Plex and NAS folders and every drive
  %(prog)s ~/Downloads         Size up ~/Downloads instead of the default folders
  %(prog)s --warn-percent 80   Warn once a filesystem is 80%% full
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "directories",
        nargs="*",
        metavar="directory",
        help="Directories to size up (default: the Plex folders, plus the NAS "
        "folders if they're available here)",
    )
    parser.add_argument(
        "--warn-percent",
        type=int,
        default=WARN_PERCENT,
        metavar="PERCENT",
        help=f"Warn when a filesystem is at least this full (default: {WARN_PERCENT})",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Script entry point."""
    args = parse_args(argv)

    if args.directories:
        requested = args.directories
    else:
        # The NAS folders are only here when the NAS is mounted, so skip
        # them quietly rather than reporting them missing.
        requested = MAIN_DIRECTORIES + [d for d in NAS_DIRECTORIES if os.path.isdir(d)]

    directories = existing_directories(requested)

    # A missing Plex folder usually means its drive isn't mounted.
    problems = [
        f"{display_path(directory)}: not found - is the drive mounted?"
        for directory in requested
        if directory not in directories
    ]

    errors: List[str] = []
    folders = folder_usage(directories, errors)

    # Always check the system drive too; a full / breaks everything else.
    mounts = list(dict.fromkeys(["/"] + [mount_point(d) for d in directories]))
    filesystems = [filesystem_usage(mount) for mount in mounts]

    for fs in filesystems:
        problems.extend(filesystem_problems(fs, args.warn_percent))

    try:
        drives = drive_health(read_udisks())
    except OSError as error:
        drives = []
        problems.append(f"could not read drive health from udisks: {error}")

    for drive in drives:
        problems.extend(f"{drive.device} {drive.name}: {p}" for p in drive.problems)

    print("Disk space")
    print("\n".join(format_filesystems(filesystems)))

    if folders:
        print("\nFolder sizes")
        print("\n".join(format_folders(folders)))
        print_problems([], errors)

    if drives:
        print("\nDrive health")
        print("\n".join(format_drives(drives)))

    print()
    if problems:
        print(f"{len(problems)} problem(s) found:")
        for problem in problems:
            print(f"  {problem}")
    else:
        print("All checks passed.")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
