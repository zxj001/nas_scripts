# 8. ShellFish widgets

Automated by `setup-machine --only shellfish` once the iPhone side is done.

A Secure ShellFish widget on the iPhone Home Screen, Lock Screen, StandBy or Apple Watch
shows this machine's name, CPU, CPU temperature, memory and disk usage. Cron pushes new
values every 15 minutes; `--minutes` changes that ([Install, disable, uninstall](#install-disable-uninstall)).

Values are green, turn orange at 75% (70°C for Temp) and red at 90% (85°C).

## On the iPhone

1. Connect to the machine in ShellFish (see [04-tailscale.md](04-tailscale.md)).
2. In this server's settings in ShellFish, choose **Install Shell Integration**. It writes
   `~/.shellfishrc` and adds a line to `~/.bashrc` that loads it, which provides the
   `widget` command.
3. Long-press the Home Screen, tap **+**, search for ShellFish and add a widget. Lock
   Screen, StandBy and Watch widgets use the same data.

`~/.shellfishrc` holds the key that encrypts pushes to your phone. Keep it private and
out of any repository.

## On the machine

```
setup-machine --only shellfish
```

The step:

- installs `openssl`, `xxd`, `curl` and `cron` if any are missing. `widget` uses the
  first three to encrypt its message. Without `xxd` it still exits 0, but the phone gets
  nothing it can read.
- copies the widget script from the setup bundle to `~/.local/bin/shellfish_widget.sh`,
  so cron doesn't depend on a checkout. Rerunning the step after a setup update installs
  the new version. A different file already at that path is left alone and reported.
- adds this line to your crontab and keeps the entries already there:

  ```
  */15 * * * * /home/YOU/.local/bin/shellfish_widget.sh --target HOSTNAME >/dev/null 2>&1
  ```

  `--target` sends to this machine's own widget (see [Several machines](#several-machines)).
  An older setup's line that ran the script from `~/tools/nas_scripts` is pointed at the
  installed copy and keeps its arguments.
- sends one update straight away.

Until Shell Integration is installed, the step shows `manual` and only prints the
iPhone instructions.

On the Proxmox host, use `proxmox_setup.sh --only shellfish` instead. See
[pve-host.md](pve-host.md#shellfish-widget).

## Several machines

Every machine with Shell Integration sends to the same phone. Each one sends to the
widget named after its short hostname (`pve1`, `debian-mini`), so the machines don't
replace each other's data. Separate widgets need ShellFish Pro.

On the iPhone, add one widget per machine (stack them to save space). Long-press each →
**Edit Widget** and set its identifier to that machine's hostname.

The setup step picks the name. Change it with `--widget-target`:

```
setup-machine --only shellfish --widget-target nas      # a different identifier
setup-machine --only shellfish --widget-target ''       # the one shared widget, no Pro
```

A widget line without `--target` gets one added. A line you pointed at a target yourself
(`crontab -e`) is left alone.

## By hand

```
sudo apt install -y openssl xxd curl cron
scripts/shellfish_widget.sh --print             # show the values, send nothing
scripts/shellfish_widget.sh                     # send them
scripts/shellfish_widget.sh / /media/Drive1     # one disk entry per mount point
scripts/shellfish_widget.sh --name NAS          # title instead of the short hostname
scripts/shellfish_widget.sh --target pve1       # a specific widget (ShellFish Pro)
```

To show more disks or change how often it runs, edit the entry with `crontab -e`.

`--help` prints all the options.

## Install, disable, uninstall

The script can manage its own cron line, without `setup-machine`:

```
scripts/shellfish_widget.sh --install                  # copy to ~/.local/bin, add the cron line, send once
scripts/shellfish_widget.sh --install / /media/Drive1  # new arguments replace the cron line's
shellfish_widget.sh --install --minutes 5              # send every 5 minutes instead of 15
shellfish_widget.sh --disable                          # turn the widget off on this machine
shellfish_widget.sh --install                          # turn it back on, same arguments
shellfish_widget.sh --uninstall                        # remove the cron line and the copy
```

- `--install` copies the script to `~/.local/bin/shellfish_widget.sh`, or to
  `/usr/local/bin` as root, which are the same paths the setup step uses. It
  checks `~/.shellfishrc`, `openssl`, `xxd`, `curl` and `crontab` before it
  changes anything. Like the setup step, the line sends to the widget named after
  the short hostname. `--target` picks a different widget and `--target ''` the
  one shared widget.
- `--install` with no other arguments also enables a disabled widget and keeps
  its arguments. With arguments, it replaces the widget line.
- `--minutes N` sets the time between runs, 15 by default. N must divide an hour
  (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30) or be whole hours that divide a day (60, 120,
  180, 240, 360, 480, 720, 1440), so the runs stay evenly spaced. With no other
  arguments, `--install --minutes N` changes only the schedule. iOS still limits how
  often the widget redraws (see [Gotchas](#gotchas)).
- `--install` enables and starts the `cron` service (with `sudo` when not root).
  The crontab is saved on disk, so after a reboot or power loss cron runs the widget
  again on its next scheduled run, with no login needed. The setup step checks the
  same thing.
- `--disable` comments the line out with a `#shellfish-disabled# ` prefix. Rerunning
  `setup-machine` reports the step as `skipped` and leaves the widget off until
  `--install` enables it again.
- `--uninstall` removes the widget lines from the crontab, disabled ones included, and
  removes the installed copy. A later `setup-machine --only shellfish` installs it again.
  Use `--disable` to keep a machine's widget off.

The last update stays on the phone after `--disable` or `--uninstall`. Remove that
machine's widget on the iPhone if you no longer want it.

| Item | Source |
|------|--------|
| CPU | busy share of all cores over 1 second, from `/proc/stat` |
| Temp | the CPU package sensor under `/sys/class/hwmon` (`coretemp`, `k10temp`), else the first sensor |
| Mem | `MemTotal` minus `MemAvailable`, from `/proc/meminfo` |
| Disk | `df` use% of each mount point, `/` by default |

Temp is left out in a VM, because a VM can't see the host's sensors. For a real reading,
run the script on bare metal such as `debianbeelink` or the Proxmox host. See the root
[README](../README.md#temperatures) for more on temperatures.

## Gotchas

- **Cron and `~/.bashrc`:** Debian's `~/.bashrc` stops early in non-interactive shells,
  before the line that loads `~/.shellfishrc`. Cron jobs must load it themselves, which
  the script does.
- **`/usr/bin/widget`:** the `perl-tk` package ships a Tk demo with the same name. In a
  shell that hasn't loaded `~/.shellfishrc`, `widget` runs that demo and fails with a
  display error.
- **Throttling:** iOS limits how often widgets update. The Shell Integration log in the
  app shows the stats. Running more often than every 15 minutes doesn't help.
- **Widget name:** the "Widget 1" label comes from the app, not the server. The
  machine name is the first line of the widget's content.
- **Options:** run `widget` with no arguments for the full list. Summary:
  - `50%` or `110/220` shows as progress.
  - Icons are SF Symbols names such as `cpu.fill`.
  - Colors are hex codes like `#f00`; `foreground` switches back to the default.
  - An `https://` link opens when you tap the widget, and `--shortcut Name` runs a
    Shortcut instead.
  - `--image path` shows a local image.
  - `--target id` sends different content to different widgets. It needs ShellFish Pro
    and also raises the daily update budget.
- **Live Activities** show the same content on the Lock Screen and in the Dynamic Island
  while a long task runs.

## References

- https://secureshellfish.app/help/widgets
