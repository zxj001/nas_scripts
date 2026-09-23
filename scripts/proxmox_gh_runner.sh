#!/usr/bin/env bash
# proxmox_gh_runner.sh - on the Proxmox host, create a Debian 13 VM that runs
# one Nicu-Labs GitHub Actions runner (GITHUB_RUNNER.md). The VM is cloned
# from a cloud-image template, named and sized by cloud-init, and set up by
# running scripts/ghrunner_setup.sh inside it through the QEMU guest agent,
# so it needs no SSH access and the token never lands in a file.
#
#   scripts/proxmox_gh_runner.sh create --name gh-runner-1    # asks for the token
#   scripts/proxmox_gh_runner.sh create --name gh-runner-2 --cores 4 --memory 8192 \
#       --disk 80 --labels heavy --token AAAA...
#   scripts/proxmox_gh_runner.sh list                  # runner VMs and their checks
#   scripts/proxmox_gh_runner.sh check --name gh-runner-1
#   scripts/proxmox_gh_runner.sh destroy --name gh-runner-1   # unregister, delete VM
#   scripts/proxmox_gh_runner.sh template              # build the template only
#
# create is safe to rerun: an existing VM is started if needed and its runner
# set up or checked. The template (VM 9100, built on first use) comes from
# Debian's genericcloud image, checked against its SHA512SUMS. It needs a
# storage with the `snippets` content type (for the cloud-init that installs
# the guest agent); the script says how to enable it on `local` if none has.
#
# Options: --name (VM name, which becomes the hostname and the runner name),
# --token (registration token for create, removal token for destroy; also
# $GHRUNNER_TOKEN, else asked for), --cores (2), --memory MB (4096), --disk GB
# (40), --labels (extra runner labels), --storage (local-lvm), --bridge (vmbr0),
# --ssh-keys (file of public keys for the VM's `debian` user; default
# /root/.ssh/authorized_keys), --template-id (9100), --yes (destroy without
# asking).
set -euo pipefail

TAG=gh-runner
IMAGE_URL=https://cloud.debian.org/images/cloud/trixie/latest
IMAGE=debian-13-genericcloud-amd64.qcow2
SNIPPET=gh-runner-vendor.yaml
SETUP_URL=https://raw.githubusercontent.com/zxj001/nas_scripts/main/scripts/ghrunner_setup.sh

usage() {
    sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
    echo "proxmox_gh_runner: $*" >&2
    exit 1
}

# The VM ID of the VM called $1 on this node, or nothing.
vm_id() {
    qm list | awk -v name="$1" 'NR > 1 && $2 == name { print $1 }'
}

vm_status() {
    qm status "$1" | awk '{ print $2 }'
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

# A token from --token, $GHRUNNER_TOKEN, or the terminal.
get_token() {
    local kind=$1 token=${TOKEN:-${GHRUNNER_TOKEN:-}}
    local page=https://github.com/organizations/Nicu-Labs/settings/actions/runners
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
        content=$(awk '/^[a-z]+: / { in_local = ($2 == "local") } in_local && $1 == "content" { print $2 }' \
            /etc/pve/storage.cfg 2>/dev/null || true)
        die "no storage allows snippets; enable them on local with:
  pvesm set local --content ${content:+$content,}snippets"
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

cmd_template() {
    local tmp
    if qm status "$TEMPLATE_ID" >/dev/null 2>&1; then
        qm config "$TEMPLATE_ID" | grep -q '^template: 1' \
            || die "VM $TEMPLATE_ID exists and is not a template; pick another --template-id"
        return 0
    fi
    pvesm status | awk 'NR > 1 { print $1 }' | grep -qx "$STORAGE" || die "no storage $STORAGE (see pvesm status)"
    ip link show "$BRIDGE" >/dev/null 2>&1 || die "no bridge $BRIDGE (see ip -br link)"

    echo "Building template $TEMPLATE_ID from $IMAGE"
    tmp=$(mktemp -d /var/tmp/gh-runner-image.XXXXXX)
    # shellcheck disable=SC2064  # expand $tmp now
    trap "rm -rf '$tmp'" EXIT
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

cmd_create() {
    [[ -n $NAME ]] || die "create needs --name"
    [[ $NAME =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "--name must be a hostname: lowercase letters, digits, -"
    local vmid snippet keys token
    vmid=$(vm_id "$NAME")
    if [[ -n $vmid ]]; then
        has_tag "$vmid" "$TAG" || die "VM $vmid is called $NAME but is not a runner VM (no $TAG tag)"
        echo "VM $vmid ($NAME) exists"
    else
        cmd_template
        snippet=$(ensure_snippet)
        keys=$SSH_KEYS
        [[ -z $keys || -f $keys ]] || die "no ssh keys file $keys"
        vmid=$(pvesh get /cluster/nextid)
        echo "Creating VM $vmid ($NAME): $CORES cores, $MEMORY MB, ${DISK} GB"
        qm clone "$TEMPLATE_ID" "$vmid" --name "$NAME" --full 1 --storage "$STORAGE"
        qm set "$vmid" --cores "$CORES" --memory "$MEMORY" --tags "$TAG" \
            --cicustom "vendor=$snippet" ${keys:+--sshkeys "$keys"} >/dev/null
        qm resize "$vmid" scsi0 "${DISK}G"
    fi
    [[ $(vm_status "$vmid") == running ]] || qm start "$vmid"
    wait_for_agent "$vmid"
    guest "$vmid" cloud-init status --wait >/dev/null || true

    push_setup "$vmid"
    local report
    if report=$(guest "$vmid" bash /root/ghrunner_setup.sh check --name "$NAME" 2>/dev/null); then
        echo "$report"
        echo "VM $vmid ($NAME): runner already set up"
        return 0
    fi
    token=$(get_token registration)
    echo "Setting up the runner in VM $vmid (a few minutes)"
    # shellcheck disable=SC2016  # expanded by bash in the VM
    guest "$vmid" stdin bash -c 'IFS= read -r GHRUNNER_TOKEN; export GHRUNNER_TOKEN
        exec bash /root/ghrunner_setup.sh install --name "$1" ${2:+--labels "$2"}' \
        _ "$NAME" "$LABELS" <<<"$token"
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
    [[ -n $vmid ]] || die "no VM called $NAME"
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
NAME='' TOKEN='' CORES=2 MEMORY=4096 DISK=40 LABELS='' STORAGE=local-lvm BRIDGE=vmbr0
SSH_KEYS='' TEMPLATE_ID=9100 YES=0
while (($#)); do
    case $1 in
        --name) NAME=$2; shift 2 ;;
        --token) TOKEN=$2; shift 2 ;;
        --cores) CORES=$2; shift 2 ;;
        --memory) MEMORY=$2; shift 2 ;;
        --disk) DISK=${2%G}; shift 2 ;;
        --labels) LABELS=$2; shift 2 ;;
        --storage) STORAGE=$2; shift 2 ;;
        --bridge) BRIDGE=$2; shift 2 ;;
        --ssh-keys) SSH_KEYS=$2; shift 2 ;;
        --template-id) TEMPLATE_ID=$2; shift 2 ;;
        --yes) YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option $1 (see --help)" ;;
    esac
done
if [[ -z $SSH_KEYS ]]; then
    SSH_KEYS=/root/.ssh/authorized_keys
    [[ -f $SSH_KEYS ]] || SSH_KEYS=''
fi
case $COMMAND in -h|--help|'') usage; exit 0 ;; esac
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
