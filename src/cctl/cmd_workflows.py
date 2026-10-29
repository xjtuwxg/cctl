"""High-level commands: status, up (lease + server + ip), down (server + lease)."""

from __future__ import annotations

from .cmd_leases import add_create_args as add_lease_args, plan_window
from .cmd_servers import add_create_args as add_server_args, launch
from .errors import CctlError
from .leases import (LEASE_COLUMNS, all_leases, build_reservations, create_lease, delete_lease, host_reservation,
                     lease_row, resolve_lease, wait_lease)
from .output import fmt_dt
from .servers import SERVER_COLUMNS, all_servers, delete_server, find_server
from .timeutil import now_utc


def register(sub, parents):
    p = sub.add_parser("status", parents=parents, help="overview: leases and servers on every site")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("up", parents=parents, help="one shot: reserve a node, launch a server, attach a public IP")
    add_lease_args(p)
    add_server_args(p, shared=False)
    p.set_defaults(func=cmd_up)

    p = sub.add_parser("down", parents=parents, help="tear down: delete the lease's servers, release IPs, delete the lease")
    p.add_argument("lease", help="lease name or id")
    p.add_argument("--keep-ip", action="store_true", help="keep floating IPs allocated")
    p.set_defaults(func=cmd_down)


def cmd_status(ctx, args):
    sites = ctx.sites()
    leases = [lease_row(s, l) for s, l in all_leases(sites, ctx.out.warn)]
    leases = [r for r in leases if r["status"] not in ("TERMINATED", "ERROR") and r["remaining"] != "expired"]
    leases.sort(key=lambda r: (r["site"], r["end"] or now_utc()))
    servers = all_servers(sites, ctx.out.warn)
    servers.sort(key=lambda r: (r["site"], r["name"] or ""))
    if ctx.out.as_json:
        ctx.out.obj({"leases": leases, "servers": servers})
        return
    ctx.out.rows(leases, LEASE_COLUMNS, title="leases")
    print()
    ctx.out.rows(servers, SERVER_COLUMNS, title="servers")
    soon = [r for r in leases if r["end"] and (r["end"] - now_utc()).total_seconds() < 48 * 3600]
    if soon:
        print()
        ctx.out.warn("expiring within 48h: " + ", ".join(f"{r['name'] or r['id']} ({r['remaining']})" for r in soon)
                     + "  -> cctl leases renew NAME -d 7d")


def cmd_up(ctx, args):
    site = ctx.one_site()
    if not (args.node_type or args.node or args.properties):
        raise CctlError("say what to reserve: --type NODE_TYPE (see 'cctl nodes') or --node NAME")
    start, end = plan_window(ctx, args)
    reservations = build_reservations(site, node_type=args.node_type, count=args.count, node_name=args.node,
                                      properties=args.properties, fips=args.fip)
    label = args.node or args.node_type or "custom"
    name = args.name or f"cctl-{label}-{now_utc().astimezone().strftime('%m%d-%H%M')}"
    if args.dry_run:
        ctx.out.obj({"site": site.name, "lease": name, "start": fmt_dt(start), "end": fmt_dt(end),
                     "reservations": reservations, "server": name, "image": args.image or ctx.defaults.image,
                     "key": args.key or ctx.defaults.key_name, "floating_ip": args.attach_ip, "reserved_fips": args.fip})
        return 0
    ctx.out.info(f"{site.name}: lease '{name}' {fmt_dt(start)} -> {fmt_dt(end)}")
    lease = create_lease(site, name, start, end, reservations, before_end=args.before_end)
    lease = wait_lease(site, lease["id"], timeout=args.timeout, log=ctx.out.info)
    r = host_reservation(lease)
    if not r:
        raise CctlError("lease has no host reservation; nothing to launch")
    args.wait = True
    args.timeout = max(args.timeout, 1800)  # bare-metal deploys take 10-20 min
    row = launch(ctx, site, args, r["id"], name)
    result = {"lease": lease_row(site, lease), "server": row}
    if ctx.out.as_json:
        ctx.out.obj(result)
    else:
        ctx.out.rows([result["lease"]], LEASE_COLUMNS, title="lease")
        print()
        ctx.out.rows([row], SERVER_COLUMNS, title="server")
        if row.get("ssh"):
            print(f"\nssh {row['ssh']}")


def cmd_down(ctx, args):
    sites = ctx.sites()
    site, lease = resolve_lease(sites, args.lease, ctx.out.warn)
    name = lease.get("name") or lease["id"]
    # Servers that belong to this lease: same name, or name prefix, on that site.
    victims = [r for r in all_servers([site], ctx.out.warn)
               if r["name"] == name or (r["name"] or "").startswith(name + "-")]
    what = ", ".join(v["name"] for v in victims) or "(no servers)"
    if not ctx.confirm(f"Delete servers [{what}] and lease '{name}' on {site.name}?"):
        return 1
    released = []
    for v in victims:
        _, server = find_server([site], v["id"])
        released += delete_server(site, server, release_ips=not args.keep_ip)
        ctx.out.info(f"deleted server {v['name']}")
    delete_lease(site, lease["id"])
    ctx.out.info(f"deleted lease {name}")
    if ctx.out.as_json:
        ctx.out.obj({"site": site.name, "lease": lease["id"], "servers": [v["name"] for v in victims], "released_ips": released})
