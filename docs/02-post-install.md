# 2. Post-install

Automated by `setup-machine --only upgrade,guest-agent,no-sleep` (the snapshot stays manual).

## Update

```
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

## Guest agent (VM)

```
sudo apt install -y qemu-guest-agent spice-vdagent
sudo systemctl enable --now qemu-guest-agent
```

Then in Proxmox: VM → Options → QEMU Guest Agent → Enabled, and reboot the VM.

## Disable sleep

Always-on machines shouldn't suspend.

```
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
systemctl status sleep.target suspend.target   # should say masked
```

GNOME also has its own idle settings:

```
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
gsettings set org.gnome.desktop.session idle-delay 0
```

## Lighter XFCE (optional)

Applications → Settings → Window Manager Tweaks → Compositor → uncheck
**Enable display compositing**. That drops shadows and transparency and saves a little GPU work.

## Browser

Firefox comes preinstalled. For Chromium: `sudo apt install chromium`.

## Snapshot (VM)

Once networking, sudo and the guest agent work: VM → Snapshots → Take Snapshot, name it
`clean-xfce-base`. That's your rollback point before GPU passthrough or anything risky.

Next: [03-ssh.md](03-ssh.md)
