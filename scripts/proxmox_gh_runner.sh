#!/usr/bin/env bash
# proxmox_gh_runner.sh - on the Proxmox host, create a Debian 13 VM that runs
# one GitHub Actions runner for an org or a repo (GITHUB_RUNNER.md). The VM is cloned
# from a cloud-image template, named and sized by cloud-init, and set up by
# running scripts/ghrunner_setup.sh inside it through the QEMU guest agent,
# so it needs no SSH access and the token never lands in a file.
#
#   scripts/proxmox_gh_runner.sh create --name gh-runner-1 --org Nicu-Labs  # asks for the token
#   scripts/proxmox_gh_runner.sh create --name gh-runner-2 --repo OWNER/REPO
#   scripts/proxmox_gh_runner.sh create --name gh-runner --count 3 --org Nicu-Labs
#       # 3 new VMs gh-runner-N (the first free numbers), set up in parallel
#   scripts/proxmox_gh_runner.sh create --name gh-runner-3 --org Nicu-Labs --cores 4 \
#       --memory 8192 --disk 80 --labels heavy --token AAAA...
#   scripts/proxmox_gh_runner.sh list                  # runner VMs and their checks
#   scripts/proxmox_gh_runner.sh check --name gh-runner-1
#   scripts/proxmox_gh_runner.sh destroy --name gh-runner-1   # unregister, delete VM
#   scripts/proxmox_gh_runner.sh template [--rebuild]  # build (or rebuild) the template
#
# Every command is safe to rerun. create picks up where an interrupted run
# stopped (a half-built template is rebuilt, a half-configured clone is
# finished), starts a stopped VM, and sets up or just checks its runner; on an
# existing VM only the --cores/--memory/--disk you pass are applied, and disks
# only grow. Before creating VMs it checks the host has the RAM to start them
# and the disk for them, and warns when their full (thin) disks would not fit. Missing host prerequisites are installed: curl, and the `snippets`
# content type on `local` (for the cloud-init that installs the guest agent).
#
# The OS: VMs are full clones of a template (VM 9100, built on first use) whose
# disk is Debian 13's genericcloud image, checked against its SHA512SUMS. The
# template is a snapshot of that day's image; `template --rebuild` refreshes it
# for VMs created later (runner setup updates packages in every VM anyway).
#
# Options: --name (VM name, which becomes the hostname and the runner name;
# with --count N, create makes N new VMs NAME-1, NAME-2, ..., skipping names
# any VM in the cluster already has),
# --org ORG or --repo OWNER/REPO (create needs one: where the runner
# registers, every repo of an org or just one repo),
# --token (registration token for create, removal token for destroy; also
# $GHRUNNER_TOKEN, else asked for), --cores (2), --memory MB (4096), --disk GB
# (40), --labels (extra runner labels), --storage (local-lvm), --bridge
# (vmbr0), --ssh-keys (file of public keys for the VM's `debian` user,
# required as SSH with a key is the only login; default
# /root/.ssh/authorized_keys), --template-id (9100), --yes (destroy without
# asking), --rebuild (template: replace the existing one).
set -euo pipefail

TAG=gh-runner
IMAGE_URL=https://cloud.debian.org/images/cloud/trixie/latest
IMAGE=debian-13-genericcloud-amd64.qcow2
SNIPPET=gh-runner-vendor.yaml
MIN_FREE_MB=2048   # RAM left for the host after starting VMs
NEW_VM_GB=4        # disk a new VM takes at first: Debian, Docker, the runner
SETUP_URL=https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/ghrunner_setup.sh

usage() {
    sed -n '2,43p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
    echo "proxmox_gh_runner: $*" >&2
    exit 1
}

# The VM ID of the VM called $1 on this node, or nothing.
vm_id() {
    local ids
    ids=$(qm list | awk -v name="$1" 'NR > 1 && $2 == name { print $1 }')
    [[ $(wc -w <<<"$ids") -le 1 ]] || die "several VMs are called $1 (${ids//$'\n'/ }); rename all but one"
    echo "$ids"
}

is_template() {
    qm config "$1" </dev/null | grep -q '^template: 1'
}

# Disk size of scsi0 in whole GB.
disk_gb() {
    qm config "$1" </dev/null | awk -F'size=' '/^scsi0:/ {
        split($2, a, ","); n = a[1] + 0; u = substr(a[1], length(a[1]))
        if (u == "T") n *= 1024; else if (u == "M") n /= 1024; else if (u == "K") n /= 1048576
        printf "%d\n", n
    }'
}

# curl for the image download; everything else ships with Proxmox VE.
ensure_host_tools() {
    command -v curl >/dev/null && return 0
    echo "Installing curl"
    apt-get install -y -qq curl >/dev/null 2>&1 || {
        apt-get update -qq || true   # an unlicensed enterprise repo fails; the rest still updates
        apt-get install -y -qq curl >/dev/null
    }
}

check_host() {
    pvesm status | awk 'NR > 1 { print $1 }' | grep -qx "$STORAGE" || die "no storage $STORAGE (see pvesm status)"
    ip link show "$BRIDGE" >/dev/null 2>&1 || die "no bridge $BRIDGE (see ip -br link)"
    perl -MJSON::PP -e1 2>/dev/null || die "perl JSON::PP is missing; is this a Proxmox VE host?"
    ensure_host_tools
}

vm_status() {
    qm status "$1" | awk '{ print $2 }'
}

# The name of every VM and container in the cluster, one per line (all of
# them, not just this node's, so a new name is unique cluster-wide).
vm_names() {
    pvesh get /cluster/resources --type vm --output-format json | perl -MJSON::PP -e '
        print "$_->{name}\n" for grep { defined $_->{name} } @{ decode_json(join "", <STDIN>) }'
}

has_tag() {
    qm config "$1" </dev/null | awk '/^tags:/ { print $2 }' | tr ';,' '\n' | grep -qx "$2"
}

# Run a command in VM $1 through the guest agent and print its output. Stdin
# is passed through when $2 is "stdin". Returns the command's exit code.
guest() {
    local vmid=$1 stdin=0 json
    shift
    if [[ $1 == stdin ]]; then
        stdin=1
        shift
    fi
    if ((stdin)); then
        json=$(qm guest exec "$vmid" --timeout 3600 --pass-stdin 1 -- "$@")
    else
        json=$(qm guest exec "$vmid" --timeout 3600 -- "$@" </dev/null)
    fi
    # Proxmox is written in Perl, so JSON::PP is always there.
    perl -MJSON::PP -e '
        my $r = decode_json(join "", <STDIN>);
        for (["out-data", *STDOUT], ["err-data", *STDERR]) {
            my ($key, $fh) = @$_;
            my $text = $r->{$key} // next;
            $text .= "\n" unless $text =~ /\n\z/;
            print {$fh} $text;
        }
        exit(exists $r->{exitcode} ? $r->{exitcode} : 124);
    ' <<<"$json"
}

wait_for_agent() {
    local vmid=$1 i
    qm agent "$vmid" ping >/dev/null 2>&1 && return 0
    echo "Waiting for the guest agent in VM $vmid (first boot installs it)"
    for ((i = 0; i < 120; i++)); do
        qm agent "$vmid" ping >/dev/null 2>&1 && return 0
        sleep 5
    done
    die "VM $vmid has no guest agent after 10 minutes; check its console (qm terminal $vmid)"
}

# Where the runner registers, from --org ORG or --repo OWNER/REPO (a
# https://github.com/ prefix is allowed). Only one of them may be given.
set_url() {
    local kind=$1 path=${2#https://github.com/}
    path=${path%/}
    [[ -z $URL ]] || die "give only one of --org and --repo"
    case $kind in
        org) [[ $path =~ ^[A-Za-z0-9-]+$ ]] || die "--org takes an org name, like Nicu-Labs" ;;
        repo) [[ $path =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || die "--repo takes OWNER/REPO" ;;
    esac
    URL=https://github.com/$path
}

# A token from --token, $GHRUNNER_TOKEN, or the terminal. The page that shows
# it is the org's or repo's runners page; for destroy without --org/--repo
# (which reads where the runner is registered) the hint is generic.
get_token() {
    local kind=$1 token=${TOKEN:-${GHRUNNER_TOKEN:-}} path=${URL#https://github.com/}
    local page="the org's or repo's Settings > Actions > Runners"
    if [[ $path == */* ]]; then
        page=https://github.com/$path/settings/actions/runners
    elif [[ -n $path ]]; then
        page=https://github.com/organizations/$path/settings/actions/runners
    fi
    if [[ -z $token ]]; then
        { : </dev/tty; } 2>/dev/null || die "no $kind token; pass --token (see $page)"
        if [[ $kind == registration ]]; then
            echo "Registration token: $page/new > copy the value after --token" >/dev/tty
        else
            echo "Removal token: $page > the runner's ... menu > Remove" >/dev/tty
        fi
        read -rsp "Token: " token </dev/tty
        echo >/dev/tty
    fi
    [[ -n $token ]] || die "no $kind token"
    echo "$token"
}

# The storage that holds cloud-init snippets, and the vendor data there that
# installs the guest agent on first boot.
ensure_snippet() {
    local storage path content
    storage=$(pvesm status --content snippets 2>/dev/null | awk 'NR > 1 && $3 == "active" { print $1; exit }')
    if [[ -z $storage ]]; then
        content=$(awk '/^[a-z]+: / { in_local = ($1 == "dir:" && $2 == "local") }
            in_local && $1 == "content" { print $2 }' /etc/pve/storage.cfg 2>/dev/null || true)
        [[ -n $content ]] || die "no storage allows snippets and there is no dir storage 'local'; add snippets to a dir storage"
        echo "Allowing snippets on storage local (content: $content,snippets)" >&2
        pvesm set local --content "$content,snippets" >&2
        storage=local
    fi
    path=$(pvesm path "$storage:snippets/$SNIPPET")
    content='#cloud-config
package_update: true
packages:
  - qemu-guest-agent
runcmd:
  - [systemctl, enable, --now, qemu-guest-agent]'
    if [[ $(cat "$path" 2>/dev/null) != "$content" ]]; then
        mkdir -p "$(dirname "$path")"
        printf '%s\n' "$content" >"$path"
    fi
    echo "$storage:snippets/$SNIPPET"
}

# The template VM, built when missing. One left half-built by an interrupted
# run (tagged by us, not yet a template) is removed and built again.
cmd_template() {
    local tmp
    check_host
    if qm status "$TEMPLATE_ID" >/dev/null 2>&1; then
        has_tag "$TEMPLATE_ID" "$TAG-template" \
            || die "VM $TEMPLATE_ID is not ours (no $TAG-template tag); pick another --template-id"
        if is_template "$TEMPLATE_ID" && ((!REBUILD)); then
            return 0
        fi
        if is_template "$TEMPLATE_ID"; then
            echo "Removing template $TEMPLATE_ID to rebuild it (existing VMs are full clones and keep working)"
        else
            echo "Removing half-built template $TEMPLATE_ID from an interrupted run"
        fi
        qm destroy "$TEMPLATE_ID" --purge 1 --destroy-unreferenced-disks 1 >/dev/null
    fi

    echo "Building template $TEMPLATE_ID from $IMAGE"
    tmp=$(mktemp -d /var/tmp/gh-runner-image.XXXXXX)
    # shellcheck disable=SC2064  # expand $tmp now
    trap "rm -rf '$tmp'" EXIT
    echo "Downloading $IMAGE_URL/$IMAGE"
    curl -fL --progress-bar -o "$tmp/$IMAGE" "$IMAGE_URL/$IMAGE"
    curl -fsSL "$IMAGE_URL/SHA512SUMS" | grep "  $IMAGE\$" >"$tmp/SHA512SUMS" \
        || die "$IMAGE is not in SHA512SUMS"
    (cd "$tmp" && sha512sum -c --quiet SHA512SUMS) || die "checksum mismatch for $IMAGE"

    qm create "$TEMPLATE_ID" --name debian13-gh-runner-template --ostype l26 \
        --machine q35 --cpu host --cores 2 --memory 4096 \
        --scsihw virtio-scsi-single --net0 "virtio,bridge=$BRIDGE" \
        --serial0 socket --vga serial0 --agent enabled=1 --tags "$TAG-template"
    qm set "$TEMPLATE_ID" --scsi0 "$STORAGE:0,import-from=$tmp/$IMAGE,discard=on,iothread=1,ssd=1" \
        --ide2 "$STORAGE:cloudinit" --boot order=scsi0 --ipconfig0 ip=dhcp
    qm template "$TEMPLATE_ID"
    rm -rf "$tmp"
    trap - EXIT
}

# Copy ghrunner_setup.sh into the VM: the one next to this script when run
# from a checkout, so host and VM use the same version, else main's.
push_setup() {
    local vmid=$1 src
    src=$(dirname "$0")/ghrunner_setup.sh
    if [[ -f $src ]]; then
        guest "$vmid" stdin sh -c 'cat >/root/ghrunner_setup.sh' <"$src" >/dev/null
    else
        curl -fsSL "$SETUP_URL" | guest "$vmid" stdin sh -c 'cat >/root/ghrunner_setup.sh' >/dev/null
    fi
}

# Size and cloud-init settings for VM $1. A new VM ($2 = 1) gets all of them;
# an existing one only the --cores/--memory/--disk given this time, and its
# disk only grows. The gh-runner tag goes on last, so a clone that still has
# the template's tag was interrupted here and is finished on the next run.
configure_vm() {
    local vmid=$1 fresh=$2 snippet keys=$SSH_KEYS settings=() size
    snippet=$(ensure_snippet)
    ((fresh || CORES_SET)) && settings+=(--cores "$CORES")
    ((fresh || MEMORY_SET)) && settings+=(--memory "$MEMORY")
    ((fresh)) && settings+=(--cicustom "vendor=$snippet" --sshkeys "$keys")
    ((${#settings[@]} == 0)) || qm set "$vmid" "${settings[@]}" >/dev/null
    if ((fresh || DISK_SET)); then
        size=$(disk_gb "$vmid")
        if ((size < DISK)); then
            qm resize "$vmid" scsi0 "${DISK}G"
        elif ((size > DISK)); then
            echo "Disk is ${size} GB; disks only grow, so --disk $DISK is ignored"
        fi
    fi
    if ((!fresh && (CORES_SET || MEMORY_SET))) && [[ $(vm_status "$vmid") == running ]]; then
        echo "New CPU/memory settings take effect when VM $vmid next boots (qm reboot $vmid)"
    fi
    has_tag "$vmid" "$TAG" || qm set "$vmid" --tags "$TAG" >/dev/null
}

# Clone (or finish, or resize) runner VM $1 and start it; prints its VM ID.
prepare_vm() {
    local name=$1 vmid
    vmid=$(vm_id "$name")
    if [[ -n $vmid ]]; then
        if has_tag "$vmid" "$TAG"; then
            echo "VM $vmid ($name) exists" >&2
            configure_vm "$vmid" 0 >&2
        elif has_tag "$vmid" "$TAG-template" && ! is_template "$vmid"; then
            echo "VM $vmid ($name) was cloned but not configured; finishing it" >&2
            configure_vm "$vmid" 1 >&2
        else
            die "VM $vmid is called $name but is not a runner VM (no $TAG tag)"
        fi
    else
        cmd_template >&2
        vmid=$(pvesh get /cluster/nextid)
        echo "Creating VM $vmid ($name): $CORES cores, $MEMORY MB, ${DISK} GB" >&2
        qm clone "$TEMPLATE_ID" "$vmid" --name "$name" --full 1 --storage "$STORAGE" >&2
        configure_vm "$vmid" 1 >&2
    fi
    [[ $(vm_status "$vmid") == running ]] || qm start "$vmid" >&2
    echo "$vmid"
}

# Before cloning, check the host has room for the VMs that will be started.
# RAM: Proxmox starts VMs past the host's memory, and the OOM killer then
# kills VMs, so the memory of every VM to start must fit in MemAvailable,
# less MIN_FREE_MB for the host. Disk: VM disks are thin, so a new VM only
# takes about NEW_VM_GB at first, but a thin pool that fills up stops every
# VM on it; it must have room for the new VMs now, and a warning says when
# their full disks would not fit.
check_capacity() {
    local new=0 start=0 name vmid avail_mb need_mb avail_gb
    for name in "$@"; do
        vmid=$(vm_id "$name")
        if [[ -z $vmid ]]; then
            new=$((new + 1)) start=$((start + 1))
        elif [[ $(vm_status "$vmid") != running ]]; then
            start=$((start + 1))
        fi
    done
    avail_mb=$(awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo)
    need_mb=$((start * MEMORY))
    if ((need_mb > avail_mb - MIN_FREE_MB)); then
        die "starting $start VMs needs $need_mb MB of RAM, but the host has $avail_mb MB available (keeping $MIN_FREE_MB MB for itself); use a smaller --count or --memory"
    fi
    ((new)) || return 0
    avail_gb=$(pvesm status --storage "$STORAGE" | awk 'NR > 1 { print int($6 / 1048576) }')
    if ((new * NEW_VM_GB > avail_gb)); then
        die "$new new VMs need about $((new * NEW_VM_GB)) GB on $STORAGE at first, but it has $avail_gb GB free"
    fi
    if ((new * DISK > avail_gb)); then
        echo "Warning: $new VMs with ${DISK} GB disks can grow to $((new * DISK)) GB, but $STORAGE has $avail_gb GB free." >&2
        echo "  Disks are thin, so this works until they fill up; a full pool stops every VM on it." >&2
        echo "  Runner cleanup keeps each VM under 80% of its disk; watch with: pvesm status" >&2
    fi
}

# Set up the runner in VM $1 (named $2) with registration token $3.
install_runner() {
    local vmid=$1 name=$2 token=$3
    # shellcheck disable=SC2016  # expanded by bash in the VM
    guest "$vmid" stdin bash -c 'IFS= read -r GHRUNNER_TOKEN; export GHRUNNER_TOKEN
        exec bash /root/ghrunner_setup.sh install --name "$1" --"$2" "$3" ${4:+--labels "$4"}' \
        _ "$name" "$SCOPE" "$URL" "$LABELS" <<<"$token"
}

# One runner VM called --name (or, if it exists, finished and checked), or
# with --count N, N new ones called NAME-1, NAME-2, ... skipping every name a
# VM in the cluster already has. VMs are cloned one at a time (clones of one template cannot run
# side by side), boot together, and then have their runners set up in
# parallel, all with the one registration token.
cmd_create() {
    [[ -n $NAME ]] || die "create needs --name"
    [[ -n $URL ]] || die "create needs --org ORG (a runner for every repo of the org) or --repo OWNER/REPO"
    [[ $NAME =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "--name must be a hostname: lowercase letters, digits, -"
    [[ -n $SSH_KEYS ]] || die "no /root/.ssh/authorized_keys; pass --ssh-keys FILE (the VM's only login is SSH with a key)"
    [[ -f $SSH_KEYS ]] || die "no ssh keys file $SSH_KEYS"
    # Lines may start with options such as restrict or from="...".
    grep -qE '(ssh-(ed25519|rsa)|ecdsa-sha2-|sk-(ssh|ecdsa)-)' "$SSH_KEYS" || die "$SSH_KEYS has no public keys"
    local names=() vmids=() todo=() i name vmid report token failed=0
    local -A pids=() taken=()
    if ((COUNT_SET)); then
        while read -r name; do taken[$name]=1; done < <(vm_names)
        for ((i = 1; ${#names[@]} < COUNT; i++)); do
            [[ -n ${taken[$NAME-$i]:-} ]] || names+=("$NAME-$i")
        done
        echo "Creating ${names[*]}"
    else
        names=("$NAME")
    fi
    check_host
    check_capacity "${names[@]}"
    for name in "${names[@]}"; do
        vmid=$(prepare_vm "$name")   # on its own line so set -e stops on a failure
        vmids+=("$vmid")
    done

    # Wait for every VM to boot, and keep the ones whose runner is not set up.
    for i in "${!names[@]}"; do
        name=${names[i]} vmid=${vmids[i]}
        wait_for_agent "$vmid"
        guest "$vmid" cloud-init status --wait >/dev/null || true
        push_setup "$vmid"
        if report=$(guest "$vmid" bash /root/ghrunner_setup.sh check --name "$name" --"$SCOPE" "$URL" 2>/dev/null); then
            echo "$report"
            echo "VM $vmid ($name): runner already set up"
        else
            todo+=("$i")
        fi
    done
    ((${#todo[@]})) || return 0

    token=$(get_token registration)
    if ((${#todo[@]} == 1)); then
        i=${todo[0]}
        echo "Setting up the runner in VM ${vmids[i]} (${names[i]}) (a few minutes)"
        install_runner "${vmids[i]}" "${names[i]}" "$token"
        return
    fi
    echo "Setting up ${#todo[@]} runners in parallel (a few minutes; output appears as each finishes)"
    for i in "${todo[@]}"; do
        name=${names[i]}
        install_runner "${vmids[i]}" "$name" "$token" > >(sed "s/^/[$name] /") 2>&1 &
        pids[$name]=$!
    done
    for name in "${!pids[@]}"; do
        if ! wait "${pids[$name]}"; then
            echo "[$name] setup failed; retry with: $0 create --name $name --$SCOPE ${URL#https://github.com/}" >&2
            failed=$((failed + 1))
        fi
    done
    sleep 1   # let the sed output processes flush
    ((failed == 0)) || die "$failed of ${#todo[@]} runners failed to set up"
}

# Every runner VM on this node: ID, name, state, and the runner check.
cmd_list() {
    local vmid name status problems=0
    while read -r vmid name status; do
        has_tag "$vmid" "$TAG" || continue
        echo "== VM $vmid $name ($status)"
        [[ $status == running ]] || continue
        if ! qm agent "$vmid" ping </dev/null >/dev/null 2>&1; then
            echo "  FAIL  no guest agent"
            problems=$((problems + 1))
            continue
        fi
        guest "$vmid" /usr/local/sbin/ghrunner_setup.sh check 2>&1 | sed 1d || problems=$((problems + 1))
    done < <(qm list | awk 'NR > 1 { print $1, $2, $3 }')
    ((problems == 0))
}

cmd_check() {
    [[ -n $NAME ]] || { cmd_list; return; }
    local vmid
    vmid=$(vm_id "$NAME")
    [[ -n $vmid ]] || die "no VM called $NAME"
    guest "$vmid" /usr/local/sbin/ghrunner_setup.sh check --name "$NAME"
}

cmd_destroy() {
    [[ -n $NAME ]] || die "destroy needs --name"
    local vmid token answer
    vmid=$(vm_id "$NAME")
    if [[ -z $vmid ]]; then
        echo "No VM called $NAME; nothing to delete"
        return 0
    fi
    has_tag "$vmid" "$TAG" || die "VM $vmid ($NAME) is not a runner VM (no $TAG tag); not touching it"
    if ((!YES)); then
        read -rp "Delete VM $vmid ($NAME) and its disks? Type the name to confirm: " answer </dev/tty
        [[ $answer == "$NAME" ]] || die "not confirmed"
    fi
    if [[ $(vm_status "$vmid") == running ]] && qm agent "$vmid" ping >/dev/null 2>&1; then
        token=$(get_token removal)
        # shellcheck disable=SC2016  # expanded by bash in the VM
        guest "$vmid" stdin bash -c 'IFS= read -r GHRUNNER_TOKEN; export GHRUNNER_TOKEN
            exec /usr/local/sbin/ghrunner_setup.sh unregister --name "$1"' _ "$NAME" <<<"$token" \
            || die "could not unregister $NAME; remove it on GitHub, then destroy again with the VM stopped"
        qm stop "$vmid"
    elif [[ $(vm_status "$vmid") == running ]]; then
        die "VM $vmid has no guest agent, so its runner cannot be unregistered; stop it first"
    else
        echo "VM $vmid is stopped: remove runner $NAME on GitHub yourself if it is still listed"
    fi
    qm destroy "$vmid" --purge 1 --destroy-unreferenced-disks 1
    echo "Deleted VM $vmid ($NAME)"
}

COMMAND=${1:-}
[[ -n $COMMAND ]] && shift
NAME='' URL='' SCOPE='' TOKEN='' COUNT=1 COUNT_SET=0 CORES=2 MEMORY=4096 DISK=40 LABELS='' STORAGE=local-lvm BRIDGE=vmbr0
SSH_KEYS='' TEMPLATE_ID=9100 YES=0 REBUILD=0 CORES_SET=0 MEMORY_SET=0 DISK_SET=0
while (($#)); do
    case $1 in
        --name) NAME=$2; shift 2 ;;
        --org|--repo) SCOPE=${1#--}; set_url "$SCOPE" "$2"; shift 2 ;;
        --token) TOKEN=$2; shift 2 ;;
        --count) COUNT=$2; COUNT_SET=1; shift 2 ;;
        --cores) CORES=$2; CORES_SET=1; shift 2 ;;
        --memory) MEMORY=$2; MEMORY_SET=1; shift 2 ;;
        --disk) DISK=${2%G}; DISK_SET=1; shift 2 ;;
        --labels) LABELS=$2; shift 2 ;;
        --storage) STORAGE=$2; shift 2 ;;
        --bridge) BRIDGE=$2; shift 2 ;;
        --ssh-keys) SSH_KEYS=$2; shift 2 ;;
        --template-id) TEMPLATE_ID=$2; shift 2 ;;
        --yes) YES=1; shift ;;
        --rebuild) REBUILD=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option $1 (see --help)" ;;
    esac
done
if [[ -z $SSH_KEYS ]]; then
    SSH_KEYS=/root/.ssh/authorized_keys
    [[ -f $SSH_KEYS ]] || SSH_KEYS=''
fi
case $COMMAND in -h|--help|'') usage; exit 0 ;; esac
for n in "$COUNT" "$CORES" "$MEMORY" "$DISK" "$TEMPLATE_ID"; do
    [[ $n =~ ^[1-9][0-9]*$ ]] || die "--count, --cores, --memory, --disk and --template-id take whole numbers"
done
((!COUNT_SET)) || [[ $COMMAND == create ]] || die "--count only works with create"
[[ $EUID -eq 0 ]] || die "run this as root on the Proxmox host"
command -v qm >/dev/null || die "qm not found; run this on the Proxmox host"

case $COMMAND in
    create) cmd_create ;;
    list) cmd_list ;;
    check) cmd_check ;;
    destroy) cmd_destroy ;;
    template) cmd_template ;;
    *) die "unknown command $COMMAND (see --help)" ;;
esac
