# Chameleon Control

`cctl` is a local helper for managing a Chameleon Cloud instance over SSH.

The initial workflow is:

1. SSH to the instance as the existing `cc` user.
2. Create or update a Linux user.
3. Add the user to the remote sudo group.
4. Install the user's public SSH key into `~/.ssh/authorized_keys`.
5. Optionally set the user's password.

## Quick Start

Make the script executable:

```sh
chmod +x ./cctl
```

Provision a user:

```sh
./cctl cc@192.5.86.156 adduser \
  --user llamacpp \
  --pubkey ~/.ssh/id_ed25519.pub
```

The script prompts for the new password by default. After it finishes, test login:

```sh
ssh llamacpp@192.5.86.156
```

Verify sudo access:

```sh
./cctl llamacpp@192.5.86.156 sudo
```

## Common Commands

Show basic instance status:

```sh
./cctl 192.5.86.156 status
```

Check GPU hardware, NVIDIA driver state, CUDA toolkit/libraries, Python GPU packages, and container GPU runtime:

```sh
./cctl xiaoguang@192.5.86.157 gpu
```

Install fish and set it as the login user's default shell:

```sh
./cctl xiaoguang@192.5.86.157 fish
```

Add another public key to an existing user:

```sh
./cctl llamacpp@192.5.86.156 addkey \
  --pubkey ~/.ssh/work_key.pub
```

You can also paste a public key directly:

```sh
./cctl llamacpp@192.5.86.156 addkey \
  --pubkey 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... xiaoguang@laptop'
```

Run a remote command as the login user:

```sh
./cctl 192.5.86.156 run -- whoami
```

Use a private key and an explicit login user:

```sh
./cctl cc@192.5.86.156 \
  --identity-file ~/.ssh/chameleon_key \
  adduser --user llamacpp --pubkey ~/.ssh/id_ed25519.pub
```

The `-u` option is still available if you prefer to keep the login user separate:

```sh
./cctl -u cc 192.5.86.156 \
  --identity-file ~/.ssh/chameleon_key \
  adduser --user llamacpp --pubkey ~/.ssh/id_ed25519.pub
```

If a reused instance IP reports `REMOTE HOST IDENTIFICATION HAS CHANGED`, remove the old SSH host key entry before connecting:

```sh
./cctl -R 192.5.87.31 gpu
```

Use `-y` with `-R` or `--replace-host-key` only when you already expect the instance behind that IP to have changed.

## Password Options

Prompt interactively, the default:

```sh
./cctl 192.5.86.156 adduser --user llamacpp --pubkey ~/.ssh/id_ed25519.pub
```

Read from stdin:

```sh
printf '%s\n' 'temporary-password' | ./cctl 192.5.86.156 \
  adduser --user llamacpp --pubkey ~/.ssh/id_ed25519.pub --password-stdin
```

Skip password changes and only configure SSH access:

```sh
./cctl 192.5.86.156 adduser \
  --user llamacpp \
  --pubkey ~/.ssh/id_ed25519.pub \
  --no-password
```

Force the user to change the password at first password login:

```sh
./cctl 192.5.86.156 adduser \
  --user llamacpp \
  --pubkey ~/.ssh/id_ed25519.pub \
  --force-password-change
```

## Notes

- The remote login user defaults to your local username. Prefer `LOGIN_USER@HOST`; `-u USER` and `--user USER` are available as alternatives.
- Older command names are still accepted as compatibility aliases: `init`, `key`, `provision-user`, `add-key`, `sudo-test`, and `gpu-info`.
- The script uses `sudo` remotely, so `cc` must be able to run sudo.
- The remote sudo group is detected as `sudo` first and `wheel` second.
- Public keys may be passed as a path or as a literal OpenSSH public key string.
- `gpu` is read-only and does not require sudo. It prints a compact summary; some checks may report unavailable if tools such as `lspci`, `nvcc`, Docker, or Python packages are not installed.
