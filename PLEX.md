
# Plex Server setup

// Install git so that we can setup our scripts repo
sudo apt install git

Here's a step-by-step guide for Debian:
1. Check if Plex is a systemd Service

    Open your terminal and check: 
    ```
    systemctl status plexmediaserver
    ```
    If it shows Loaded: loaded (...), it's managed by systemd. 

2. Configure for Automatic Start & Restart

    Create a Drop-in Directory:
    bash sudo mkdir -p /etc/systemd/system/plexmediaserver.service.d/
    Create a Configuration File:
    bash sudo nano /etc/systemd/system/plexmediaserver.service.d/override.conf
    Add the following content to override.conf to ensure it starts after network and restarts on failure (which covers unexpected shutdowns/crashes):

```ini
[Unit]
After=network-online.target
Requires=network-online.target 

[Service]
Restart=on-failure
RestartSec=5s # Wait 5 seconds before restarting
```
This tells systemd to wait for the network to be fully up before starting Plex and to automatically try restarting it if it stops unexpectedly (like during a shutdown cycle). 
3. Reload Systemd & Enable the Service

    Reload systemd to recognize your changes:
```
bash sudo systemctl daemon-reload
```
    Enable Plex to start on boot:
```
bash sudo systemctl enable plexmediaserver
```
# Drive setup

sudo umount /dev/sda1 /dev/sdb1
sudo mkfs.ext4 -F /dev/sda1
sudo mkfs.ext4 -F /dev/sdb1

## Mount drives
sudo mkdir -p /media/jasonz001/Drive1 /media/jasonz001/Drive2
sudo chown -R jasonz001:jasonz001 /media/jasonz001/Drive1 /media/jasonz001/Drive2

// Wipe and format the drives
sudo umount /dev/sda1 /dev/sdb1
sudo mkfs.ext4 -F /dev/sda1
sudo mkfs.ext4 -F /dev/sdb1

// Get new UUIDs
sudo blkid /dev/sda1 /dev/sdb1
// Update the mount file
sudo nano /etc/fstab

/dev/sda1: UUID="d885039c-9ea0-4301-8606-cb95367a6b2f" BLOCK_SIZE="4096"TYPE="ext4" PARTUUID="6930cbbe-ad6f-428d-bdde-34f56f0cd9d1"
/dev/sdb1: UUID="8068a03d-7267-4daf-b766-a22aa1d0e870" BLOCK_SIZE="4096"TYPE="ext4" PARTUUID="a6095df6-2fe9-4e40-8e0b-dd62808c07f8"

UUID=d885039c-9ea0-4301-8606-cb95367a6b2f /media/jasonz001/Drive1 ext4 defaults,nofail 0 2
UUID=8068a03d-7267-4daf-b766-a22aa1d0e870 /media/jasonz001/Drive2 ext4 defaults,nofail 0 2


// Check mounts
sudo mkdir -p /media/jasonz001/Drive1 /media/jasonz001/Drive2
sudo systemctl daemon-reload
sudo mount -a
sudo chown -R jasonz001:jasonz001 /media/jasonz001/Drive1 /media/jasonz001/Drive2
sudo chmod 755 /media/jasonz001/Drive1 /media/jasonz001/Drive2

// Confirm success
df -h | grep Drive



## Syncing Plex files
Plex media can be found at:
"/media/jasonz001/Drive1/Plex1/Disney"
"/media/jasonz001/Drive1/Plex1/Movies"
"/media/jasonz001/Drive2/Plex2/Anime"
"/media/jasonz001/Drive2/Plex2/Anime_Movies"
"/media/jasonz001/Drive2/Plex2/TV_Shows"

## From the Truenas (usually over SSH)
Truenas: 192.168.1.201:2222
Use key: ~/.ssh/nas_sync
```
// -n does a dryrun
rsync -ahPn --preallocate --no-delay-updates --size-only --delete --info=progress2 -e "ssh -i ~/.ssh/nas_sync -p 2222" remote@192.168.1.201:/mnt/Media/family/videos/Disney/ "/media/jasonz001/Drive1/Plex1/Disney/"
rsync -ahPn --preallocate --no-delay-updates --size-only --delete --info=progress2 -e "ssh -i ~/.ssh/nas_sync -p 2222" remote@192.168.1.201:/mnt/Media/family/videos/Movies/ "/media/jasonz001/Drive1/Plex1/Movies/"

rsync -ahPn --preallocate --no-delay-updates --size-only --delete --info=progress2 -e "ssh -i ~/.ssh/nas_sync -p 2222" remote@192.168.1.201:/mnt/Media/family/videos/Anime/ /media/jasonz001/Drive2/Plex2/Anime/
rsync -ahPn --preallocate --no-delay-updates --size-only --delete --info=progress2 -e "ssh -i ~/.ssh/nas_sync -p 2222" "remote@192.168.1.201:/mnt/Media/family/videos/Anime Movies/" "/media/jasonz001/Drive2/Plex2/Anime_Movies/"
rsync -ahPn --preallocate --no-delay-updates --size-only --delete --info=progress2 -e "ssh -i ~/.ssh/nas_sync -p 2222" "remote@192.168.1.201:/mnt/Media/family/videos/TV Shows/" "/media/jasonz001/Drive2/Plex2/TV_Shows/"
```