# OpenStack and multi-site setup

`cctl` wraps the OpenStack CLI for common Chameleon Cloud operations. Instance listing supports multiple site profiles; commands that modify instances or leases use the OpenStack environment currently sourced in your shell.

## Requirements

Install the OpenStack client and the Blazar reservation plugin. Using `uv` keeps them isolated from the system Python:

```sh
uv tool install python-openstackclient --with python-blazarclient --force
```

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

## Manage leases

Lease commands also use the currently sourced site:

```sh
source ~/.config/cctl/uc.sh

./cctl leases
./cctl renew my-lease --add-days 7
./cctl renew my-lease --end-date "2026-08-11 18:00"
```

Chameleon limits when and how far leases can be extended. If a renewal is rejected, check the site's current lease policy and the reservation's end time.

## Troubleshooting

### `Missing value auth-url required for auth plugin password`

No valid OpenStack environment is active. For instance listing, configure `tacc.sh` and `uc.sh` and run:

```sh
./cctl instances
```

For `launch`, `leases`, or `renew`, source the target site's RC file first.

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
