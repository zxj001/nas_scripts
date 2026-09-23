#!/usr/bin/env bash
# ghrunner_setup.sh - install a GitHub Actions self-hosted runner and run it as
# a systemd service (GITHUB_RUNNER.md), on any Debian machine or fresh VM.
# Rerunning install is safe: it only adds what is missing (no token needed
# once registered) and ends by checking each runner, exiting 1 on a problem.
# Run it as root (the runner user is created) or as the runner user with sudo.
# Prerequisites (curl, libicu, Docker) are installed with apt, and the runner
# user joins the docker group so jobs can use Docker. Each runner lives in its
# own directory. One runner per VM: install will not add a second runner to a
# machine, and check warns about machines that already have several.
#
#   # fresh VM: token from GitHub > Settings > Actions > Runners > New runner
#   curl -fsSLO https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/ghrunner_setup.sh
#   bash ghrunner_setup.sh install --token AAAA...
#
#   scripts/ghrunner_setup.sh install                    # asks for the token
#   scripts/ghrunner_setup.sh install --name build-vm-1 --labels docker
#   scripts/ghrunner_setup.sh install --url https://github.com/OWNER/REPO
#   scripts/ghrunner_setup.sh check                 # every runner here: registered,
#                                                   # running, docker, timer, disk
#   scripts/ghrunner_setup.sh status    --name build-vm-1
#   scripts/ghrunner_setup.sh start|stop --name build-vm-1
#   scripts/ghrunner_setup.sh uninstall --name build-vm-1   # service only
#   scripts/ghrunner_setup.sh unregister --name build-vm-1   # remove from GitHub
#   sudo scripts/ghrunner_setup.sh report           # disk use of every runner here
#   sudo scripts/ghrunner_setup.sh cleanup [--dry-run]
#
# install also sets up ghrunner-cleanup.timer, which runs `cleanup` hourly for
# every runner on the machine: runner logs and idle job workspaces older than
# GHRUNNER_KEEP_DAYS (7) go, as do unused Docker images and build cache. Above
# GHRUNNER_DISK_LIMIT (80%) full it also clears all idle workspaces and unused
# images, and warns in the journal if that is not enough. Set both in
# /etc/default/ghrunner-cleanup; `journalctl -u ghrunner-cleanup` shows runs.
#
# Options: --url (default: https://github.com/Nicu-Labs, the org; a repo URL
# makes a runner for that repo only), --name (default: hostname), --user
# (runner account; default: you, or `runner` when run as root), --dir
# (default: ~USER/actions-runner-NAME), --labels (comma separated, added to
# self-hosted,linux,ARCH), --token (a registration or removal token from the
# runners page; also read from $GHRUNNER_TOKEN, asked for when missing, or as a
# last resort requested with `gh`, which needs admin:org).
set -euo pipefail
# dockerd, ldconfig and usermod live here, off a normal Debian user's PATH.
PATH=$PATH:/usr/sbin:/sbin

usage() {
    sed -n '2,41p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
    echo "ghrunner_setup: $*" >&2
    exit 1
}

# Commands for root: directly when we are root, otherwise through sudo.
as_root() {
    if [[ $EUID -eq 0 ]]; then
        "$@"
    else
        command -v sudo >/dev/null || die "sudo is not installed; run this as root instead"
        sudo "$@"
    fi
}

# Commands for the runner account (config.sh refuses to run as root).
as_runner() {
    if [[ $(id -un) == "$RUNNER_USER" ]]; then
        "$@"
    else
        as_root runuser -u "$RUNNER_USER" -- "$@"
    fi
}

ensure_user() {
    id "$RUNNER_USER" >/dev/null 2>&1 && return 0
    echo "Creating user $RUNNER_USER"
    as_root useradd --create-home --shell /bin/bash "$RUNNER_USER"
}

# curl for the download, and the newest libicu this Debian release ships
# (the runner's own installdependencies.sh lags behind new releases).
ensure_packages() {
    local icu="" need=()
    command -v curl >/dev/null || need+=(curl ca-certificates)
    if ! ldconfig -p | grep -q libicu; then
        as_root apt-get update -qq
        icu=$(apt-cache pkgnames libicu | grep -E '^libicu[0-9]+$' | sort -V | tail -n1)
        [[ -n $icu ]] || die "no libicu package found in apt"
        need+=("$icu")
    fi
    ((${#need[@]})) || return 0
    echo "Installing ${need[*]}"
    [[ -n $icu ]] || as_root apt-get update -qq
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${need[@]}" >/dev/null
}

# Docker Engine and Compose from Debian (as setup_tasks/docker.py installs
# them), unless Docker's own docker-ce is already there. The runner user joins
# the docker group; a running runner service is stopped so the start below
# picks the membership up.
ensure_docker() {
    local need=()
    command -v dockerd >/dev/null || need+=(docker.io)
    command -v docker >/dev/null || need+=(docker-cli)
    if ! docker compose version >/dev/null 2>&1; then
        if dpkg-query -W -f='${Status}' docker-ce 2>/dev/null | grep -q 'install ok installed'; then
            die "Docker CE is installed without Compose; install docker-compose-plugin from Docker's apt repository"
        fi
        need+=(docker-compose)
    fi
    if ((${#need[@]})); then
        echo "Installing ${need[*]}"
        as_root apt-get update -qq
        as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${need[@]}" >/dev/null
    fi
    if ! { systemctl is-enabled --quiet docker && systemctl is-active --quiet docker; }; then
        as_root systemctl enable --now docker
    fi
    if ! id -nG "$RUNNER_USER" | tr ' ' '\n' | grep -qx docker; then
        echo "Adding $RUNNER_USER to the docker group"
        as_root usermod -aG docker "$RUNNER_USER"
        if service_installed; then svc stop; fi
    fi
}

# https://github.com/OWNER -> orgs/OWNER, https://github.com/OWNER/REPO -> repos/OWNER/REPO
api_scope() {
    local path=${URL#https://github.com/}
    path=${path%/}
    case $path in
        */*/*|'') die "--url must be https://github.com/OWNER or https://github.com/OWNER/REPO" ;;
        */*) echo "repos/$path" ;;
        *) echo "orgs/$path" ;;
    esac
}

# The org's or repo's runners page, where tokens are shown.
runners_page() {
    local scope
    scope=$(api_scope)
    case $scope in
        orgs/*) echo "https://github.com/organizations/${scope#orgs/}/settings/actions/runners" ;;
        *) echo "https://github.com/${scope#repos/}/settings/actions/runners" ;;
    esac
}

# Registration or removal token: --token, then $GHRUNNER_TOKEN, then asked for
# on the terminal. One registration token serves every runner set up within
# its hour. Without a terminal, `gh` is tried as a last resort.
get_token() {
    local kind=$1 token=${TOKEN:-${GHRUNNER_TOKEN:-}}
    if [[ -z $token ]] && { : </dev/tty; } 2>/dev/null; then
        if [[ $kind == registration ]]; then
            echo "Registration token: $(runners_page)/new > copy the value after --token" >/dev/tty
        else
            echo "Removal token: $(runners_page) > the runner's ... menu > Remove" >/dev/tty
        fi
        read -rsp "Token: " token </dev/tty
        echo >/dev/tty
    fi
    if [[ -z $token ]] && command -v gh >/dev/null; then
        token=$(gh api -X POST "$(api_scope)/actions/runners/$kind-token" --jq .token) \
            || die "could not get a $kind token from gh (needs admin:org); pass --token"
    fi
    [[ -n $token ]] || die "no $kind token; pass --token (see $(runners_page))"
    echo "$token"
}

runner_arch() {
    case $(uname -m) in
        x86_64) echo x64 ;;
        aarch64|arm64) echo arm64 ;;
        armv7l) echo arm ;;
        *) die "unsupported architecture $(uname -m)" ;;
    esac
}

# Latest actions/runner release, checked against the SHA-256 in its release notes.
download_runner() {
    local arch release version tarball sha
    arch=$(runner_arch)
    release=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest)
    version=$(grep -o '"tag_name": *"v[^"]*"' <<<"$release" | grep -o '[0-9][0-9.]*')
    [[ -n $version ]] || die "could not find the latest runner version"
    sha=$(grep -o "BEGIN SHA linux-$arch -->[0-9a-f]\{64\}" <<<"$release" | grep -o '[0-9a-f]\{64\}$' || true)
    [[ -n $sha ]] || die "no checksum for linux-$arch in runner v$version release notes"
    tarball=actions-runner-linux-$arch-$version.tar.gz

    echo "Downloading runner v$version ($arch) into $DIR"
    as_runner mkdir -p "$DIR"
    as_runner curl -fsSL -o "$DIR/$tarball" \
        "https://github.com/actions/runner/releases/download/v$version/$tarball"
    echo "$sha  $DIR/$tarball" | sha256sum -c --quiet - || die "checksum mismatch for $tarball"
    as_runner tar -xzf "$DIR/$tarball" -C "$DIR"
    as_runner rm "$DIR/$tarball"
    # Remaining libraries (libssl, krb5, zlib, lttng); apt skips what is there.
    as_root "$DIR/bin/installdependencies.sh" >/dev/null \
        || echo "warning: bin/installdependencies.sh failed; the runner may not start" >&2
}

# Keep needrestart from restarting the runner in the middle of a job.
exclude_from_needrestart() {
    [[ -d /etc/needrestart/conf.d ]] || return 0
    local conf=/etc/needrestart/conf.d/actions_runner_services.conf
    [[ -f $conf ]] && return 0
    echo "Excluding runner services from needrestart"
    # shellcheck disable=SC2016  # the $nrconf is Perl, written literally
    echo '$nrconf{override_rc}{qr(^actions\.runner\..+\.service$)} = 0;' | as_root tee "$conf" >/dev/null
}

svc() {
    [[ -x $DIR/svc.sh ]] || die "no runner in $DIR (see --dir / --name)"
    (cd "$DIR" && as_root ./svc.sh "$@")
}

service_installed() {
    runner_has .service
}

# Every runner service on this machine, as its directory.
runner_dirs() {
    local unit
    systemctl list-unit-files --no-legend 'actions.runner.*.service' | awk '{print $1}' \
        | while read -r unit; do systemctl show -P WorkingDirectory "$unit"; done
}

# A job is running when the runner has a worker process.
runner_busy() {
    pgrep -f -- "$1/bin/Runner.Worker" >/dev/null
}

disk_percent() {
    df --output=pcent "$1" | tail -n1 | tr -dc 0-9
}

remove() {
    if ((DRY_RUN)); then echo "  would remove $1"; else echo "  removing $1"; rm -rf -- "$1"; fi
}

# Runner logs older than $1 days, and job workspaces untouched that long (all
# of them when $2 is set). A removed workspace is only cloned again next job.
clean_runner() {
    local dir=$1 days=$2 all=$3 path
    echo "$dir"
    find "$dir/_diag" -type f -mtime "+$days" -print0 2>/dev/null \
        | while IFS= read -r -d '' path; do remove "$path"; done || true
    if runner_busy "$dir"; then
        echo "  job running, workspaces left alone"
        return
    fi
    [[ -d $dir/_work ]] || return 0
    if ((all)); then
        find "$dir/_work" -mindepth 1 -maxdepth 1 -print0
    else
        find "$dir/_work" -mindepth 1 -maxdepth 1 -mtime "+$days" -print0
    fi | while IFS= read -r -d '' path; do remove "$path"; done
}

# Images no container uses, and build cache. Containers and volumes are never
# touched, so other services on the machine keep their data.
clean_docker() {
    local days=$1 all=$2 filter=()
    command -v docker >/dev/null || return 0
    ((all)) || filter=(--filter "until=$((days * 24))h")
    if ((DRY_RUN)); then
        echo "docker: would prune unused images and build cache ${filter[*]}"
        return
    fi
    echo "docker: pruning unused images and build cache ${filter[*]}"
    docker image prune -af "${filter[@]}" | tail -n1
    docker builder prune -af "${filter[@]}" | tail -n1
}

cmd_report() {
    local dir state
    df -h --output=target,size,used,avail,pcent / | sed 1d | sed 's/^/disk /'
    while read -r dir; do
        [[ -n $dir ]] || continue
        state=idle
        runner_busy "$dir" && state=busy
        printf '%-6s %6s  %s\n' "$state" "$(du -sh "$dir" 2>/dev/null | cut -f1)" "$dir"
    done < <(runner_dirs)
    if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
        docker system df
    fi
}

cmd_cleanup() {
    [[ $EUID -eq 0 ]] || die "cleanup needs root (sudo)"
    local days=${GHRUNNER_KEEP_DAYS:-7} limit=${GHRUNNER_DISK_LIMIT:-80}
    local dirs dir used all=0 idle=1
    mapfile -t dirs < <(runner_dirs)
    for dir in "${dirs[@]}"; do
        [[ -n $dir ]] || continue
        runner_busy "$dir" && idle=0
        used=$(disk_percent "$dir")
        ((used < limit)) || all=1
    done
    used=$(disk_percent /)
    ((used < limit)) || all=1
    ((all)) && echo "disk at or above ${limit}%: clearing all idle workspaces and unused images"

    for dir in "${dirs[@]}"; do
        [[ -n $dir ]] && clean_runner "$dir" "$days" "$all"
    done
    # A job may have just pulled an image, so leave Docker until all are idle.
    if ((idle)); then clean_docker "$days" "$all"; else echo "docker: a job is running, skipped"; fi

    cmd_report
    for dir in / "${dirs[@]}"; do
        [[ -n $dir ]] || continue
        used=$(disk_percent "$dir")
        if ((used >= limit)); then
            echo "WARNING: $(df --output=target "$dir" | tail -n1) is ${used}% full after cleanup" >&2
            logger -p user.warning -t ghrunner-cleanup "$(df --output=target "$dir" | tail -n1) is ${used}% full after cleanup"
        fi
    done
}

# Copy this script to /usr/local/sbin and run its cleanup hourly. Files are
# only written when they differ, so a rerun changes nothing.
install_cleanup_timer() {
    local bin=/usr/local/sbin/ghrunner_setup.sh changed=0 service timer
    service="[Unit]
Description=Clean up GitHub Actions runner workspaces, logs and Docker images

[Service]
Type=oneshot
EnvironmentFile=-/etc/default/ghrunner-cleanup
ExecStart=$bin cleanup
Nice=19
IOSchedulingClass=idle"
    timer="[Unit]
Description=Hourly GitHub Actions runner cleanup

[Timer]
OnCalendar=hourly
RandomizedDelaySec=10m
Persistent=true

[Install]
WantedBy=timers.target"
    if ! cmp -s "$0" "$bin"; then
        as_root install -m 755 "$0" "$bin"
    fi
    if [[ $(cat /etc/systemd/system/ghrunner-cleanup.service 2>/dev/null) != "$service" ]]; then
        as_root tee /etc/systemd/system/ghrunner-cleanup.service <<<"$service" >/dev/null
        changed=1
    fi
    if [[ $(cat /etc/systemd/system/ghrunner-cleanup.timer 2>/dev/null) != "$timer" ]]; then
        as_root tee /etc/systemd/system/ghrunner-cleanup.timer <<<"$timer" >/dev/null
        changed=1
    fi
    if ((changed)); then
        echo "Installing ghrunner-cleanup.timer"
        as_root systemctl daemon-reload
    fi
    if ! { systemctl is-enabled --quiet ghrunner-cleanup.timer && systemctl is-active --quiet ghrunner-cleanup.timer; }; then
        as_root systemctl enable --now ghrunner-cleanup.timer
    fi
}

# A file in the runner directory, read as the runner user (its home may be private).
runner_has() {
    as_runner test -e "$DIR/$1" 2>/dev/null
}

runner_file() {
    as_runner cat "$DIR/$1" 2>/dev/null | tr -d '\357\273\277'
}

# One line per check of runner $DIR; returns the number of problems.
check_runner() {
    local problems=0 url name unit used limit=${GHRUNNER_DISK_LIMIT:-80}
    ok() { echo "  ok    $*"; }
    bad() { echo "  FAIL  $*"; problems=$((problems + 1)); }
    echo "== Runner $NAME ($DIR)"
    if ! runner_has config.sh; then
        bad "not installed here"
        return 1
    fi
    if runner_has .runner; then
        url=$(runner_file .runner | grep -o '"gitHubUrl": *"[^"]*"' | cut -d'"' -f4)
        name=$(runner_file .runner | grep -o '"agentName": *"[^"]*"' | cut -d'"' -f4)
        ok "registered as $name with $url"
        [[ -z $URL || $url == "$URL" ]] || bad "registered with $url, not $URL"
    else
        bad "not registered with GitHub"
    fi
    if runner_has .service; then
        unit=$(runner_file .service)
        if systemctl is-active --quiet "$unit"; then
            ok "service $unit running"
        else
            bad "service $unit is $(systemctl is-active "$unit")"
        fi
    else
        bad "no systemd service"
    fi
    if runner_busy "$DIR"; then ok "running a job"; fi
    if systemctl is-active --quiet docker; then ok "docker running"; else bad "docker not running"; fi
    if id -nG "$RUNNER_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
        ok "$RUNNER_USER in the docker group"
    else
        bad "$RUNNER_USER not in the docker group"
    fi
    if systemctl is-active --quiet ghrunner-cleanup.timer; then
        ok "cleanup timer active, next $(systemctl show -P NextElapseUSecRealtime ghrunner-cleanup.timer)"
    else
        bad "ghrunner-cleanup.timer not active"
    fi
    local runners
    runners=$(runner_dirs | grep -c . || true)
    if ((runners > 1)); then
        echo "  WARN  $runners runners on this machine; one per VM keeps jobs from colliding"
    fi
    used=$(disk_percent "$DIR")
    if ((used < limit)); then ok "disk ${used}% used"; else bad "disk ${used}% used (limit ${limit}%)"; fi
    return "$problems"
}

# Set up runner $NAME in $DIR with registration token $1.
install_runner() {
    local token=$1
    ensure_packages
    ensure_user
    runner_has config.sh || download_runner
    ensure_docker

    if ! runner_has .runner; then
        local labels=${LABELS:+--labels $LABELS}
        # shellcheck disable=SC2086  # $labels is empty or two words
        # No --replace: a name already in use (say a cloned VM that kept its
        # hostname) fails here instead of taking over the other runner.
        (cd "$DIR" && as_runner ./config.sh --unattended --url "$URL" \
            --token "$token" --name "$NAME" --work _work $labels)
    fi

    exclude_from_needrestart
    runner_has .service || svc install "$RUNNER_USER"
    systemctl is-active --quiet "$(runner_file .service)" || svc start
    install_cleanup_timer
}

# Set up whatever is missing, then check the runner. A runner that is already
# registered needs no token, so a rerun only inspects and reports. A machine
# gets one runner: runners sharing a VM share its Docker daemon, ports and
# home directories, and break each other's jobs.
cmd_install() {
    local token='' dir
    if ! runner_has .runner; then
        while read -r dir; do
            [[ -z $dir || $dir == "$DIR" ]] && continue
            die "this machine already has a runner in $dir; set up one runner per VM (check shows it)"
        done < <(runner_dirs)
        token=$(get_token registration)
    fi
    install_runner "$token"
    check_runner
}

# Check the --name runner, or every runner service on this machine.
cmd_check() {
    local dirs dir problems=0
    if ((NAME_SET || DIR_SET)); then
        check_runner
        return
    fi
    mapfile -t dirs < <(runner_dirs)
    ((${#dirs[@]})) || die "no runner services on this machine"
    URL=''
    for dir in "${dirs[@]}"; do
        DIR=$dir NAME=${dir##*/actions-runner-}
        RUNNER_USER=$(stat -c %U "$dir")
        check_runner || problems=$((problems + 1))
    done
    ((problems == 0))
}

cmd_uninstall() {
    service_installed || die "no service installed for $DIR"
    svc stop
    svc uninstall
}

cmd_unregister() {
    local token
    token=$(get_token remove)
    if service_installed; then cmd_uninstall; fi
    (cd "$DIR" && as_runner ./config.sh remove --token "$token")
}

COMMAND=${1:-}
[[ -n $COMMAND ]] && shift
URL=https://github.com/Nicu-Labs NAME=$(hostname) DIR='' LABELS='' TOKEN='' RUNNER_USER='' DRY_RUN=0 NAME_SET=0
while (($#)); do
    case $1 in
        --url) URL=$2; shift 2 ;;
        --name) NAME=$2; NAME_SET=1; shift 2 ;;
        --dir) DIR=$2; shift 2 ;;
        --user) RUNNER_USER=$2; shift 2 ;;
        --labels) LABELS=$2; shift 2 ;;
        --token) TOKEN=$2; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option $1 (see --help)" ;;
    esac
done
if [[ -z $RUNNER_USER ]]; then
    if [[ $EUID -eq 0 ]]; then RUNNER_USER=runner; else RUNNER_USER=$(id -un); fi
fi
[[ $RUNNER_USER != root ]] || die "the runner cannot run as root; pick another --user"

runner_dir() {
    local home
    home=$(getent passwd "$RUNNER_USER" | cut -d: -f6 || true)
    echo "${home:-/home/$RUNNER_USER}/actions-runner-$1"
}
DIR_SET=0
if [[ -n $DIR ]]; then DIR_SET=1; else DIR=$(runner_dir "$NAME"); fi

case $COMMAND in
    install) cmd_install ;;
    start|stop|status) svc "$COMMAND" ;;
    uninstall) cmd_uninstall ;;
    unregister) cmd_unregister ;;
    check) cmd_check ;;
    report) cmd_report ;;
    cleanup) cmd_cleanup ;;
    -h|--help|'') usage ;;
    *) die "unknown command $COMMAND (see --help)" ;;
esac
