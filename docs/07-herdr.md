# 7. Herdr

Automated by `setup-machine --only herdr`.

Herdr keeps agent panes (Claude Code, Codex, Pi) running across SSH disconnects. tmux isn't
needed.

## Install

```
curl -fsSL https://herdr.dev/install.sh | sh
herdr --version
herdr integration install claude                    # optional: Claude Code session restore
npx skills add herdrdev/herdr --skill herdr -g      # optional: Herdr skill for agents
```

Update later with `herdr update`.

## Daily workflow

```
cd ~/projects/YOUR_PROJECT
herdr                            # start, or reattach to a running session
```

In a pane, run `claude`, `codex` or `pi`.

| Action | How |
|--------|-----|
| Detach, leaving panes running | `Ctrl+B`, then `Q` |
| Reattach after reconnecting | `herdr` |
| Stop the server and all sessions | `herdr server stop` |

From the iPhone: SSH in over [Tailscale](04-tailscale.md), run `herdr`, and detach before
closing ShellFish.

Next: [08-shellfish-widgets.md](08-shellfish-widgets.md)

## References

- https://herdr.dev/docs/
- https://github.com/herdrdev/herdr
