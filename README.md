# Linux Setup
```
cat /etc/os-release
```
PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
NAME="Debian GNU/Linux"
VERSION_ID="13"
VERSION="13 (trixie)"
VERSION_CODENAME=trixie
DEBIAN_VERSION_FULL=13.2
ID=debian
HOME_URL="https://www.debian.org/"
SUPPORT_URL="https://www.debian.org/support"
BUG_REPORT_URL="https://bugs.debian.org/"

# Openssh Setup

// Check if ssh services is active
sudo systemctl status sshd
// start the service
sudo systemctl start sshd
// enable the service at startup
sudo systemctl enable sshd

## Connect from a client
use ~/.ssh/id_rsa
```
ssh jasonz001@192.168.1.126
```

# Disable sleep

```
// mask sleep and suspend
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
// check sleep and suspend
systemctl status sleep.target suspend.target

// for gnome
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
gsettings set org.gnome.desktop.session idle-delay 0
// check the values
gsettings get org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type
gsettings get org.gnome.desktop.session idle-delay

```
