# GitHub Actions self-hosted runners

Runners for the **Nicu-Labs** GitHub org (`https://github.com/Nicu-Labs`, with the hyphen).
`scripts/ghrunner_setup.sh` sets one up on any Debian machine or VM, and
`scripts/ghrunner_setup.sh --help` lists every command.

What `install` does:

- Installs what a headless runner VM needs, without recommended packages so nothing else
  (least of all a desktop) comes along:
  - `git` (so `actions/checkout` makes a real clone), `jq`, `unzip`, `zip`, `xz-utils`,
    `curl`, `ca-certificates`;
  - `openssh-server`, enabled and running, so the VM can be reached over SSH;
  - the newest `libicu` apt has (the runner needs it);
  - Docker Engine, Compose and Buildx (Debian's `docker.io`, `docker-compose`,
    `docker-buildx`, plus `apparmor`), or an existing `docker-ce`. Docker is required:
    jobs can use it without sudo because the runner user joins the `docker` group.
  Language toolchains (Node, Python, Go, Java) are not preinstalled: `actions/setup-node`
  and the like download them per workflow.
- Creates the `runner` account when run as root (the runner refuses to run as root).
- Downloads the latest runner release and checks its SHA-256 against the release notes.
- Registers the runner with the org and runs it as a systemd service
  (`actions.runner.Nicu-Labs.<name>.service`), with the needrestart exclusion below.
- Installs `ghrunner-cleanup.timer`, which keeps disk use in check ([below](#disk-cleanup-and-monitoring)).
- Checks each runner and prints the result (see [Checking runners](#checking-runners)).

`install` is idempotent. Every step first looks at what is already there and only does
what is missing, so a rerun on a machine that is set up asks for no token, needs no
sudo, changes nothing, and just prints the check. A runner that is half set up (say the
VM rebooted mid-install, or the service was uninstalled) is completed; a token is only
asked for when a runner still has to be registered.

## One runner per VM

Each runner gets a VM of its own. Runners that share a machine share its Docker daemon,
host ports, `/tmp` and home directories, so concurrent jobs break each other (one job's
`docker compose down` or `docker logout` hits the other). A VM of its own also means a
broken runner is fixed by deleting the VM and cloning a new one.

The script enforces this: `install` will not register a second runner on a machine, and
`check` warns (`WARN`) on older machines that already have several, like `debianbeelink`.

Suggested VM: Debian 13 without a desktop, 2 vCPU, 4 GB RAM, 40 GB disk (thin
provisioned, so only what is written takes space); more CPU and RAM for heavy builds.

Disk, roughly: an idle runner VM is about 3 GB (Debian ~1.5 GB, Docker ~0.3 GB, the
runner ~0.7 GB, logs and apt cache). Building a web app adds ~5-12 GB: the checkout and
`node_modules`, the npm cache, Node from `setup-node`, Playwright browsers, Docker images
(`node:22` alone is ~1.1 GB) and Docker build cache. 40 GB leaves room for peaks, such as
two tool versions during an upgrade, before cleanup's 80% limit (32 GB). 25 GB is a
workable minimum.

## Create a runner VM on Proxmox

On `pve1`, as root, `scripts/proxmox_gh_runner.sh` does the whole thing in one command:
it clones a Debian 13 cloud-image template into a new VM, sizes it, names it (the VM
name becomes the hostname and the runner name), boots it, and runs `ghrunner_setup.sh
install` inside it through the QEMU guest agent. The VM needs no SSH access for this,
and the token is passed on stdin, so it is never written to a file or shown in `ps`.

```sh
# from a checkout on pve1 (it then uses the checkout's ghrunner_setup.sh), or download it
curl -fsSLO https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/proxmox_gh_runner.sh
bash proxmox_gh_runner.sh create --name gh-runner-1                 # asks for the token
bash proxmox_gh_runner.sh create --name gh-runner-2 --cores 4 --memory 8192 --disk 80 --labels heavy
bash proxmox_gh_runner.sh list                                      # runner VMs and their checks
bash proxmox_gh_runner.sh check --name gh-runner-1
bash proxmox_gh_runner.sh destroy --name gh-runner-1                # unregister, then delete the VM
```

| Option | Default | Notes |
|--------|---------|-------|
| `--name` | - | Required. Lowercase letters, digits and `-`; unique in the org. |
| `--token` | asked for | Registration token for `create`, removal token for `destroy`; also `$GHRUNNER_TOKEN`. |
| `--cores` / `--memory` / `--disk` | 2 / 4096 MB / 40 GB | The disk is thin provisioned. |
| `--labels` | - | Extra runner labels, e.g. `heavy` for big builds. |
| `--storage` / `--bridge` | `local-lvm` / `vmbr0` | Where the disks go and which network the VM joins. |
| `--ssh-keys` | `/root/.ssh/authorized_keys` | Required: public keys for the VM's `debian` user, the only way to log in (SSH key, no password). |
| `--template-id` | 9100 | The template VM, built on first `create` (or with `template`). |
| `--rebuild` | - | With `template`: replace the template with one from the current Debian image. |
| `--yes` | - | `destroy` without typing the name to confirm. |

- **The OS:** nothing is installed. Every VM is a full clone of template VM 9100, whose
  disk is Debian 13's official cloud image (`debian-13-genericcloud-amd64.qcow2` from
  cloud.debian.org, checked against its `SHA512SUMS`): Debian already installed, ready for
  cloud-init: a headless server with the SSH server, no desktop. On first boot cloud-init
  sets the hostname, network and SSH keys and installs the guest agent. The template is a snapshot of the image on the day it was
  built; `template --rebuild` refreshes it for later VMs. Existing VMs are unaffected, and
  runner setup updates packages in each VM anyway.
- **Prerequisites** are set up as needed: `curl` on the host if missing, and the
  `snippets` content type on the `local` storage (cloud-init's vendor snippet that
  installs the guest agent lives there). Inside the VM, `ghrunner_setup.sh` installs its own.
- **Safe to rerun**, including after an interrupted run:
  - a half-built template is removed and built again;
  - a clone interrupted before it was configured is finished;
  - a stopped VM is started, and its runner set up or, if it already is, just checked;
  - on an existing VM only the `--cores`/`--memory`/`--disk` given are applied (CPU and
    memory at the next reboot), and a disk only grows.
- **Only its own VMs:** runner VMs are tagged `gh-runner` and the template
  `gh-runner-template`. Any other VM (a name clash, a foreign VM at the template ID,
  two VMs with the same name) stops the script rather than being changed or deleted.
- **Destroy:** it unregisters the runner from GitHub first (removal token from the
  runner's **...** menu > **Remove**). A stopped VM is deleted without unregistering, so
  remove the runner on GitHub yourself.
- **Output:** the guest agent returns output when a command finishes, so the runner
  install is quiet for a few minutes, then prints everything at once.

Other hypervisors, or a VM made by hand: install Debian 13 (see
[docs/01-debian-install.md](docs/01-debian-install.md)) **without a desktop**, ticking
only *SSH server* and *standard system utilities*, give it a unique hostname
(`sudo hostnamectl set-hostname gh-runner-3`), since it becomes the runner name and
registration fails if the name is taken, then set up the runner as below.

## Set up a runner

1. Get a registration token: as an org admin, open
   <https://github.com/organizations/Nicu-Labs/settings/actions/runners/new> and copy
   the value after `--token` in the `config.sh` line. One token works for an hour, on as
   many VMs as you set up in that time; after that, reload the page for a new one.
2. On the machine (a fresh VM needs only root and network):

   ```sh
   curl -fsSLO https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/ghrunner_setup.sh
   bash ghrunner_setup.sh install --token AAAA...    # the runner is named after the host
   ```

   Leave out `--token` and the script asks for it (input hidden), which keeps it out of
   shell history; `GHRUNNER_TOKEN=AAAA...` in the environment works too. From a checkout,
   `bash scripts/ghrunner_setup.sh install ...` does the same.
3. The runner appears as **Idle** on the org's runners page.
4. Add the machine and runner name to [Local Machines](README.md#local-machines).

### Inputs

| Option | Required | Default | Notes |
|--------|----------|---------|-------|
| `--token` | yes | asked for | From step 1. Also read from `$GHRUNNER_TOKEN`, or asked for on the terminal. With no terminal either, the script tries `gh api` as a last resort, which needs `gh auth refresh -s admin:org` first. |
| `--url` | no | `https://github.com/Nicu-Labs` | The org. A repo URL (`https://github.com/Nicu-Labs/REPO`) makes a runner for that repo only. |
| `--name` | no | hostname | Must be unique in the org; registration fails if it is taken. Prefer setting the hostname instead. |
| `--labels` | no | - | Extra labels for `runs-on:`, comma separated. `self-hosted`, `linux` and `X64`/`ARM64` are always added. |
| `--user` | no | `runner` as root, else you | The account the service runs as. Never `root`. |
| `--dir` | no | `~USER/actions-runner-NAME` | Where the runner lives. |

Run as root, or as the runner user with sudo. A workflow picks the runner with
`runs-on: self-hosted` or with its labels, e.g. `runs-on: [self-hosted, docker]`.

### Managing a runner

Pass the same `--name` (and `--user` or `--url` if they were not the defaults) used at install:

```sh
bash ghrunner_setup.sh status --name build-vm-1
bash ghrunner_setup.sh stop --name build-vm-1        # start works the same
bash ghrunner_setup.sh uninstall --name build-vm-1   # remove the service, keep the registration
bash ghrunner_setup.sh unregister --name build-vm-1 --token BBBB...
```

`unregister` also removes the runner from the org; its token is the removal token from the
runner's **...** menu > **Remove** on the org runners page (or fetched with `gh`). Delete the
runner directory afterwards if the machine is staying.

### Checking runners

```sh
bash ghrunner_setup.sh check                  # every runner service on this machine
bash ghrunner_setup.sh check --name build-vm-1
```

```
== Runner build-vm-1 (/home/runner/actions-runner-build-vm-1)
  ok    registered as build-vm-1 with https://github.com/Nicu-Labs
  ok    service actions.runner.Nicu-Labs.build-vm-1.service running
  ok    docker running
  ok    runner in the docker group
  ok    cleanup timer active, next Wed 2026-09-23 15:07:12 PDT
  ok    disk 24% used
```

A `WARN` line flags a machine with more than one runner. A `FAIL` line names what is wrong, and the command (like `install`) exits 1. Rerunning
`install` with the same options fixes anything it can. The check sees this machine only;
whether GitHub shows the runner as **Idle** is on the org's runners page.

### If jobs are not picked up

- **Public repos:** new org runners join the **Default** runner group, which does not serve
  public repositories until **Allow public repositories** is ticked in
  *Settings > Actions > Runner groups > Default*.
- **Labels:** every label in `runs-on:` must be on the runner.
- **Service:** `bash ghrunner_setup.sh check`, then `status --name NAME` and the runner's
  logs in `DIR/_diag`.

## Disk cleanup and monitoring

`ghrunner-cleanup.timer` runs hourly (as root, from the copy of the script in
`/usr/local/sbin`) and covers every runner service on the machine:

- Deletes runner logs (`_diag`) and job workspaces (`_work/*`) untouched for
  `GHRUNNER_KEEP_DAYS` (7) days. A removed workspace is only cloned again on the next job.
- Deletes files in the runner user's tool caches (`~/.cache`: Playwright browsers, pip,
  node-gyp, Go; and `~/.npm`) that no job has read for the same number of days, so old
  tool versions go while the ones in use stay.
- Prunes Docker images no container uses, and build cache, older than the same age.
  Containers and volumes are never touched, so other services on the machine keep their data.
- When a disk the runners use is at `GHRUNNER_DISK_LIMIT` (80%) or more, clears every
  idle workspace, the tool caches and every unused image regardless of age, then writes a warning to the
  journal if the disk is still over the limit.
- Leaves a runner's workspaces alone while it has a job running, and skips tool caches and
  Docker while any job is running.

Anything removed is only downloaded or cloned again by the next job that needs it.

```sh
sudo ghrunner_setup.sh report                     # disk, busy/idle and size per runner, docker df
sudo ghrunner_setup.sh cleanup --dry-run          # what a run would remove
journalctl -u ghrunner-cleanup                    # what past runs removed, and warnings
systemctl list-timers ghrunner-cleanup.timer      # next run
```

To change the limits, put them in `/etc/default/ghrunner-cleanup`:

```sh
GHRUNNER_KEEP_DAYS=3
GHRUNNER_DISK_LIMIT=70
```

## systemd service reference

The rest of this page is GitHub's reference for the service, which the script automates.

https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application?platform=linux

You must add a runner to GitHub before you can configure the self-hosted runner application as a service. For more information, see Adding self-hosted runners.
For Linux systems that use systemd, you can use the svc.sh script that is created after successfully adding the runner to install and manage using the application as a service.

On the runner machine, open a shell in the directory where you installed the self-hosted runner application. Use the commands below to install and manage the self-hosted runner service.

Installing the service

Stop the self-hosted runner application if it is currently running.

Install the service with the following command:

sudo ./svc.sh install
Alternatively, the command takes an optional user argument to install the service as a different user.

./svc.sh install USERNAME
Starting the service

Start the service with the following command:

sudo ./svc.sh start
Note

On Debian-based Linux systems (such as Debian or Ubuntu) with needrestart enabled, you can prevent needrestart from restarting the runner service during a workflow job by configuring it to ignore the runner service. Run the following command:

echo '$nrconf{override_rc}{qr(^actions\.runner\..+\.service$)} = 0;' | sudo tee /etc/needrestart/conf.d/actions_runner_services.conf
Checking the status of the service

Check the status of the service with the following command:

sudo ./svc.sh status
For more information on viewing the status of your self-hosted runner, see Monitoring and troubleshooting self-hosted runners.

Stopping the service

Stop the service with the following command:

sudo ./svc.sh stop
Uninstalling the service

Stop the service if it is currently running.

Uninstall the service with the following command:

sudo ./svc.sh uninstall
Customizing the self-hosted runner service

If you don't want to use the above default systemd service configuration, you can create a customized service or use whichever service mechanism you prefer. Consider using the serviced template at actions-runner/bin/actions.runner.service.template as a reference. If you use a customized service, the self-hosted runner service must always be invoked using the runsvc.sh entry point.