"""Run setup definitions in isolated homes; never install or reload host services."""
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = (Path(__file__).resolve().parents[1] / 'scripts/setup.sh').read_text()
DEFINITIONS = SOURCE.removesuffix('main "$@"\n')


def run(tmp_path, body, *, success=True):
    # All privileged paths go into the fixture. GNU ln -T is emulated on macOS.
    prelude = r'''
OS=debian
REPO_DIR="$HOME/tools/nas_scripts"
detect_os() { OS=debian; }
tool_path() { :; }
curl() { echo 'unexpected download' >&2; return 99; }
# Tests exercise one step; the dependency tests put real_add_deps back.
eval "real_$(declare -f add_deps)"
add_deps() { :; }
sudo() {
    local arg
    local args=()
    for arg in "$@"; do
        case "$arg" in /etc/*) arg="$HOME$arg" ;; esac
        args+=("$arg")
    done
    if [ "${args[0]}" = ln ] && [ "${args[1]:-}" = -T ]; then
        "$TEST_PYTHON" -c 'import os,sys; os.link(sys.argv[1],sys.argv[2])' "${args[2]}" "${args[3]}"
    else
        case "${args[0]}" in
            test|cmp|mktemp|install|mkdir|rm) command "${args[@]}" ;;
            *) echo "unexpected privileged command: ${args[*]}" >&2; return 99 ;;
        esac
    fi
}
'''
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path), 'SETUP_UPDATED': '1',
           'NVM_DIR': str(tmp_path / '.nvm'), 'TEST_PYTHON': sys.executable}
    result = subprocess.run(['bash', '-c', DEFINITIONS + prelude + body],
                            env=env, text=True, capture_output=True)
    if success:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr
    return result


def test_tailscale_bootstraps_https_and_second_run_skips(tmp_path):
    run(tmp_path, r'''
have() { [ "$1" = tailscale ] && [ -f "$HOME/installed" ]; }
check_tailscale() { [ -f "$HOME/connected" ]; }
sudo() {
    echo "$*" >>"$HOME/calls"
    case "$*" in
        'apt-get update') touch "$HOME/updated" ;;
        'apt-get install -y curl ca-certificates')
            test -f "$HOME/updated"
            touch "$HOME/https"
            ;;
        'tailscale up') test -f "$HOME/installed"; touch "$HOME/connected" ;;
        *) return 1 ;;
    esac
}
curl() {
    test -f "$HOME/https" || return 127
    printf 'touch "$HOME/installed"\n' >"$4"
}
main --yes --only tailscale
cp "$HOME/calls" "$HOME/first-calls"
main --yes --only tailscale
cmp "$HOME/calls" "$HOME/first-calls"
''')
    assert (tmp_path / 'connected').exists()


def test_installed_tailscale_only_connects(tmp_path):
    run(tmp_path, r'''
have() { [ "$1" = tailscale ]; }
run_installer() { exit 90; }
sudo() { [ "$*" = 'tailscale up' ]; }
do_tailscale
''')


def test_failed_download_never_executes_partial_script(tmp_path):
    run(tmp_path, r'''
ensure_https() { :; }
curl() { printf 'touch "$HOME/executed"\n' >"$4"; return 22; }
run_installer https://example.invalid/install.sh bash
''', success=False)
    assert not (tmp_path / 'executed').exists()


@pytest.mark.parametrize('kind', ['file', 'directory', 'symlink', 'dangling'])
def test_config_conflicts_preserved(tmp_path, kind):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.write_text('new settings\n')
    original = tmp_path / 'original'
    original.write_text('user settings\n')
    if kind == 'file':
        target.write_text('user settings\n')
    elif kind == 'directory':
        target.mkdir()
    else:
        target.symlink_to(original if kind == 'symlink' else tmp_path / 'missing')
    run(tmp_path, 'create_config "$HOME/source" "$HOME/target"', success=False)
    assert original.read_text() == 'user settings\n'
    if kind == 'file':
        assert target.read_text() == 'user settings\n'
    elif kind == 'directory':
        assert list(target.iterdir()) == []
    else:
        assert target.is_symlink()


def test_config_second_run_does_not_rewrite(tmp_path):
    (tmp_path / 'source').write_text('settings\n')
    run(tmp_path, 'create_config "$HOME/source" "$HOME/target"')
    before = (tmp_path / 'target').stat()
    run(tmp_path, 'create_config "$HOME/source" "$HOME/target"; test "$CONFIG_CREATED" = 0')
    after = (tmp_path / 'target').stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)


@pytest.mark.parametrize('kind', ['file', 'directory', 'symlink', 'dangling'])
def test_existing_launcher_preserved(tmp_path, kind):
    repo = tmp_path / 'tools/nas_scripts/.git'
    repo.mkdir(parents=True)
    launcher = tmp_path / '.local/bin/setup-machine'
    launcher.parent.mkdir(parents=True)
    if kind == 'file':
        launcher.write_text('my launcher')
    elif kind == 'directory':
        launcher.mkdir()
    else:
        launcher.symlink_to(tmp_path / 'other')
    before = launcher.lstat()
    run(tmp_path, 'install_repo; install_repo')
    assert launcher.lstat().st_ino == before.st_ino
    if kind == 'file':
        assert launcher.read_text() == 'my launcher'
    elif kind == 'directory':
        assert list(launcher.iterdir()) == []


def test_launcher_created_once(tmp_path):
    (tmp_path / 'tools/nas_scripts/.git').mkdir(parents=True)
    run(tmp_path, 'install_repo; install_repo')
    assert (tmp_path / '.local/bin/setup-machine').readlink() == tmp_path / 'tools/nas_scripts/scripts/setup.sh'


def test_status_and_invalid_steps_never_self_update(tmp_path):
    body = 'unset SETUP_UPDATED\nself_update() { touch "$HOME/updated"; exit 99; }\n'
    run(tmp_path, body + 'main --status --only firstmate')
    run(tmp_path, body + 'main --yes --only nonexistent', success=False)
    assert not (tmp_path / 'updated').exists()


@pytest.mark.parametrize('scenario', ['dirty', 'branch', 'origin'])
def test_custom_checkout_not_pulled(tmp_path, scenario):
    (tmp_path / 'tools/nas_scripts/.git').mkdir(parents=True)
    run(tmp_path, 'SCENARIO=' + shlex.quote(scenario) + r'''
unset SETUP_UPDATED
git() {
    case "$3" in
        remote) if [ "$SCENARIO" = origin ]; then echo https://example.org/custom; else echo "$REPO_URL"; fi ;;
        symbolic-ref) if [ "$SCENARIO" = branch ]; then echo my-branch; else echo main; fi ;;
        status) if [ "$SCENARIO" = dirty ]; then echo ' M scripts/setup.sh'; fi ;;
        *) touch "$HOME/unexpected-git"; return 99 ;;
    esac
}
self_update
''')
    assert not (tmp_path / 'unexpected-git').exists()


def test_profiles_preserve_content_and_do_not_duplicate(tmp_path):
    profile = tmp_path / '.zprofile'
    profile.write_text('# my settings without a trailing newline')
    run(tmp_path, 'append_once "export CUSTOM=yes" "$HOME/.zprofile"\n' * 2)
    assert profile.read_text() == '# my settings without a trailing newline\nexport CUSTOM=yes\n'


def test_node_preserves_custom_nvm_and_default(tmp_path):
    nvm = tmp_path / 'custom-nvm'
    (nvm / 'alias').mkdir(parents=True)
    (nvm / 'alias/default').write_text('20\n')
    (nvm / 'nvm.sh').write_text('nvm() { [ "$*" = "install 22" ]; }\n')
    run(tmp_path, r'''
NVM_DIR="$HOME/custom-nvm"
run_installer() { exit 90; }
do_node
''')
    assert (nvm / 'alias/default').read_text() == '20\n'
    assert not (tmp_path / '.nvm').exists()


def test_incomplete_nvm_directory_preserved(tmp_path):
    (tmp_path / '.nvm').mkdir()
    (tmp_path / '.nvm/keep').write_text('my data')
    run(tmp_path, 'do_node', success=False)
    assert (tmp_path / '.nvm/keep').read_text() == 'my data'


@pytest.mark.parametrize('destination', ['firstmate', 'custom tools/firstmate'])
def test_firstmate_install_location_and_repeat(tmp_path, destination):
    options = '' if destination == 'firstmate' else ' --firstmate-dir ' + shlex.quote(str(tmp_path / destination))
    body = r'''
git() {
    [ "$1" = clone ] || exit 99
    [ "$2" = https://github.com/kunchenguid/firstmate ] || exit 99
    mkdir -p "$3/.git"
    echo clone >>"$HOME/clones"
}
'''
    run(tmp_path, body + 'main --yes --only firstmate' + options)
    result = run(tmp_path, body + 'main --status --only firstmate' + options)
    assert 'done' in result.stdout
    run(tmp_path, body + 'main --yes --only firstmate' + options)
    assert (tmp_path / destination / '.git').is_dir()
    assert (tmp_path / 'clones').read_text() == 'clone\n'


@pytest.mark.parametrize('argument', ['', "''", '--yes'])
def test_firstmate_missing_path_rejected_before_update(tmp_path, argument):
    result = run(tmp_path, r'''
self_update() { touch "$HOME/updated"; }
main --firstmate-dir ''' + argument, success=False)
    assert result.returncode == 2
    assert not (tmp_path / 'updated').exists()


def test_firstmate_relative_path(tmp_path):
    run(tmp_path, r'''
cd "$HOME"
parse_args --firstmate-dir 'custom tools/firstmate'
test "$FIRSTMATE_DIR" = "$HOME/custom tools/firstmate"
''')


def test_firstmate_custom_occupied_path_preserved(tmp_path):
    (tmp_path / 'custom').symlink_to(tmp_path / 'missing')
    run(tmp_path, 'main --yes --only firstmate --firstmate-dir "$HOME/custom"', success=False)
    assert (tmp_path / 'custom').is_symlink()


def test_firstmate_worktree_is_done_and_existing_path_preserved(tmp_path):
    path = tmp_path / 'firstmate'
    path.mkdir(parents=True)
    (path / '.git').write_text('gitdir: /some/worktree\n')
    run(tmp_path, 'check_firstmate; do_firstmate')
    (path / '.git').unlink()
    (path / 'keep').write_text('my data')
    run(tmp_path, 'do_firstmate', success=False)
    assert (path / 'keep').read_text() == 'my data'


def test_ssh_hardening_preserves_custom_config_and_continues(tmp_path):
    config = tmp_path / 'etc/ssh/sshd_config.d/99-local.conf'
    config.parent.mkdir(parents=True)
    config.write_text('# user config\nPasswordAuthentication yes\n')
    result = run(tmp_path, r'''
eval "$(declare -f sudo | sed '1s/sudo/file_sudo/')"
sudo() {
    case "$1" in
        /usr/sbin/sshd)
            if [ "$2" = -T ]; then echo 'passwordauthentication yes'; fi
            ;;
        systemctl) exit 99 ;;
        *) file_sudo "$@" ;;
    esac
}
check_ssh_harden() { return 1; }
check_firstmate() { return 1; }
do_firstmate() { touch "$HOME/continued"; }
main --yes --only ssh-harden,firstmate
''')
    assert config.read_text() == '# user config\nPasswordAuthentication yes\n'
    assert 'hardening skipped' in result.stdout
    assert (tmp_path / 'continued').exists()


def test_ssh_invalid_new_config_is_rolled_back(tmp_path):
    run(tmp_path, r'''
id() { echo 1000; }
ssh-keygen() { return 0; }
# Extend the fixture sudo for service calls, retaining safe file operations.
eval "$(declare -f sudo | sed '1s/sudo/file_sudo/')"
sudo() {
    case "$1" in /usr/sbin/sshd) return 1 ;; systemctl) return 0 ;; *) file_sudo "$@" ;; esac
}
do_ssh_harden
''', success=False)
    assert not (tmp_path / 'etc/ssh/sshd_config.d/99-local.conf').exists()


def test_runner_rechecks_after_prior_steps(tmp_path):
    run(tmp_path, r'''
STEPS=(dev-tools firstmate)
check_dev_tools() { return 1; }
check_firstmate() { test -f "$HOME/ready"; }
do_dev_tools() { touch "$HOME/ready"; }
do_firstmate() { exit 90; }
main --yes
''')


def test_fresh_custom_nvm_directory_is_created_for_installer(tmp_path):
    run(tmp_path, r'''
NVM_DIR="$HOME/custom-nvm"
run_installer() {
    test -d "$NVM_DIR" || return 1
    printf 'nvm() { :; }\n' >"$NVM_DIR/nvm.sh"
}
do_node
''')


def test_herdr_preserves_existing_claude_settings(tmp_path):
    settings = tmp_path / '.claude/settings.json'
    settings.parent.mkdir()
    settings.write_text('{"hooks": {"my-hook": true}}\n')
    run(tmp_path, r'''
run_installer() { :; }
have() { return 0; }
herdr() { exit 99; }
do_herdr
''')
    assert settings.read_text() == '{"hooks": {"my-hook": true}}\n'


def test_gh_preserves_key_and_conflicting_sources(tmp_path):
    key = tmp_path / 'etc/apt/keyrings/githubcli-archive-keyring.gpg'
    key.parent.mkdir(parents=True)
    key.write_bytes(b'my keyring')
    sources = tmp_path / 'etc/apt/sources.list.d/github-cli.list'
    sources.parent.mkdir(parents=True)
    sources.write_text('# my custom apt repository\n')
    run(tmp_path, r'''
ensure_https() { :; }
dpkg() { echo amd64; }
do_gh
''', success=False)
    assert key.read_bytes() == b'my keyring'
    assert sources.read_text() == '# my custom apt repository\n'


def test_failed_gh_key_download_publishes_nothing(tmp_path):
    run(tmp_path, r'''
ensure_https() { :; }
curl() { printf 'partial key' >"$4"; return 22; }
do_gh
''', success=False)
    assert not (tmp_path / 'etc/apt/keyrings/githubcli-archive-keyring.gpg').exists()
    assert not (tmp_path / 'etc/apt/sources.list.d/github-cli.list').exists()


@pytest.mark.parametrize('existing', [False, True])
def test_gpu_config_survives_repeat_or_conflict(tmp_path, existing):
    sources = tmp_path / 'etc/apt/sources.list.d/nonfree.sources'
    sources.parent.mkdir(parents=True)
    if existing:
        sources.write_text('# custom repository\nComponents: main\n')
    body = r'''
have() { return 1; }
grep() {
    local arg
    local args=()
    for arg in "$@"; do
        case "$arg" in /etc/*) arg="$HOME$arg" ;; esac
        args+=("$arg")
    done
    command grep "${args[@]}"
}
eval "$(declare -f sudo | sed '1s/sudo/file_sudo/')"
sudo() { if [ "$1" = apt-get ]; then return 0; else file_sudo "$@"; fi; }
do_gpu
'''
    run(tmp_path, body, success=not existing)
    before = sources.read_bytes()
    run(tmp_path, body, success=not existing)
    assert sources.read_bytes() == before
    if existing:
        assert before == b'# custom repository\nComponents: main\n'
    else:
        assert b'Components: contrib non-free non-free-firmware' in before


def test_partial_sleep_mask_is_todo(tmp_path):
    run(tmp_path, r'''
systemctl() { if [ "$2" = sleep.target ]; then echo masked; else echo static; fi; }
check_no_sleep
''', success=False)


def test_ssh_reload_failure_preserves_preexisting_identical_config(tmp_path):
    config = tmp_path / 'etc/ssh/sshd_config.d/99-local.conf'
    config.parent.mkdir(parents=True)
    config.write_text('PermitRootLogin no\nPubkeyAuthentication yes\nPasswordAuthentication no\n')
    before = config.read_bytes()
    run(tmp_path, r'''
id() { echo 1000; }
ssh-keygen() { return 0; }
eval "$(declare -f sudo | sed '1s/sudo/file_sudo/')"
sudo() {
    case "$1" in
        /usr/sbin/sshd)
            if [ "$2" = -T ]; then printf '%s\n' 'permitrootlogin no' 'pubkeyauthentication yes' 'passwordauthentication no'; fi
            ;;
        systemctl) return 1 ;;
        *) file_sudo "$@" ;;
    esac
}
do_ssh_harden
''', success=False)
    assert config.read_bytes() == before


def test_ssh_effective_policy_accepts_comments_and_other_settings(tmp_path):
    config = tmp_path / 'etc/ssh/sshd_config.d/99-local.conf'
    config.parent.mkdir(parents=True)
    original = '# local policy\nPasswordAuthentication no\nPort 2222\nPermitRootLogin no\nPubkeyAuthentication yes\n'
    config.write_text(original)
    run(tmp_path, r'''
eval "$(declare -f sudo | sed '1s/sudo/file_sudo/')"
sudo() {
    if [ "$1" = -n ]; then shift; fi
    case "$1" in
        /usr/sbin/sshd)
            if [ "$2" = -T ]; then printf '%s\n' 'port 2222' 'passwordauthentication no' 'permitrootlogin no' 'pubkeyauthentication yes'; fi
            ;;
        systemctl) touch "$HOME/reloaded" ;;
        *) file_sudo "$@" ;;
    esac
}
check_ssh_harden
do_ssh_harden
do_ssh_harden
''')
    assert config.read_text() == original
    assert (tmp_path / 'reloaded').exists()


def test_ssh_effective_policy_override_is_not_done(tmp_path):
    run(tmp_path, r'''
sudo() {
    [ "$*" = '-n /usr/sbin/sshd -T' ] || exit 99
    printf '%s\n' 'permitrootlogin no' 'pubkeyauthentication yes' 'passwordauthentication yes'
}
check_ssh_harden
''', success=False)


def test_ssh_status_does_not_prompt_for_sudo(tmp_path):
    run(tmp_path, r'''
sudo() { [ "$1" = -n ] || exit 99; return 1; }
main --status --only ssh-harden
''')


def test_new_ssh_config_overridden_elsewhere_is_rolled_back(tmp_path):
    run(tmp_path, r'''
id() { echo 1000; }
ssh-keygen() { return 0; }
eval "$(declare -f sudo | sed '1s/sudo/file_sudo/')"
sudo() {
    case "$1" in
        /usr/sbin/sshd) if [ "$2" = -T ]; then echo 'passwordauthentication yes'; fi ;;
        systemctl) return 0 ;;
        *) file_sudo "$@" ;;
    esac
}
do_ssh_harden
''', success=False)
    assert not (tmp_path / 'etc/ssh/sshd_config.d/99-local.conf').exists()


def test_desktop_power_restore_prints_firmware_guidance_without_changes(tmp_path):
    result = run(tmp_path, r'''
power_restore_virtual() { return 1; }
local_ipmi() { return 1; }
sudo() { exit 99; }
main --status --only power-restore
main --yes --only power-restore
''')
    assert 'manual' in result.stdout
    assert 'BIOS/UEFI' in result.stdout
    assert 'has not been verified' in result.stdout
    assert 'is enabled' not in result.stdout


def test_vm_power_restore_is_not_applicable(tmp_path):
    result = run(tmp_path, r'''
power_restore_virtual() { return 0; }
sudo() { exit 99; }
main --yes --only power-restore
''')
    assert 'n/a' in result.stdout


def test_ipmi_power_restore_verified_and_second_run_does_nothing(tmp_path):
    run(tmp_path, r'''
power_restore_virtual() { return 1; }
local_ipmi() { return 0; }
have() { [ "$1" = ipmitool ] && [ -f "$HOME/ipmitool" ]; }
sudo() {
    if [ "$1" = -n ]; then shift; fi
    case "$*" in
        'apt-get update') touch "$HOME/updated" ;;
        'apt-get install -y ipmitool') test -f "$HOME/updated"; touch "$HOME/ipmitool" ;;
        'modprobe ipmi_devintf') : ;;
        'ipmitool -I open chassis policy always-on') echo set >>"$HOME/writes"; touch "$HOME/enabled" ;;
        'ipmitool -I open chassis status')
            if [ -f "$HOME/enabled" ]; then echo 'Power Restore Policy : always-on'; else echo 'Power Restore Policy : previous'; fi
            ;;
        *) exit 99 ;;
    esac
}
main --yes --only power-restore
main --yes --only power-restore
''')
    assert (tmp_path / 'writes').read_text() == 'set\n'


def test_ipmi_unverified_policy_fails(tmp_path):
    run(tmp_path, r'''
power_restore_virtual() { return 1; }
local_ipmi() { return 0; }
have() { return 0; }
sudo() { if [ "${*: -2}" = 'chassis status' ]; then echo 'Power Restore Policy : always-off'; fi; }
do_power_restore
''', success=False)


def test_mac_power_restore_preserves_other_settings_and_skips_repeat(tmp_path):
    run(tmp_path, r'''
detect_os() { OS=macos; }
power_restore_virtual() { return 1; }
pmset() {
    case "$*" in
        '-g cap') printf 'Capabilities:\n autorestart\n' ;;
        '-g custom')
            if [ -f "$HOME/enabled" ]; then echo ' autorestart 1'; else echo ' autorestart 0'; fi
            echo ' sleep 30'
            ;;
        *) exit 99 ;;
    esac
}
sudo() {
    [ "$*" = 'pmset -a autorestart 1' ] || exit 99
    echo set >>"$HOME/writes"
    touch "$HOME/enabled"
}
main --yes --only power-restore
main --yes --only power-restore
''')
    assert (tmp_path / 'writes').read_text() == 'set\n'


def test_unsupported_mac_power_restore_remains_manual(tmp_path):
    result = run(tmp_path, r'''
detect_os() { OS=macos; }
power_restore_virtual() { return 1; }
pmset() { echo 'sleep'; }
sudo() { exit 99; }
main --yes --only power-restore
''')
    assert 'manual' in result.stdout
    assert 'has not been verified' in result.stdout


def test_power_restore_status_does_not_modify_hardware(tmp_path):
    run(tmp_path, r'''
power_restore_virtual() { return 1; }
local_ipmi() { return 0; }
have() { return 0; }
sudo() { [ "$*" = '-n ipmitool -I open chassis status' ] || exit 99; return 1; }
main --status --only power-restore
''')


SHELLFISH_STUBS = r'''
crontab() {
    case "$1" in
        -l)
            # Some crontabs warn on stderr and still succeed.
            echo "crontab: warning on stderr" >&2
            if [ -f "$HOME/crontab" ]; then cat "$HOME/crontab"; else echo "no crontab for test" >&2; return 1; fi
            ;;
        -) cat >"$HOME/crontab"; echo write >>"$HOME/crontab-writes" ;;
        *) exit 99 ;;
    esac
}
mkdir -p "$REPO_DIR/scripts"
printf '#!/bin/sh\necho sent >>"$HOME/sent"\n' >"$REPO_DIR/scripts/shellfish_widget.sh"
chmod +x "$REPO_DIR/scripts/shellfish_widget.sh"
'''


def test_shellfish_without_shell_integration_is_manual(tmp_path):
    result = run(tmp_path, SHELLFISH_STUBS + r'''
sudo() { exit 99; }
main --status --only shellfish
main --yes --only shellfish
''')
    assert 'manual' in result.stdout
    assert 'Install Shell Integration' in result.stdout
    assert not (tmp_path / 'crontab').exists()
    assert not (tmp_path / 'sent').exists()


def test_shellfish_installs_tools_keeps_crontab_and_repeats_cleanly(tmp_path):
    (tmp_path / '.shellfishrc').write_text('widget() { :; }\n')
    (tmp_path / 'crontab').write_text('0 3 * * * backup\n')
    run(tmp_path, SHELLFISH_STUBS + r'''
have() { [ "$1" != xxd ] || [ -f "$HOME/xxd" ]; }
sudo() {
    case "$*" in
        'apt-get update') touch "$HOME/updated" ;;
        'apt-get install -y openssl xxd curl cron') test -f "$HOME/updated"; touch "$HOME/xxd" ;;
        *) exit 99 ;;
    esac
}
main --yes --only shellfish
main --yes --only shellfish
main --status --only shellfish | grep -Eq '^shellfish +done$'
''')
    lines = (tmp_path / 'crontab').read_text().splitlines()
    assert lines[0] == '0 3 * * * backup'
    assert len(lines) == 2 and lines[1].startswith('*/15 * * * * ')
    assert 'scripts/shellfish_widget.sh' in lines[1]
    assert (tmp_path / 'crontab-writes').read_text() == 'write\n'
    assert (tmp_path / 'sent').read_text() == 'sent\n'


def test_shellfish_unreadable_crontab_left_alone(tmp_path):
    (tmp_path / '.shellfishrc').write_text('')
    run(tmp_path, SHELLFISH_STUBS + r'''
have() { return 0; }
crontab() {
    case "$1" in
        -l) echo 'crontab: permission denied' >&2; return 1 ;;
        *) touch "$HOME/overwritten" ;;
    esac
}
do_shellfish
''', success=False)
    assert not (tmp_path / 'overwritten').exists()


def test_shellfish_not_applicable_on_macos(tmp_path):
    result = run(tmp_path, r'''
detect_os() { OS=macos; }
main --yes --only shellfish
''')
    assert 'n/a' in result.stdout


WIDGET = Path(__file__).resolve().parents[1] / 'scripts/shellfish_widget.sh'


@pytest.mark.skipif(not Path('/proc/stat').exists(), reason='reads /proc')
def test_shellfish_widget_print_sends_nothing(tmp_path):
    result = subprocess.run([str(WIDGET), '--print', '--name', 'NAS', '/', str(tmp_path)],
                            env={'PATH': os.environ['PATH'], 'HOME': str(tmp_path)},
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    words = result.stdout.split()
    assert words[:3] == ['server.rack', '--text', 'NAS']
    # icon, color, value, foreground, label per metric; Temp only with a sensor
    color = r'#[0-9a-f]{6}'
    pattern = (rf'cpu\.fill {color} \d+% foreground CPU '
               rf'(thermometer\.medium {color} \d+°C foreground Temp )?'
               rf'memorychip {color} \d+% foreground Mem '
               rf'internaldrive {color} \d+% foreground Disk '
               rf'internaldrive {color} \d+% foreground {re.escape(tmp_path.name)}')
    rest = ' '.join(words[3:])
    assert re.fullmatch(pattern, rest), rest


def test_shellfish_widget_colors_by_level(tmp_path):
    definitions = WIDGET.read_text().removesuffix('main "$@"\n')
    result = subprocess.run(['bash', '-c', definitions + r'''
[ "$(level_color 74% 75 90)" = "$GREEN" ]
[ "$(level_color 75% 75 90)" = "$ORANGE" ]
[ "$(level_color 90% 75 90)" = "$RED" ]
[ "$(level_color 71°C 70 85)" = "$ORANGE" ]
'''], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('missing', ['pip', 'venv'])
def test_dev_tools_needs_python_pip_and_venv(tmp_path, missing):
    run(tmp_path, r'''
mkdir -p "$HOME/projects" "$HOME/tools"
have() { return 0; }
dpkg-query() { echo 'install ok installed'; }
python3() {
    case "$*" in
        '-m pip --version') [ "$MISSING" != pip ] ;;
        *) [ "$MISSING" != venv ] ;;
    esac
}
MISSING=none check_dev_tools
MISSING=''' + missing + r''' check_dev_tools && exit 1 || true
''')


@pytest.mark.skipif(not Path('/proc/stat').exists(), reason='reads /proc')
def test_shellfish_widget_tolerates_shellfishrc_unset_variables(tmp_path):
    # The real file reads $TMUX and friends, which cron leaves unset.
    (tmp_path / '.shellfishrc').write_text(
        'if [[ -n "$TMUX" ]]; then :; fi\nfalse\n'
        'widget() { echo "$*" >"$HOME/sent"; }\n')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for tool in ('openssl', 'xxd', 'curl'):
        (bin_dir / tool).write_text('#!/bin/sh\n')
        (bin_dir / tool).chmod(0o755)
    script = Path(__file__).resolve().parents[1] / 'scripts/shellfish_widget.sh'
    result = subprocess.run([str(script)], text=True, capture_output=True,
                            env={'PATH': f'{bin_dir}:/usr/bin:/bin', 'HOME': str(tmp_path)})
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'sent').read_text().startswith('server.rack --text ')


@pytest.mark.parametrize('os_name', ['debian', 'macos'])
def test_step_deps_are_registered_and_come_first(tmp_path, os_name):
    run(tmp_path, 'OS=' + os_name + r'''
for step in "${STEPS[@]}"; do
    for dep in $(step_deps "$step"); do
        seen=0
        for s in "${STEPS[@]}"; do
            [ "$s" = "$step" ] && break
            [ "$s" = "$dep" ] && seen=1
        done
        [ "$seen" = 1 ] || { echo "$step needs $dep, which is not before it" >&2; exit 1; }
    done
done
''')


DEP_STUBS = r'''
STEPS=(sudo ssh dev-tools node pi shellfish)
for s in sudo ssh dev-tools node pi shellfish; do
    f=${s//-/_}
    eval "check_$f() { [ -f \"\$HOME/done-$s\" ]; }"
    eval "do_$f() { echo $s >>\"\$HOME/ran\"; touch \"\$HOME/done-$s\"; }"
    eval "default_$f() { echo yes; }"
done
signin_list() { :; }
add_deps() { real_add_deps; }
'''


def test_only_adds_unfinished_deps_transitively(tmp_path):
    (tmp_path / 'done-sudo').touch()
    (tmp_path / 'done-ssh').touch()
    result = run(tmp_path, DEP_STUBS + 'main --status --only pi,shellfish')
    assert 'pi needs node' in result.stderr
    assert 'shellfish needs dev-tools' in result.stderr
    assert 'ssh' not in result.stderr  # done deps are not mentioned
    steps = [line.split()[0] for line in result.stdout.splitlines()[1:]]
    assert steps == ['dev-tools', 'node', 'pi', 'shellfish']
    assert not (tmp_path / 'ran').exists()


def test_only_yes_runs_deps_before_the_step(tmp_path):
    run(tmp_path, DEP_STUBS + 'main --yes --only shellfish')
    assert (tmp_path / 'ran').read_text().split() == ['sudo', 'ssh', 'dev-tools', 'shellfish']


def test_without_only_nothing_is_added(tmp_path):
    result = run(tmp_path, DEP_STUBS + 'main --status')
    assert 'needs' not in result.stderr
