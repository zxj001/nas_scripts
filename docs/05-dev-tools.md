# 5. Dev tools

Automated by `setup-machine --only dev-tools,gh,node,docker,chromium`.

## Basics

```
sudo apt install -y git curl ca-certificates build-essential jq ripgrep \
    python3 python3-pip python3-venv
git --version
python3 -m pip --version
```

Debian's Python refuses `pip install` outside a virtual environment. Use a venv per
project:

```
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

The repo's own tests: `python3 -m venv .venv && .venv/bin/pip install pytest && .venv/bin/python -m pytest`.
On macOS, `setup-machine` installs Homebrew's `python`, which includes pip.

## GitHub CLI

From GitHub's official apt repo:

```
sudo apt update
sudo apt install -y wget
sudo mkdir -p -m 755 /etc/apt/keyrings
out=$(mktemp)
wget -nv -O$out https://cli.github.com/packages/githubcli-archive-keyring.gpg
cat $out | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null
sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
sudo mkdir -p -m 755 /etc/apt/sources.list.d
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
sudo apt update
sudo apt install -y gh
```

```
gh auth login
gh auth status
```

## Node 22 via nvm

Debian 13 ships Node 20. Pi needs 22.19.0 or newer, so don't use the apt `nodejs` package.
Install as your normal user:

```
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.7/install.sh | bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"   # or open a new terminal

nvm install 22
nvm alias default 22
node --version                   # >= 22.19.0
```

## Docker

Debian's own packages: the engine, its CLI and the Compose plugin (`docker compose`).

```
sudo apt install -y docker.io docker-cli docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Log out and back in so the `docker` group applies, then check it works without sudo:

```
docker compose version
docker run --rm hello-world
```

Setup reports `docker` as deferred until that new login. It keeps an existing Docker
CE install from Docker's own apt repository, whose packages conflict with Debian's; if
that install lacks the Compose plugin, setup reports it as manual (install
`docker-compose-plugin` from Docker's repository).

On macOS, setup installs Docker Desktop (`brew install --cask docker-desktop`). Its
engine starts only after you open Docker.app and accept the terms, so `--status`
shows `docker` as manual until then. When another engine such as OrbStack or Colima
already provides a `docker` command, setup never installs Docker Desktop over it; if
that engine is stopped, `docker` is manual until you start it.

## Chromium

For headless browser checks:

```
sudo apt install -y chromium
chromium --headless --dump-dom https://example.com
```

Homebrew no longer ships Chromium for macOS (its unsigned cask is disabled), so on
a Mac setup installs Google Chrome, the signed Chromium build
(`brew install --cask google-chrome`). An existing Chromium.app or Google Chrome.app
counts as done.

## Projects directory

```
mkdir -p ~/projects ~/tools
```

Repos go in `~/projects`; FirstMate goes in `~/tools`.

Next: [06-agent-clis.md](06-agent-clis.md)

## References

- https://github.com/cli/cli
- https://github.com/nvm-sh/nvm
- https://docs.docker.com/engine/install/linux-postinstall/
