# 5. Dev tools

Automated by `setup-machine --only dev-tools,gh,node`.

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

## Projects directory

```
mkdir -p ~/projects ~/tools
```

Repos go in `~/projects`; FirstMate goes in `~/tools`.

Next: [06-agent-clis.md](06-agent-clis.md)

## References

- https://github.com/cli/cli
- https://github.com/nvm-sh/nvm
