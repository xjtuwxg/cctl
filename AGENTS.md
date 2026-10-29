# Driving cctl from an agent

`cctl` is designed to be called by an LLM agent. Rules of the road:

1. **Always pass `--json`.** Data goes to stdout as JSON; progress and warnings go to stderr.
   Errors are `{"error": "...", "hint": "..."}` on stderr with a non-zero exit code:
   `1` generic, `3` policy denied (credential problem), `4` not found, `5` ambiguous name.
2. **Destructive commands need `-y`** (`leases delete`, `servers delete`, `down`,
   `ips release`). Without a TTY and without `-y` they refuse.
3. **Use `--dry-run` first** on `leases create`, `servers create` and `up` to show the exact
   request, then run it for real.
4. **Name things.** Pass `-n NAME` to `up` so the lease and server share a name; `down NAME`
   then removes both.
5. **Pick a site explicitly** (`-s uc` / `-s tacc`) for anything that creates resources.
   Read commands default to all sites.

## Recipes

Find hardware that is free for 2 days, GPU only:

```sh
cctl nodes --gpu --free -d 2d --json
```

Per-node availability for one type (shows `busy_until` / `next_free`):

```sh
cctl nodes list -t gpu_a100_pcie -d 2d --json
```

Reserve + deploy + public IP, wait until SSH is reachable (10–20 min on bare metal):

```sh
cctl up -s uc -t gpu_rtx_6000 -d 2d -n exp1 -k mykey -i CC-Ubuntu24.04 --json
# result: {"lease": {...}, "server": {..., "floating_ip": "192.5.87.x", "ssh": "cc@192.5.87.x"}}
```

Reserve for later (start in 2 hours, 1 day), no server yet:

```sh
cctl leases create -s uc -t compute_skylake --start +2h -d 1d -n later --json
cctl servers create -s uc --lease later -n later --wait --json   # once the lease is ACTIVE
```

Keep leases alive (run from cron / a scheduled agent):

```sh
cctl leases renew --all --within 36h -d 7d --json
```

Status, and what expires soon:

```sh
cctl status --json
```

Post-boot setup and checks over SSH:

```sh
cctl ssh cc@IP adduser --user me --pubkey ~/.ssh/id_ed25519.pub --no-password -y
cctl ssh me@IP gpu
cctl ssh me@IP run -- nvidia-smi
```

Tear down:

```sh
cctl down exp1 -y --json
```

## Things to know

- Lease dates in JSON are ISO-8601 UTC; `remaining` is a human string.
- `nodes` merges Blazar's host list with Chameleon's public hardware catalogue
  (cached 24 h in `~/.cache/cctl/`); GPU model, RAM and CPU counts come from the catalogue.
- If `cctl config check` shows `BLAZAR LEASES: DENIED`, the application credential lacks
  the `reader` role or `unrestricted`. Stop and tell the user to recreate it; only
  read-only commands work meanwhile.
- `up` and `servers create` without `--no-ip` reuse a free floating IP in the project
  before allocating a new one.
