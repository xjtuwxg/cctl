# Chameleon Control

`cctl` is a small Bash CLI for managing [Chameleon Cloud](https://www.chameleoncloud.org/) instances. It can:

- list instances across CHI@TACC and CHI@UC;
- create users and install SSH keys;
- inspect instance, GPU, CUDA, and container-runtime status;
- manage OpenStack instances and leases.

## Install

Run it directly from this repository:

```sh
chmod +x ./cctl
./cctl --help
```

Optional: install it on your `PATH`:

```sh
mkdir -p ~/.local/bin
install -m 755 ./cctl ~/.local/bin/cctl
```

The examples below use `./cctl`. Use `cctl` instead after installing it.

## Quick start

### List instances at TACC and UC

Install the OpenStack client and save each site's [application-credential RC file](https://chameleoncloud.readthedocs.io/en/latest/technical/cli/rc_script.html):

```sh
uv tool install python-openstackclient --with python-blazarclient --force

mkdir -p ~/.config/cctl
cp ~/Downloads/CHI-TACC-openrc.sh ~/.config/cctl/tacc.sh
cp ~/Downloads/CHI-UC-openrc.sh ~/.config/cctl/uc.sh
chmod 600 ~/.config/cctl/*.sh

./cctl instances
```

Query one site with `./cctl instances --profile tacc` or `--profile uc`.

### Configure a new instance

Create a user through Chameleon's default `cc` account and install your public key:

```sh
./cctl cc@PUBLIC_IP adduser \
  --user YOUR_USER \
  --pubkey ~/.ssh/id_ed25519.pub \
  --no-password

ssh YOUR_USER@PUBLIC_IP
```

Commands that modify the instance ask for confirmation and may prompt for the `cc` user's sudo password.

### Forward local ports

Forward one or several remote ports through a single SSH connection:

```sh
./cctl YOUR_USER@PUBLIC_IP tunnel -L 8888 -L 6006
```

Each number maps the same remote port to the same local port. The tunnel stays in the foreground; press `Ctrl-C` to close it.

## Commands

| Area | Command | Purpose |
| --- | --- | --- |
| Cloud | `instances` | List instances across TACC and UC |
| Cloud | `launch` | Launch an OpenStack instance |
| Cloud | `leases` | List Blazar leases across TACC and UC |
| Cloud | `renew` | Extend or update a lease |
| SSH | `adduser` | Create/configure a user and SSH key |
| SSH | `addkey` | Add a public key to an existing user |
| SSH | `status` | Show host, uptime, disk, and memory status |
| SSH | `gpu` | Inspect GPU, driver, CUDA, and runtimes |
| SSH | `tunnel` | Forward one or more remote ports locally |
| SSH | `sudo` / `nopasswd` | Check or configure sudo access |
| SSH | `fish` | Install fish and set the login shell |
| SSH | `run` | Run a remote command |

Use `./cctl COMMAND --help` for command-specific options.

## Documentation

- [OpenStack and multi-site setup](docs/openstack.md)
- [SSH instance management](docs/ssh.md)
- [SSH tunnels and port forwarding](docs/tunnels.md)

RC files contain credentials. Keep them private and never commit them; the common `tacc.sh`, `uc.sh`, and `*openrc*.sh` names are already ignored by this repository.
