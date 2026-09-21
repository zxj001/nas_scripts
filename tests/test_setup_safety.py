"""Run setup definitions in isolated homes; never install or reload host services."""
import os
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


def test_firstmate_worktree_is_done_and_existing_path_preserved(tmp_path):
    path = tmp_path / 'tools/firstmate'
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
