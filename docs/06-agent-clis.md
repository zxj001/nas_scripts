# 6. Agent CLIs

Automated by `setup-machine --only codex,pi,claude,firstmate`.

Needs [05-dev-tools.md](05-dev-tools.md) done first (Node 22 for Pi, `gh` for FirstMate).
Install all of these as your normal user, not root.

## Codex

Standalone installer, independent of Node:

```
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex --version
codex                            # choose "Sign in with ChatGPT"
```

## Pi

```
npm install -g --ignore-scripts @earendil-works/pi-coding-agent
pi --version
```

Inside `pi`, run `/login` and pick **ChatGPT Plus/Pro (Codex)**. Use browser login at the
desktop, or device-code login over SSH. `/model` picks the model.

Config lives in `~/.pi/agent/`: `auth.json` (auth), `skills/` (global skills), and a global
`AGENTS.md`. Per-project config goes in `.pi/` and `AGENTS.md`. Never commit `auth.json` or keys.

## Claude Code

Use the native installer; the npm install is deprecated.

```
curl -fsSL https://claude.ai/install.sh | bash
```

If it warns that `~/.local/bin` isn't on PATH:

```
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

```
claude --version
claude                           # first run walks through auth
claude --continue                # resume the last conversation in this project
```

## FirstMate

Not a package: the repo itself is what you run. Clone it and start a harness inside it, which
then picks up the repo's `AGENTS.md`. Needs `gh` logged in.

```
cd ~
git clone https://github.com/kunchenguid/firstmate
cd firstmate
claude                           # or pi, codex
```

Setup defaults to `~/firstmate`. To choose another checkout location, use:

```
setup-machine --only firstmate --firstmate-dir "$HOME/projects/firstmate"
```

Use the same `--firstmate-dir` on later runs, including `--status`. For an existing
checkout at the old location, pass `--firstmate-dir "$HOME/tools/firstmate"`;
setup does not move it. Add `--yes` to install without prompting.

FirstMate's Herdr backend is experimental; use it only if you want the Herdr path.

## Everyday use

Start agents from inside the repo they should work on, inside Herdr so they survive
disconnects: see [07-herdr.md](07-herdr.md).

## References

- https://github.com/openai/codex
- https://pi.dev/ · https://github.com/earendil-works/pi
- https://github.com/anthropics/claude-code
- https://github.com/kunchenguid/firstmate
