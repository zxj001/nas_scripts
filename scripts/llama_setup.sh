#!/usr/bin/env bash
# llama_setup.sh - set up llama.cpp with Vulkan in a Debian 13 VM with a
# passed-through GPU, fetch GGUF models and benchmark GPU against CPU
# (docs/llama.md). Made for the R9 Nano VM (myllama, docs/fiji-amdgpu.md) but
# works with any GPU Mesa's Vulkan drivers support. No root needed.
#
#   scripts/llama_setup.sh install            # llama.cpp Vulkan and CPU builds
#   scripts/llama_setup.sh models             # the default Qwen3 GGUFs
#   scripts/llama_setup.sh model REPO FILE    # any GGUF on Hugging Face
#   scripts/llama_setup.sh check [MODEL]      # devices, plus a short GPU generation
#   scripts/llama_setup.sh bench [MODEL...]   # llama-bench, GPU vs CPU, saved to results/
#   scripts/llama_setup.sh status             # what is installed
#   scripts/llama_setup.sh --help             # this text
#
# Everything lives under $LLAMA_HOME (default ~/llama): BUILD/vulkan and
# BUILD/cpu are llama.cpp's prebuilt Ubuntu x64 releases, `current` links to
# the build in use, models/ holds GGUFs and results/ the benchmark tables.
# install takes --build bNNNNN (default: the pinned build below). The CPU build
# is the baseline: the Vulkan build offloads big prompt batches to the GPU even
# with -ngl 0. Each build runs from its own directory, because llama.cpp also
# loads backends from the working directory.
#
# models and model check each file against the SHA-256 Hugging Face lists for it.
# MODEL is a path or a file name in models/; bench and check default to every
# GGUF there (check: the smallest).
set -euo pipefail

LLAMA_HOME="${LLAMA_HOME:-$HOME/llama}"
# llama.cpp release tested on the R9 Nano (RADV FIJI); bump after testing.
BUILD_DEFAULT=b11577
RELEASES=https://github.com/ggml-org/llama.cpp/releases/download
# Qwen3 GGUFs that fit a 4 GiB card with room for the context.
DEFAULT_MODELS=(
    "Qwen/Qwen3-1.7B-GGUF Qwen3-1.7B-Q8_0.gguf"
    "Qwen/Qwen3-4B-GGUF Qwen3-4B-Q4_K_M.gguf"
)

usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
}

die() { echo "llama_setup: $*" >&2; exit 1; }
say() { echo "==> $*"; }

current() {
    [[ -d "$LLAMA_HOME/current/vulkan" ]] || die "llama.cpp is not installed; run $0 install"
    readlink -f "$LLAMA_HOME/current"
}

# Vulkan loader and Mesa drivers come from apt; installing them needs root.
need_vulkan() {
    local pkg missing=()
    for pkg in libvulkan1 mesa-vulkan-drivers; do
        dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'ok installed' || missing+=("$pkg")
    done
    ((${#missing[@]} == 0)) || die "missing ${missing[*]}: sudo apt-get install ${missing[*]} vulkan-tools"
}

cmd_install() {
    local build="$BUILD_DEFAULT"
    if [[ "${1:-}" == --build ]]; then build="${2:?--build needs a build like b11577}"; fi
    need_vulkan
    local dir="$LLAMA_HOME/$build" flavour tarball
    for flavour in vulkan cpu; do
        if [[ -x "$dir/$flavour/llama-bench" ]]; then
            say "$build $flavour already installed"
            continue
        fi
        tarball="llama-$build-bin-ubuntu-$([[ $flavour == vulkan ]] && echo vulkan-x64 || echo x64).tar.gz"
        say "Downloading $tarball"
        mkdir -p "$dir/$flavour"
        curl -fSL --progress-bar -o "$dir/$tarball" "$RELEASES/$build/$tarball"
        tar -xzf "$dir/$tarball" -C "$dir/$flavour" --strip-components=1
        rm -f "$dir/$tarball"
    done
    ln -sfn "$build" "$LLAMA_HOME/current"
    say "$("$dir/vulkan/llama-cli" --version 2>&1 | grep '^version')"
    (cd "$dir/vulkan" && ./llama-bench --list-devices 2>/dev/null | sed -n '/Available devices/,$p')
}

# Download REPO/FILE into models/ and check the SHA-256 Hugging Face reports.
fetch_model() {
    local repo="$1" file="$2" sha
    mkdir -p "$LLAMA_HOME/models"
    sha="$(curl -fsS "https://huggingface.co/api/models/$repo/tree/main" | python3 -I -c '
import json, sys
for f in json.load(sys.stdin):
    if f["path"] == sys.argv[1]:
        print(f.get("lfs", {}).get("oid", ""))' "$file")" || die "cannot list $repo"
    [[ -n "$sha" ]] || die "$file is not in $repo"
    if [[ -f "$LLAMA_HOME/models/$file" ]] &&
        echo "$sha  $LLAMA_HOME/models/$file" | sha256sum -c --status; then
        say "$file already downloaded"
        return
    fi
    say "Downloading $repo/$file"
    curl -fSL --progress-bar -o "$LLAMA_HOME/models/$file.part" "https://huggingface.co/$repo/resolve/main/$file"
    echo "$sha  $LLAMA_HOME/models/$file.part" | sha256sum -c --status || die "$file: SHA-256 mismatch"
    mv "$LLAMA_HOME/models/$file.part" "$LLAMA_HOME/models/$file"
}

cmd_models() {
    local spec
    for spec in "${DEFAULT_MODELS[@]}"; do
        # shellcheck disable=SC2086  # "repo file"
        fetch_model $spec
    done
}

# MODEL arguments as absolute paths; none: every GGUF in models/, by size.
models_or_all() {
    local m
    if (($#)); then
        for m in "$@"; do
            [[ -f "$m" ]] || m="$LLAMA_HOME/models/$m"
            [[ -f "$m" ]] || die "no model $m"
            readlink -f "$m"
        done
    else
        find "$LLAMA_HOME/models" -maxdepth 1 -name '*.gguf' -printf '%s %p\n' 2>/dev/null |
            sort -n | cut -d' ' -f2-
    fi
}

cmd_check() {
    local dir model
    dir="$(current)"
    (cd "$dir/vulkan" && ./llama-bench --list-devices 2>/dev/null | sed -n '/Available devices/,$p')
    model="$(models_or_all "$@" | head -1)"
    [[ -n "$model" ]] || { echo "No model to test; run $0 models"; return; }
    say "GPU generation with $(basename "$model") (greedy, should read as sensible English)"
    (cd "$dir/vulkan" && ./llama-completion -m "$model" -ngl 99 --temp 0 -n 48 -no-cnv \
        -p "The three primary colors of light are" 2>/dev/null) || die "GPU generation failed"
    echo
}

cmd_bench() {
    local dir build model out
    dir="$(current)"
    build="$(basename "$dir")"
    mapfile -t models < <(models_or_all "$@")
    ((${#models[@]})) || die "no models; run $0 models"
    mkdir -p "$LLAMA_HOME/results"
    for model in "${models[@]}"; do
        out="$LLAMA_HOME/results/$(date +%F)-$(hostname -s)-$build-$(basename "$model" .gguf).md"
        {
            echo "# $(basename "$model") on $(hostname -s), llama.cpp $build, $(date '+%F %R')"
            echo
            echo "CPU: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ //'), $(nproc) threads"
            (cd "$dir/vulkan" && ./llama-bench --list-devices 2>/dev/null | grep -E '^ +Vulkan')
            echo
            echo "## GPU (Vulkan, all layers offloaded)"
            echo
            (cd "$dir/vulkan" && ./llama-bench -m "$model" -ngl 99 -p 512 -n 128 -r 3 -o md 2>/dev/null)
            echo
            echo "## CPU only"
            echo
            (cd "$dir/cpu" && ./llama-bench -m "$model" -t "$(nproc)" -p 512 -n 128 -r 3 -o md 2>/dev/null)
        } | tee "$out"
        say "Saved $out"
    done
}

cmd_status() {
    local dir
    echo "LLAMA_HOME:  $LLAMA_HOME"
    if [[ -d "$LLAMA_HOME/current/vulkan" ]]; then
        dir="$(readlink -f "$LLAMA_HOME/current")"
        echo "llama.cpp:   $(basename "$dir") ($("$dir/vulkan/llama-cli" --version 2>&1 | grep -o 'commit [0-9a-f]*'))"
        (cd "$dir/vulkan" && ./llama-bench --list-devices 2>/dev/null | grep -E '^ +Vulkan' | sed 's/^ */device:      /')
    else
        echo "llama.cpp:   not installed"
    fi
    find "$LLAMA_HOME/models" -maxdepth 1 -name '*.gguf' -printf 'model:       %f (%s bytes)\n' 2>/dev/null | sort
    find "$LLAMA_HOME/results" -maxdepth 1 -name '*.md' -printf 'result:      %p\n' 2>/dev/null | sort
}

case "${1:-}" in
    install) shift; cmd_install "$@" ;;
    models) cmd_models ;;
    model) fetch_model "${2:?model REPO FILE}" "${3:?model REPO FILE}" ;;
    check) shift; cmd_check "$@" ;;
    bench) shift; cmd_bench "$@" ;;
    status) cmd_status ;;
    -h|--help|help) usage ;;
    *) usage >&2; exit 2 ;;
esac
