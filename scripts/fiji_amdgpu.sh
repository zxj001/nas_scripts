#!/usr/bin/env bash
# fiji_amdgpu.sh - in a Debian 13 VM with an R9 Nano / Fiji passed through,
# build, sign and install an amdgpu module patched for the Fiji passthrough
# use-after-free (issue #64, docs/fiji-amdgpu.md). Stock amdgpu frees
# adev->pm.fw in amdgpu_device_need_post() without clearing it, and SMU init
# then reads the freed firmware: an Oops in amdgpu_ucode_print_smc_hdr and no
# render node. patches/amdgpu-fiji-passthrough-pm-fw-uaf.patch fixes that.
#
#   scripts/fiji_amdgpu.sh build              # as your user: download, patch, compile
#   sudo scripts/fiji_amdgpu.sh install       # sign, install to updates/, initramfs
#   sudo scripts/fiji_amdgpu.sh uninstall     # back to the stock module
#   scripts/fiji_amdgpu.sh status             # module in use, key, render node, Vulkan
#   scripts/fiji_amdgpu.sh --help             # this text
#
# build needs no root. It runs apt-get download for the linux-source and
# linux-headers packages of the exact running kernel version, unpacks them
# under the work dir ($FIJI_WORK, default ~/fiji-amdgpu), applies the patch to
# drivers/gpu/drm/amd and compiles only amdgpu.ko against those headers.
# Reruns reuse the downloads and the objects already built.
#
# install signs the module with the dkms key /var/lib/dkms/mok.key (created
# when missing, so a later dkms package reuses the same enrolled key), copies
# it to /lib/modules/KVER/updates/amdgpu.ko, which depmod prefers over the
# stock kernel/.../amdgpu.ko.xz, and rebuilds the initramfs. With Secure Boot
# on and the key not yet enrolled it runs mokutil --import, which asks for a
# one-time password; reboot, and in the blue MOK Manager on the VM console
# (Proxmox noVNC) choose Enroll MOK and enter that password.
#
# KVER (default: uname -r) selects another installed kernel. The stock module
# is never touched, so uninstall, or booting another kernel, undoes install.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH="$ROOT/patches/amdgpu-fiji-passthrough-pm-fw-uaf.patch"
KVER="${KVER:-$(uname -r)}"
MOK_KEY=/var/lib/dkms/mok.key
MOK_PUB=/var/lib/dkms/mok.pub
PATH="$PATH:/usr/sbin:/sbin"

usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
}

die() { echo "fiji_amdgpu: $*" >&2; exit 1; }
say() { echo "==> $*"; }

# The invoking user's work dir, also under sudo.
work_dir() {
    if [[ -n "${FIJI_WORK:-}" ]]; then
        echo "$FIJI_WORK"
    elif [[ -n "${SUDO_USER:-}" ]]; then
        echo "$(getent passwd "$SUDO_USER" | cut -d: -f6)/fiji-amdgpu"
    else
        echo "$HOME/fiji-amdgpu"
    fi
}

WORK="$(work_dir)"
ABI="${KVER%-*}"                       # 6.12.111+deb13
SERIES="$(cut -d. -f1-2 <<<"$KVER")"   # 6.12
HEADERS="$WORK/root/usr/src/linux-headers-$KVER"
SRC="$WORK/src/linux-source-$SERIES"
AMDGPU="$SRC/drivers/gpu/drm/amd/amdgpu"
MODULE="$AMDGPU/amdgpu.ko"
SIGN_FILE="$WORK/root/usr/lib/linux-kbuild-$ABI/scripts/sign-file"

need_root() { [[ $EUID -eq 0 ]] || die "$1 needs root: sudo $0 $1"; }

cmd_build() {
    [[ $EUID -ne 0 ]] || die "run build as your user, not root"
    local ver
    ver="$(dpkg-query -W -f='${Version}' "linux-image-$KVER" 2>/dev/null)" ||
        die "linux-image-$KVER is not installed"
    mkdir -p "$WORK/debs" "$WORK/root" "$WORK/src"

    local pkg missing=()
    for pkg in "linux-source-$SERIES" "linux-headers-$KVER" "linux-headers-$ABI-common" "linux-kbuild-$ABI"; do
        compgen -G "$WORK/debs/${pkg}_${ver}_*.deb" >/dev/null || missing+=("$pkg=$ver")
    done
    if ((${#missing[@]})); then
        say "Downloading kernel $ver source and headers"
        (cd "$WORK/debs" && apt-get download -q "${missing[@]}")
    fi
    if [[ ! -f "$HEADERS/Makefile" ]]; then
        for deb in "$WORK"/debs/*_"$ver"_*.deb; do dpkg -x "$deb" "$WORK/root"; done
        # The headers Makefile includes the common tree by absolute /usr/src path.
        sed -i "s#/usr/src/#$WORK/root/usr/src/#" "$HEADERS/Makefile"
    fi

    if [[ ! -d "$AMDGPU" ]]; then
        say "Unpacking drivers/gpu/drm/amd"
        tar -xJf "$WORK/root/usr/src/linux-source-$SERIES.tar.xz" -C "$WORK/src" \
            "linux-source-$SERIES/drivers/gpu/drm/amd"
    fi
    if patch -d "$SRC" -p1 -R --dry-run -s -f <"$PATCH" >/dev/null 2>&1; then
        say "Patch already applied"
    else
        say "Applying $(basename "$PATCH")"
        patch -d "$SRC" -p1 -f <"$PATCH"
    fi
    # amdgpu_trace.h includes itself as ../../drivers/gpu/drm/amd/amdgpu, which
    # only resolves in a full tree; out of tree, put our drivers/gpu on the path.
    # shellcheck disable=SC2016  # $(src) is for make
    grep -q 'CFLAGS_amdgpu_trace_points.o' "$AMDGPU/Makefile" ||
        printf '\n# out-of-tree build (fiji_amdgpu.sh)\nCFLAGS_amdgpu_trace_points.o += -I$(src)/../../..\n' \
            >>"$AMDGPU/Makefile"

    say "Compiling amdgpu.ko (log: $WORK/build.log)"
    if ! make -C "$HEADERS" M="$AMDGPU" -j"$(nproc)" modules >"$WORK/build.log" 2>&1; then
        tail -20 "$WORK/build.log" >&2
        die "build failed, see $WORK/build.log"
    fi
    say "Built $MODULE"
    echo "Next: sudo $0 install"
}

key_enrolled() {
    mokutil --test-key "$MOK_PUB" 2>/dev/null | grep -q 'already enrolled'
}

secure_boot() {
    mokutil --sb-state 2>/dev/null | grep -q 'SecureBoot enabled'
}

cmd_install() {
    need_root install
    [[ -f "$MODULE" ]] || die "no $MODULE; run $0 build first (as your user)"
    if [[ ! -f "$MOK_KEY" ]]; then
        say "Creating module signing key $MOK_KEY"
        install -d -m 700 /var/lib/dkms
        openssl req -new -x509 -nodes -days 36500 -subj "/CN=DKMS module signing key" \
            -newkey rsa:2048 -keyout "$MOK_KEY" -outform DER -out "$MOK_PUB" 2>/dev/null
        chmod 600 "$MOK_KEY"
    fi
    local dest="/lib/modules/$KVER/updates/amdgpu.ko"
    say "Signing and installing $dest"
    install -D -m 644 "$MODULE" "$dest"
    "$SIGN_FILE" sha256 "$MOK_KEY" "$MOK_PUB" "$dest"
    depmod -a "$KVER"
    [[ "$(modinfo -k "$KVER" -n amdgpu)" == "$dest" ]] || die "depmod does not pick $dest"
    say "Rebuilding the initramfs for $KVER"
    update-initramfs -u -k "$KVER"

    if secure_boot && ! key_enrolled; then
        say "Secure Boot is on and the key is not enrolled yet"
        echo "Choose a one-time password; you type it again in MOK Manager after reboot."
        mokutil --import "$MOK_PUB"
        echo "Reboot, then on the VM console choose Enroll MOK -> Continue -> Yes and the password."
    else
        echo "Reboot to load the patched module, then run: $0 status"
    fi
}

cmd_uninstall() {
    need_root uninstall
    rm -f "/lib/modules/$KVER/updates/amdgpu.ko"
    depmod -a "$KVER"
    update-initramfs -u -k "$KVER"
    echo "Stock amdgpu restored for $KVER; reboot to load it."
}

cmd_status() {
    local mod signer gpu node driver n
    mod="$(modinfo -k "$KVER" -n amdgpu 2>/dev/null || echo none)"
    signer="$(modinfo -k "$KVER" -F signer amdgpu 2>/dev/null || true)"
    echo "kernel:       $KVER"
    echo "amdgpu file:  $mod${signer:+ (signed by $signer)}"
    echo "Secure Boot:  $(secure_boot && echo enabled || echo disabled)"
    if [[ -f "$MOK_PUB" ]]; then
        echo "MOK key:      $MOK_PUB $(key_enrolled && echo enrolled || echo 'NOT enrolled')"
    else
        echo "MOK key:      none ($MOK_PUB)"
    fi
    echo "built module: $([[ -f "$MODULE" ]] && echo "$MODULE" || echo none)"
    for gpu in /sys/bus/pci/devices/*; do
        [[ "$(cat "$gpu/vendor")" == 0x1002 && "$(cat "$gpu/class")" == 0x03* ]] || continue
        node=MISSING driver=none
        for n in "$gpu"/drm/renderD*; do [[ -e "$n" ]] && node="/dev/dri/${n##*/}"; done
        [[ -L "$gpu/driver" ]] && driver="$(basename "$(readlink "$gpu/driver")")"
        echo "GPU ${gpu##*/}:  driver $driver, render node $node"
    done
    if command -v vulkaninfo >/dev/null; then
        echo "Vulkan:       $(vulkaninfo --summary 2>/dev/null | awk -F'= ' '/deviceName/ { printf "%s; ", $2 }')"
    fi
}

case "${1:-}" in
    build) cmd_build ;;
    install) cmd_install ;;
    uninstall) cmd_uninstall ;;
    status) cmd_status ;;
    -h|--help|help) usage ;;
    *) usage >&2; exit 2 ;;
esac
