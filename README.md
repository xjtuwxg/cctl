# Chameleon Control

`cctl` is a local helper for managing a Chameleon Cloud instance over SSH.

The initial workflow is:

1. SSH to the instance as the existing `cc` user.
2. Create or update a Linux user.
3. Add the user to the remote sudo group.
4. Install the user's public SSH key into `~/.ssh/authorized_keys`.
5. Optionally set the user's password.

## Installation

To use `cctl` globally from any directory, make it executable and copy it to your local bin directory:

```sh
chmod +x ./cctl
sudo cp ./cctl /usr/local/bin/
```
Now you can simply type `cctl` instead of `./cctl` everywhere!

## Quick Start

Make the script executable:

```sh
chmod +x ./cctl
```

Provision a user:

```sh
./cctl cc@192.5.86.156 adduser \
  -u llamacpp \
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

Allow the user to run sudo without entering a password:

```sh
./cctl llamacpp@192.5.86.156 nopasswd
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
  adduser -u llamacpp --pubkey ~/.ssh/id_ed25519.pub
```

The `-u` option is still available if you prefer to keep the login user separate:

```sh
./cctl -u cc 192.5.86.156 \
  --identity-file ~/.ssh/chameleon_key \
  adduser -u llamacpp --pubkey ~/.ssh/id_ed25519.pub
```

If a reused instance IP reports `REMOTE HOST IDENTIFICATION HAS CHANGED`, remove the old SSH host key entry before connecting:

```sh
./cctl -R 192.5.87.31 gpu
```

Use `-y` with `-R` or `--replace-host-key` only when you already expect the instance behind that IP to have changed.

Commands that change the remote machine, such as `adduser`, `addkey`, `fish`, and `nopasswd`, prompt once for the SSH login user's sudo password unless passwordless sudo is already configured.

## OpenStack Commands

`cctl` also provides wrappers around the OpenStack API to manage Chameleon Cloud instances and leases directly from your local machine, without needing to specify a remote host.

### Prerequisites

These commands require the `openstack` CLI with the Blazar plugin. The recommended way to install this globally without breaking your system Python is using `uv`:

```sh
# 1. Install uv if you haven't already: https://docs.astral.sh/uv/
# 2. Install openstack client with the blazar plugin:
uv tool install python-openstackclient --with python-blazarclient --force
```

Download an OpenStack RC file for each Chameleon site from the Chameleon Cloud portal (Project -> API Access). For multi-site listing, save them as `tacc.sh` and `uc.sh` either beside `cctl` or in `~/.config/cctl/`:

```sh
mkdir -p ~/.config/cctl
cp ~/Downloads/CHI-TACC-openrc.sh ~/.config/cctl/tacc.sh
cp ~/Downloads/CHI-UC-openrc.sh ~/.config/cctl/uc.sh
chmod 600 ~/.config/cctl/tacc.sh ~/.config/cctl/uc.sh
```
*(Tip: If you use federated login like a university email, generate an **Application Credential** from the Identity tab and download its openrc file instead).*

The RC files contain credentials and are ignored by this repository. You can set
`CCTL_CONFIG_DIR` if you prefer another directory.

### Usage

List instances from both TACC and UC. `cctl` loads each RC file in a separate
subshell, because sourcing both files into one shell would make the second file
overwrite the first file's `OS_*` variables:

```sh
./cctl instances
```

Query only one site, or use the environment you already sourced:

```sh
./cctl instances --profile tacc
./cctl instances --profile uc
source ~/Downloads/CHI-TACC-openrc.sh
./cctl instances --current
```

Options not consumed by `cctl` are passed to `openstack server list`:

```sh
./cctl instances --profile tacc --long
```

List all reservations/leases (colorized by expiration status):

```sh
./cctl leases
```

Renew a lease by adding days to its current end date:

```sh
./cctl renew my-lease --add-days 7
```
*(Chameleon Cloud restricts renewals to within 4 days of the lease end time).*

Launch a new instance:

```sh
./cctl launch --name my-instance --flavor m1.medium --image CC-Ubuntu20.04 --key-name mykey
```

## Password Options

Prompt interactively, the default:

```sh
./cctl 192.5.86.156 adduser -u llamacpp --pubkey ~/.ssh/id_ed25519.pub
```

Read from stdin:

```sh
printf '%s\n' 'temporary-password' | ./cctl 192.5.86.156 \
  adduser -u llamacpp --pubkey ~/.ssh/id_ed25519.pub --password-stdin
```

Skip password changes and only configure SSH access:

```sh
./cctl 192.5.86.156 adduser \
  -u llamacpp \
  --pubkey ~/.ssh/id_ed25519.pub \
  --no-password
```

Force the user to change the password at first password login:

```sh
./cctl 192.5.86.156 adduser \
  -u llamacpp \
  --pubkey ~/.ssh/id_ed25519.pub \
  --force-password-change
```

## Notes

- The remote login user defaults to your local username. Prefer `LOGIN_USER@HOST`; `-u USER` and `--user USER` are available as alternatives.
- Before the host, `-u USER` means SSH login user. After a command such as `adduser`, `addkey`, `sudo`, or `fish`, `-u USER` means that command's target user.
- Older command names are still accepted as compatibility aliases: `init`, `key`, `provision-user`, `add-key`, `sudo-test`, and `gpu-info`.
- The script uses `sudo` remotely, so `cc` must be able to run sudo.
- The remote sudo group is detected as `sudo` first and `wheel` second.
- `nopasswd` writes `/etc/sudoers.d/USER-nopasswd` and validates it with `visudo`.
- Public keys may be passed as a path or as a literal OpenSSH public key string.
- `gpu` is read-only and does not require sudo. It prints a compact summary; some checks may report unavailable if tools such as `lspci`, `nvcc`, Docker, or Python packages are not installed.
