# Chameleon Cloud Control

`ccctl` is a local helper for managing a Chameleon Cloud instance over SSH.

The initial workflow is:

1. SSH to the instance as the existing `cc` user.
2. Create or update a Linux user.
3. Add the user to the remote sudo group.
4. Install the user's public SSH key into `~/.ssh/authorized_keys`.
5. Optionally set the user's password.

## Quick Start

Make the script executable:

```sh
chmod +x ./ccctl
```

Provision a user:

```sh
./ccctl --host 192.5.86.156 provision-user \
  --user llamacpp \
  --pubkey ~/.ssh/id_ed25519.pub
```

The script prompts for the new password by default. After it finishes, test login:

```sh
ssh llamacpp@192.5.86.156
```

Verify sudo access:

```sh
./ccctl --host 192.5.86.156 sudo-test --user llamacpp
```

## Common Commands

Show basic instance status:

```sh
./ccctl --host 192.5.86.156 status
```

Add another public key to an existing user:

```sh
./ccctl --host 192.5.86.156 add-key \
  --user llamacpp \
  --pubkey ~/.ssh/work_key.pub
```

Run a remote command as the login user:

```sh
./ccctl --host 192.5.86.156 run -- whoami
```

Use a private key for the original `cc` login:

```sh
./ccctl --host 192.5.86.156 \
  --identity-file ~/.ssh/chameleon_key \
  provision-user --user llamacpp --pubkey ~/.ssh/id_ed25519.pub
```

## Password Options

Prompt interactively, the default:

```sh
./ccctl --host 192.5.86.156 provision-user --user llamacpp --pubkey ~/.ssh/id_ed25519.pub
```

Read from stdin:

```sh
printf '%s\n' 'temporary-password' | ./ccctl --host 192.5.86.156 \
  provision-user --user llamacpp --pubkey ~/.ssh/id_ed25519.pub --password-stdin
```

Skip password changes and only configure SSH access:

```sh
./ccctl --host 192.5.86.156 provision-user \
  --user llamacpp \
  --pubkey ~/.ssh/id_ed25519.pub \
  --no-password
```

Force the user to change the password at first password login:

```sh
./ccctl --host 192.5.86.156 provision-user \
  --user llamacpp \
  --pubkey ~/.ssh/id_ed25519.pub \
  --force-password-change
```

## Notes

- The remote login user defaults to `cc`.
- The script uses `sudo` remotely, so `cc` must be able to run sudo.
- The remote sudo group is detected as `sudo` first and `wheel` second.
- Public keys may be passed as a path or as a literal OpenSSH public key string.
