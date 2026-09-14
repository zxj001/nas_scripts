# Plex Server Setup

Setup notes for the Debian Plex server: service config, media drives, and syncing media from the TrueNAS.

## Prerequisites

Install git so the scripts repo can be cloned:

```bash
sudo apt install git
```

## Plex service (systemd)

### 1. Check that Plex is a systemd service

```bash
systemctl status plexmediaserver
```

If it shows `Loaded: loaded (...)`, it's managed by systemd.

### 2. Configure automatic start and restart

Create a drop-in directory and an override file:

```bash
sudo mkdir -p /etc/systemd/system/plexmediaserver.service.d/
sudo nano /etc/systemd/system/plexmediaserver.service.d/override.conf
```

Add the following to `override.conf`:

```ini
[Unit]
After=network-online.target
Requires=network-online.target

[Service]
Restart=on-failure
# Wait 5 seconds before restarting
RestartSec=5s
```

This makes systemd wait for the network to be fully up before starting Plex, and automatically restart it if it stops unexpectedly (crashes, or during a shutdown cycle).

> systemd only treats `#` as a comment at the start of a line. A trailing comment like `RestartSec=5s # ...` makes the value invalid and systemd silently ignores it.

### 3. Reload systemd and enable the service

```bash
# Pick up the override
sudo systemctl daemon-reload

# Start Plex on boot
sudo systemctl enable plexmediaserver
```

## Drive setup

Two data drives, `/dev/sda1` and `/dev/sdb1`, mounted at `/media/jasonz001/Drive1` and `/media/jasonz001/Drive2`.

### 1. Wipe and format the drives

> **Destructive:** this erases everything on both drives. Confirm the device names with `lsblk` first.

```bash
sudo umount /dev/sda1 /dev/sdb1
sudo mkfs.ext4 -F /dev/sda1
sudo mkfs.ext4 -F /dev/sdb1
```

### 2. Get the new UUIDs

Formatting generates new UUIDs, so look them up:

```bash
sudo blkid /dev/sda1 /dev/sdb1
```

Output from the current format:

```
/dev/sda1: UUID="d885039c-9ea0-4301-8606-cb95367a6b2f" BLOCK_SIZE="4096" TYPE="ext4" PARTUUID="6930cbbe-ad6f-428d-bdde-34f56f0cd9d1"
/dev/sdb1: UUID="8068a03d-7267-4daf-b766-a22aa1d0e870" BLOCK_SIZE="4096" TYPE="ext4" PARTUUID="a6095df6-2fe9-4e40-8e0b-dd62808c07f8"
```

### 3. Add the drives to fstab

```bash
sudo nano /etc/fstab
```

Add one line per drive using the UUIDs from above. `nofail` lets the system boot even if a drive is missing.

```
# External media drives
UUID=d885039c-9ea0-4301-8606-cb95367a6b2f /media/jasonz001/Drive1 ext4 defaults,nofail 0 2
UUID=8068a03d-7267-4daf-b766-a22aa1d0e870 /media/jasonz001/Drive2 ext4 defaults,nofail 0 2
```

### 4. Mount and set permissions

```bash
# Create the mount points
sudo mkdir -p /media/jasonz001/Drive1 /media/jasonz001/Drive2

# Mount everything in fstab
sudo systemctl daemon-reload
sudo mount -a

# Give ownership to jasonz001 (must run after mounting, or it only
# changes the empty mount-point directories underneath)
sudo chown -R jasonz001:jasonz001 /media/jasonz001/Drive1 /media/jasonz001/Drive2
sudo chmod 755 /media/jasonz001/Drive1 /media/jasonz001/Drive2
```

### 5. Confirm

```bash
df -h | grep Drive
```

## Syncing Plex media

### Media locations

| Library      | Path                                       |
| ------------ | ------------------------------------------ |
| Disney       | `/media/jasonz001/Drive1/Plex1/Disney`       |
| Movies       | `/media/jasonz001/Drive1/Plex1/Movies`       |
| Anime        | `/media/jasonz001/Drive2/Plex2/Anime`        |
| Anime Movies | `/media/jasonz001/Drive2/Plex2/Anime_Movies` |
| TV Shows     | `/media/jasonz001/Drive2/Plex2/TV_Shows`     |

### Sync from the TrueNAS (over SSH)

- **Host:** `192.168.1.201`, SSH port `2222`
- **Key:** `~/.ssh/nas_sync`

The commands below include `-n` (dry run): they only list what would change. Check the output, then remove `-n` to do the real sync. This matters because `--delete` removes anything on the Plex drives that isn't on the NAS.

```bash
# Drive1
rsync -ahPn --preallocate --no-delay-updates --size-only --delete --info=progress2 -e "ssh -i ~/.ssh/nas_sync -p 2222" "remote@192.168.1.201:/mnt/Media/family/videos/Disney/" "/media/jasonz001/Drive1/Plex1/Disney/"
rsync -ahPn --preallocate --no-delay-updates --size-only --delete --info=progress2 -e "ssh -i ~/.ssh/nas_sync -p 2222" "remote@192.168.1.201:/mnt/Media/family/videos/Movies/" "/media/jasonz001/Drive1/Plex1/Movies/"

# Drive2
rsync -ahPn --preallocate --no-delay-updates --size-only --delete --info=progress2 -e "ssh -i ~/.ssh/nas_sync -p 2222" "remote@192.168.1.201:/mnt/Media/family/videos/Anime/" "/media/jasonz001/Drive2/Plex2/Anime/"
rsync -ahPn --preallocate --no-delay-updates --size-only --delete --info=progress2 -e "ssh -i ~/.ssh/nas_sync -p 2222" "remote@192.168.1.201:/mnt/Media/family/videos/Anime Movies/" "/media/jasonz001/Drive2/Plex2/Anime_Movies/"
rsync -ahPn --preallocate --no-delay-updates --size-only --delete --info=progress2 -e "ssh -i ~/.ssh/nas_sync -p 2222" "remote@192.168.1.201:/mnt/Media/family/videos/TV Shows/" "/media/jasonz001/Drive2/Plex2/TV_Shows/"
```

### Other folders

```
rsync -ahPn --preallocate --no-delay-updates --size-only --delete --info=progress2 -e "ssh -i ~/.ssh/nas_sync -p 2222" "remote@192.168.1.201:/mnt/Media/family/music/" "/media/jasonz001/Drive2/Music/"

rsync -ahPn --preallocate --no-delay-updates --size-only --delete --info=progress2 -e "ssh -i ~/.ssh/nas_sync -p 2222" "remote@192.168.1.201:/mnt/Media/family/backups/" "/media/jasonz001/Drive2/Backups/"

rsync -ahPn --preallocate --no-delay-updates --size-only --delete --info=progress2 -e "ssh -i ~/.ssh/nas_sync -p 2222" "remote@192.168.1.201:/mnt/Media/family/software/" "/media/jasonz001/Drive2/Software/"
```

| Flag                 | Purpose                                                               |
| -------------------- | --------------------------------------------------------------------- |
| `-a`                 | Archive mode: recurse and preserve permissions, times, symlinks       |
| `-h`                 | Human-readable sizes                                                  |
| `-P`                 | Keep partially transferred files so interrupted syncs can resume      |
| `-n`                 | Dry run: show what would change without copying or deleting anything  |
| `--preallocate`      | Reserve each file's full size on disk before writing (see below)      |
| `--no-delay-updates` | Rename each file into place as soon as it finishes                    |
| `--size-only`        | Skip files whose size already matches, ignoring timestamps            |
| `--delete`           | Remove files on the Plex drive that no longer exist on the NAS        |
| `--info=progress2`   | Show overall progress for the whole transfer instead of per file      |
| `-e "ssh ..."`       | Connect with the `nas_sync` key on port 2222                          |

### Why rsync keeps the files (mostly) unfragmented

**Sequential writes.** rsync transfers one file at a time, start to finish, in a single write stream. Each file is written to a hidden temp file (`.name.XXXXXX`) in the target directory and renamed into place once it's complete. Because only one file is being written at any moment, ext4's allocator can give it one long contiguous run of blocks. If several files were copied at once (parallel copies, file managers, multiple rsyncs to the same drive), their blocks would interleave on disk and every file would end up split into chunks.

**`--preallocate` does most of the work.** Before writing any data, rsync calls `fallocate()` to reserve the file's full size. ext4 then picks one contiguous extent (or a few large ones) for the whole file instead of growing it piece by piece. On freshly formatted drives there's plenty of contiguous free space, so large video files land in very few extents.

**It minimizes fragmentation, it doesn't guarantee zero:**

- A file bigger than ext4's maximum extent size (128 MiB) is always split into multiple extents, but they sit back-to-back on disk, so reads stay sequential.
- As the drive fills up and `--delete` removes old files, free space gets broken up, and new files may have to be split to fit.

**Keeping it that way:**

- Run the commands for the same drive one after another, not at the same time (don't background them with `&`). One Drive1 sync and one Drive2 sync in parallel is fine, since they're separate disks.
- Don't add `--inplace`. It overwrites existing files in place, which can scatter the changed blocks.

**Checking fragmentation:**

```bash
# Drive-wide fragmentation score
sudo e4defrag -c /media/jasonz001/Drive1

# Extents for a single file
filefrag "/media/jasonz001/Drive1/Plex1/Movies/<file>.mkv"
```
