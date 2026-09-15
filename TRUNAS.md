Router Settings
Gateway: 192.168.1.254
DHCP range is: 192.168.1.64-192.168.1.220
Note when allocating a 'static' ip address using AT&T router, first set the device to DHCP so that the router picks up the device. Then assign it an allocated address.

Mac Address
igb0: 0c:c4:7a:cf:39:94
192.168.1.118 / Super Micro Computer, Inc. (IPMI)
192.168.1.201 / freenas

SSH Port: 2222

Plex Media Server Jail
vnet0: 0e:c4:7a:57:7e:c1
192.168.1.202 / pms
http://192.168.1.202:32400/web



Plex Media Server Metadata Config is at the location:
df -h /tmp
du -sh "/config/Plex Media Server"
ls -lh /tmp/plex-migration.tar.gz



# Backup profiles

# SSH into the NAS and start a tmux session
ssh -i ~/.ssh/nas_sync -p 2222 remote@192.168.1.201
tmux new -s archive

# Create the archive (-C makes paths inside it start at zhang/)
tar -cf /mnt/Media/family/backups/mnt-media-windows-zhang.tar -C /mnt/Media/windows zhang

tar -cf /mnt/Media/family/backups/mnt-media-windows-pszhang.tar -C /mnt/Media/windows pszhang

tar -cf /mnt/Media/family/backups/mnt-media-windows-jason.tar -C /mnt/Media/windows jason

# Check it afterwards
ls -lh /mnt/Media/family/backups/zhang.tar
tar -tf /mnt/Media/family/backups/zhang.tar | head
