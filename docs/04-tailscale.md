# 4. Tailscale + iPhone access

Tailscale gives the phone a private, encrypted route to the machine without opening ports
on the router.

## On the machine

```
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up                # open the printed URL to authenticate
tailscale status
tailscale ip -4                  # the 100.x.x.x address
```

Record the Tailscale IP in the machine's [Local Machines](../README.md#local-machines) entry.

## On the iPhone

1. Install Tailscale from the App Store, sign in to the same tailnet, allow the VPN profile.
2. Install **Secure ShellFish** as the SSH client.
3. Add a server: the Tailscale IP or MagicDNS name, port 22, your Debian username, key auth.
4. Generate a key in ShellFish (Ed25519, optionally Secure Enclave-backed). Add only the
   **public** key to `~/.ssh/authorized_keys` on the machine.

Test on cellular, not home Wi-Fi: `ssh YOURUSERNAME@TAILSCALE_IP`. If that connects, remote
access works.

For agent sessions that survive the phone disconnecting, see [07-herdr.md](07-herdr.md).

Next: [05-dev-tools.md](05-dev-tools.md)

## References

- https://tailscale.com/docs/install/linux
- https://tailscale.com/docs/install/ios
- https://secureshellfish.app/help/
