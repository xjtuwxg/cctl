# OpenStack and multi-site setup

`cctl` wraps the OpenStack CLI for common Chameleon Cloud operations. Instance and lease listing support multiple site profiles; commands that modify instances or leases use the OpenStack environment currently sourced in your shell.

## Requirements

Install the OpenStack client and the Blazar reservation plugin. Using `uv` keeps them isolated from the system Python:

```sh
uv tool install python-openstackclient --with python-blazarclient --force
```

For lease commands, `cctl` checks the `openstack` command on `PATH` and then
`~/.local/bin/openstack`. This handles the common case where a Homebrew client
without Blazar shadows the `uv` installation. Set `CCTL_OPENSTACK_BIN` to choose
another executable explicitly.

Download an RC file for each site from **Project → API Access** in the Chameleon portal. Application credentials are recommended when you sign in through a university or other federated identity provider.

## Configure TACC and UC profiles

Store the files under the standard user configuration directory:

```sh
mkdir -p ~/.config/cctl
cp ~/Downloads/CHI-TACC-openrc.sh ~/.config/cctl/tacc.sh
cp ~/Downloads/CHI-UC-openrc.sh ~/.config/cctl/uc.sh
chmod 600 ~/.config/cctl/tacc.sh ~/.config/cctl/uc.sh
```

For a named profile such as `tacc`, `cctl` looks for its RC file in this order:

1. `$CCTL_CONFIG_DIR/tacc.sh`, when `CCTL_CONFIG_DIR` is set;
2. `~/.config/cctl/tacc.sh`;
3. `tacc.sh` beside the `cctl` script.

RC files are shell scripts and contain credentials. Only use files you trust, keep their permissions restricted, and do not commit them.

## List instances

Query both configured sites:

```sh
./cctl instances
```

Each RC file is loaded in a separate subshell. This matters because sourcing both into one shell would make the second file overwrite the first file's `OS_*` variables.

Query a single profile:

```sh
./cctl instances --profile tacc
./cctl instances --profile uc
```

Use an RC environment that is already active:

```sh
source ~/.config/cctl/tacc.sh
./cctl instances --current
```

Additional arguments are passed to `openstack server list`:

```sh
./cctl instances --profile tacc --long
./cctl instances --profile uc --status ACTIVE
```

## Launch an instance

`launch` uses the currently sourced site. Load the intended profile first:

```sh
source ~/.config/cctl/tacc.sh

./cctl launch \
  --name my-instance \
  --flavor m1.medium \
  --image CC-Ubuntu24.04 \
  --key-name mykey
```

All options after `launch` are passed to `openstack server create`.

## List and manage leases

List leases from both configured sites without sourcing an RC file:

```sh
./cctl leases
```

Query one site, or use an environment that is already active:

```sh
./cctl leases --profile tacc
./cctl leases --profile uc

source ~/.config/cctl/uc.sh
./cctl leases --current
```

`renew` first searches both profiles by exact lease name or ID. When only one
profile matches, no source or profile option is needed:

```sh
./cctl renew my-lease --add-days 7
./cctl renew my-lease --end-date "2026-08-11 18:00"
```

The argument is the Blazar lease name reported by `cctl leases`, not the Nova
server name reported by `cctl instances`. OpenStack server metadata does not
provide a reliable mapping back to its Blazar lease, so `cctl` does not guess.

If TACC and UC contain leases with the same name, select the intended site:

```sh
./cctl renew my-lease --profile tacc --add-days 7
./cctl renew my-lease --profile uc --add-days 7
```

You can still explicitly use an environment that is already active:

```sh
source ~/.config/cctl/uc.sh
./cctl renew my-lease --current --add-days 7
```

Auto-detection is fail-safe: if either site cannot be searched, `cctl` stops
before changing a lease.

Chameleon limits when and how far leases can be extended. If a renewal is rejected, check the site's current lease policy and the reservation's end time.

## Troubleshooting

### `Missing value auth-url required for auth plugin password`

No valid OpenStack environment is active. For instance listing, configure `tacc.sh` and `uc.sh` and run:

```sh
./cctl instances
```

For `launch`, source the target site's RC file first. The `instances`, `leases`,
and unambiguous `renew` commands load configured profiles automatically.

### Profile not found

Check the expected location and filename:

```sh
ls -l ~/.config/cctl/tacc.sh ~/.config/cctl/uc.sh
```

If the files live elsewhere, point `cctl` at their directory:

```sh
CCTL_CONFIG_DIR=/path/to/rc-files ./cctl instances
```

### OpenStack CLI not found

Make sure the `uv` tool directory is on your `PATH`, then verify:

```sh
openstack --version
openstack reservation --help
```

If `openstack` exists but does not contain Blazar, verify the `uv` installation:

```sh
~/.local/bin/openstack reservation lease list --help
```

You can select it explicitly when necessary:

```sh
CCTL_OPENSTACK_BIN=~/.local/bin/openstack ./cctl leases
```
