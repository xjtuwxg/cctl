# SSH instance management

SSH commands operate on an existing Chameleon instance. The general form is:

```sh
./cctl [global options] [LOGIN_USER@]HOST COMMAND [command options]
```

`LOGIN_USER` defaults to your local username. New Chameleon instances normally start with the `cc` user, so specifying `cc@HOST` is recommended for initial setup.

## Provision a user

Create or update a Linux user, add it to `sudo` or `wheel`, and install a public key:

```sh
./cctl cc@192.5.86.156 adduser \
  --user llamacpp \
  --pubkey ~/.ssh/id_ed25519.pub
```

By default, `cctl` prompts for a new password. To configure SSH-only access without changing the password:

```sh
./cctl cc@192.5.86.156 adduser \
  --user llamacpp \
  --pubkey ~/.ssh/id_ed25519.pub \
  --no-password
```

After provisioning, verify login and sudo access:

```sh
ssh llamacpp@192.5.86.156
./cctl llamacpp@192.5.86.156 sudo
```

## Password options

Interactive prompt, the default:

```sh
./cctl cc@HOST adduser --user USER --pubkey ~/.ssh/id_ed25519.pub
```

Read the password from standard input:

```sh
printf '%s\n' 'temporary-password' | \
  ./cctl cc@HOST adduser --user USER \
    --pubkey ~/.ssh/id_ed25519.pub --password-stdin
```

Require a change at the first password login:

```sh
./cctl cc@HOST adduser --user USER \
  --pubkey ~/.ssh/id_ed25519.pub --force-password-change
```

Avoid `--password PASSWORD` when possible because the password can remain in shell history or process listings.

## Common commands

Show basic system status:

```sh
./cctl USER@HOST status
```

Inspect GPUs, the NVIDIA driver, CUDA libraries and toolkit, Python GPU packages, and the container runtime:

```sh
./cctl USER@HOST gpu
```

Run an arbitrary remote command:

```sh
./cctl USER@HOST run -- whoami
```

Add another public key. The target user defaults to the SSH login user:

```sh
./cctl USER@HOST addkey --pubkey ~/.ssh/work_key.pub
./cctl ADMIN@HOST addkey --user USER --pubkey 'ssh-ed25519 AAAA... comment'
```

Install fish and make it the target user's login shell:

```sh
./cctl USER@HOST fish
./cctl ADMIN@HOST fish --user USER
```

Allow passwordless sudo. This writes `/etc/sudoers.d/USER-nopasswd` and validates the result with `visudo`:

```sh
./cctl USER@HOST nopasswd
```

Commands that change the remote machine prompt once for the SSH login user's sudo password unless passwordless sudo is already configured.

## Global SSH options

| Option | Purpose |
| --- | --- |
| `--user USER` | Set the SSH login user instead of using `USER@HOST` |
| `--port PORT` | Use a non-default SSH port |
| `--identity-file PATH` | Select a private key |
| `--ssh-option OPTION` | Pass an extra SSH option; may be repeated |
| `--replace-host-key` / `-R` | Remove a stale entry from `known_hosts` before connecting |
| `--yes` / `-y` | Skip confirmation prompts |

Example with an explicit private key:

```sh
./cctl cc@HOST \
  --identity-file ~/.ssh/chameleon_key \
  adduser --user USER --pubkey ~/.ssh/id_ed25519.pub
```

Before the host, `--user` selects the SSH login user. After commands such as `adduser`, `addkey`, `sudo`, or `fish`, it selects that command's target user. Prefer `LOGIN_USER@HOST` to keep the distinction clear.

## Reused IP addresses

Chameleon may assign an old IP address to a new instance. If SSH reports `REMOTE HOST IDENTIFICATION HAS CHANGED` and you have confirmed that the instance was replaced, remove the stale key while connecting:

```sh
./cctl --replace-host-key USER@HOST status
```

Use `--yes` only when you already expect the host identity to have changed.

## Compatibility aliases

Older command names remain available:

| Current | Aliases |
| --- | --- |
| `adduser` | `init`, `provision-user` |
| `addkey` | `key`, `add-key` |
| `sudo` | `sudo-test` |
| `gpu` | `gpu-info` |
