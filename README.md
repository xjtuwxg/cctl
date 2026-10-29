# cctl — Chameleon Cloud from the command line

`cctl` manages [Chameleon Cloud](https://www.chameleoncloud.org/) from any directory:
find free hardware, reserve it, launch bare-metal servers, attach public IPs, keep leases
alive, tear everything down, and run the usual post-boot SSH chores.

It talks to the OpenStack and Blazar APIs directly through `openstacksdk` and
`python-blazarclient`. There is no `source openrc.sh` step and no dependency on the
`openstack` CLI: each command runs against every configured site in parallel, or the one
you pick with `-s`. Every command accepts `--json`, so an LLM agent can drive it
(see [AGENTS.md](AGENTS.md)).

```sh
cctl status                                  # leases + servers on every site
cctl nodes --gpu -d 3d                       # GPU node types with free nodes for 3 days
cctl nodes list -t gpu_a100_pcie             # per node: busy until / next free
cctl up -s uc -t gpu_rtx_6000 -d 2d -n exp1  # lease + server + public IP, prints ssh cc@IP
cctl leases renew exp1 -d 7d                 # extend a lease
cctl down exp1                               # delete server(s), release IP, delete lease
cctl ssh cc@IP adduser --user me --pubkey ~/.ssh/id_ed25519.pub --no-password
cctl ssh me@IP tunnel -L 8888 -L 6006
```

## Install

Requires Python 3.11+. The recommended way is [uv](https://docs.astral.sh/uv/), which
installs `cctl` into its own isolated environment and links it into `~/.local/bin`:

```sh
git clone https://github.com/xjtuwxg/cctl.git
uv tool install --editable ./cctl
```

`--editable` makes edits under `src/cctl/` take effect immediately; use
`uv tool install --force` to reinstall. Any of these also work:

```sh
uv tool install git+https://github.com/xjtuwxg/cctl.git   # no checkout
pipx install git+https://github.com/xjtuwxg/cctl.git
pip install .                                             # inside a venv
```

## Credentials

`cctl` reads one OpenStack RC file per site from `~/.config/cctl/`
(override with `--config-dir` or `$CCTL_CONFIG_DIR`):

```
~/.config/cctl/uc.sh          # CHI@UC
~/.config/cctl/tacc.sh        # CHI@TACC
~/.config/cctl/kvm.sh         # KVM@TACC (optional)
~/.config/cctl/config.toml    # optional defaults, see config.toml.example
```

The file name without `.sh` is the site name used by `-s`. Files are parsed, never
sourced or executed, so they can be plain `export OS_...=...` lines as downloaded.

Get an RC file from the site's portal under **Identity → Application Credentials →
Create**, download it, save it as `~/.config/cctl/<site>.sh` and `chmod 600` it.
Chameleon's Blazar policy requires the credential to have:

- roles **member** and **reader**: without `reader` every lease call fails with
  `Policy doesn't allow blazar:leases:get`;
- **Unrestricted** ticked, which is needed to create and extend leases from the CLI.

Then verify:

```sh
cctl config check
```

This authenticates against every site and reports the token roles plus Nova and Blazar
access. With a credential that lacks `reader`, `cctl leases` falls back to reconstructing
your leases from the host allocation table (IDs and end dates, but no names) and
`leases create / renew / delete` are refused by the API.

## Commands

| Command | What it does |
| --- | --- |
| `status` | Leases and servers on every site; warns about leases ending within 48 h |
| `nodes [types]` | Per `node_type`: total, reservable, free now, free for a window (`--start`, `-d`), GPU model, CPUs, RAM |
| `nodes list` | Individual nodes with `busy until` / `next free`; filter with `-t TYPE`, `-g a100`, `--gpu`, `--free` |
| `nodes show NODE` | Every Blazar property of one node and its reservations |
| `leases [list]` | Active leases; `--all` includes terminated ones |
| `leases show NAME` | Lease details and reservation IDs |
| `leases create` | `-t node_type` / `--node NAME` / `--properties JSON`, `-c N`, `--fip N`, `--start`, `-d 3d`, `--wait`, `--dry-run` |
| `leases renew NAME` | `-d 3d` from the current end, `--until 'YYYY-MM-DD HH:MM'`, or `--all --within 2d` |
| `leases delete NAME` | Delete a lease (asks first; `-y` skips) |
| `servers [list]` | Servers with public and fixed IP, image, flavor, key |
| `servers create` | `--lease NAME` uses its host reservation; `-i IMAGE`, `-k KEY`, `--wait`, `--no-ip`, `--dry-run` |
| `servers delete NAME` | `--release-ip` frees its floating IP too |
| `servers wait NAME` | Wait for ACTIVE; `--ssh` also waits for port 22 |
| `servers reboot NAME` | Soft or `--hard` reboot |
| `up` | `leases create --wait` + `servers create --wait` + floating IP in one call |
| `down NAME` | Delete the lease's servers (same name or `NAME-*`), release IPs, delete the lease |
| `ips` | Floating IPs: `list`, `create`, `attach SERVER [IP]`, `detach IP`, `release IP` |
| `images` | Images, filtered by `-f` (default `CC-`) |
| `keys` | SSH keypairs |
| `config` | `list`, `path`, `check` |
| `ssh [USER@]HOST …` | `adduser`, `addkey`, `fish`, `nopasswd`, `sudo`, `status`, `gpu`, `tunnel`, `run -- CMD` |

Flags accepted by every command, before or after it: `-s SITE` (one site, a comma list, or
`all`), `--json`, `-y`, `-q`. Before the command only: `--config-dir DIR`, `--debug`.

Times you type are local time. `--start` accepts `now`, `+2h`, or `YYYY-MM-DD HH:MM`.
Durations are `90m`, `6h`, `3d`, `1w`. Lease dates in `--json` output are ISO-8601 UTC.

Leases and servers are addressed by exact name or ID. When the same name exists on several
sites the command stops with an "ambiguous" error instead of guessing; add `-s SITE`.

## Typical session

```sh
cctl nodes --gpu -d 2d --free                 # what can I get for two days?
cctl up -s uc -t gpu_rtx_6000 -d 2d -n exp1   # 10–20 min: lease ACTIVE, server deployed, IP attached
cctl ssh cc@IP adduser --user me --pubkey ~/.ssh/id_ed25519.pub --no-password
cctl ssh me@IP gpu
cctl leases renew exp1 -d 3d                  # later, inside the renewal window
cctl down exp1 -y
```

Chameleon caps bare-metal leases at 7 days and only allows extending them near the end;
Blazar reports the exact rule when a renewal is rejected.

## Hardware search

`cctl nodes` merges Blazar's host list with Chameleon's public hardware catalogue
(`api.chameleoncloud.org`, cached for 24 h in `~/.cache/cctl/`). Newer nodes such as
H100, A100 and MI100 have no GPU fields in Blazar; the catalogue supplies their GPU model,
GPU count, RAM and CPU model, so `--gpu` and `-g h100` work for all of them. Availability
is computed from Blazar allocations, so "free for 3 days" means no reservation overlaps
that window.

## Using cctl from an agent

[AGENTS.md](AGENTS.md) is written for LLM agents: always `--json`, `-y` for destructive
commands, `--dry-run` before creating anything, name resources with `-n`, pick a site with
`-s`. JSON data goes to stdout, progress to stderr, and errors are
`{"error": ..., "hint": ...}` on stderr with exit code 1 (generic), 3 (policy denied:
credential problem), 4 (not found) or 5 (ambiguous name).

## Configuration defaults

Copy [config.toml.example](config.toml.example) to `~/.config/cctl/config.toml` to set a
default site, image, keypair, network, flavor, SSH user and lease length. Command-line
flags always win.

## SSH helpers

`cctl ssh [USER@]HOST ACTION` wraps the post-boot chores: create a user with your public
key and passwordless sudo, add keys, install fish, check sudo, show status and GPU/CUDA
details, open port-forward tunnels, or run an arbitrary command. Details in
[docs/ssh.md](docs/ssh.md) and [docs/tunnels.md](docs/tunnels.md). Sudo passwords are
read from the terminal and sent over stdin to `sudo -S`, never placed on a command line.

## Layout

```
src/cctl/
  cli.py          argument parsing, site selection, error handling
  config.py       RC-file profiles and config.toml defaults
  cloud.py        Site: openstacksdk connection + Blazar client, policy-error translation
  hardware.py     node inventory, public catalogue merge, availability windows
  leases.py       lease helpers (+ allocation fallback when the lease API is denied)
  servers.py      servers and floating IPs
  timeutil.py     durations, local/UTC conversion, Blazar date format
  output.py       tables and JSON
  cmd_*.py        one module per command group
  cmd_ssh.py      the SSH helpers ported from the original Bash script
legacy/cctl.sh    the previous Bash implementation, kept for reference
docs/             ssh.md, tunnels.md
```

## Status

Read-only commands (`nodes`, `leases`, `servers`, `images`, `keys`, `status`,
`config check`) are exercised against both CHI@UC and CHI@TACC. The SSH helpers are a
direct port of the original Bash script's remote scripts. Lease creation and renewal,
server creation, floating IP attachment, `up` and `down` were developed against the API
documentation and `--dry-run`, but have not yet been run end to end with a credential that
has the required Blazar roles. Please open an issue if one of them misbehaves.
