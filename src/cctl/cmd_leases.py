"""cctl leases: list / show / create / renew / delete Blazar leases."""

from __future__ import annotations

from datetime import timedelta

from .errors import CctlError
from .leases import (LEASE_COLUMNS, all_leases, build_reservations, create_lease, delete_lease, get_lease,
                     lease_row, resolve_lease, set_end, wait_lease)
from .output import fmt_dt
from .timeutil import from_blazar, now_utc, parse_duration, parse_when


def register(sub, parents):
    p = sub.add_parser("leases", parents=parents, help="list, create, renew and delete leases (reservations)")
    s = p.add_subparsers(dest="subcommand", metavar="<action>")

    a = s.add_parser("list", parents=parents, help="list leases on every site (default)")
    a.add_argument("--all", action="store_true", help="include terminated/expired leases")
    a.set_defaults(func=cmd_list)
    p.add_argument("--all", action="store_true", help=argparse_suppress())
    p.set_defaults(func=cmd_list)

    a = s.add_parser("show", parents=parents, help="full details of one lease")
    a.add_argument("lease", help="lease name or id")
    a.set_defaults(func=cmd_show)

    a = s.add_parser("create", parents=parents, help="reserve nodes and/or floating IPs")
    add_create_args(a)
    a.set_defaults(func=cmd_create)

    a = s.add_parser("renew", parents=parents, help="extend a lease (or every lease expiring soon)")
    a.add_argument("lease", nargs="?", help="lease name or id (omit with --all)")
    a.add_argument("--all", action="store_true", help="renew every active lease expiring within --within")
    a.add_argument("--within", default="2d", help="with --all: only leases ending within this (default 2d)")
    g = a.add_mutually_exclusive_group()
    g.add_argument("-d", "--extend", default=None, help="extend from the current end: 3d, 12h (default: 7d from now)")
    g.add_argument("--until", default=None, help="absolute new end: 'YYYY-MM-DD HH:MM' (local)")
    a.set_defaults(func=cmd_renew)

    a = s.add_parser("delete", parents=parents, help="delete a lease (and release its nodes)")
    a.add_argument("lease", help="lease name or id")
    a.set_defaults(func=cmd_delete)


def argparse_suppress():
    import argparse
    return argparse.SUPPRESS


def add_create_args(a, require_type: bool = False):
    a.add_argument("-n", "--name", help="lease name (default: cctl-<type>-<timestamp>)")
    a.add_argument("-t", "--type", dest="node_type", help="node_type to reserve, e.g. gpu_rtx_6000 (see: cctl nodes)")
    a.add_argument("--node", help="reserve one specific node by name instead of a type")
    a.add_argument("--properties", help="raw Blazar resource_properties JSON, e.g. '[\"=\", \"$gpu.gpu_model\", \"RTX 6000\"]'")
    a.add_argument("-c", "--count", type=int, default=1, help="number of nodes (default 1)")
    a.add_argument("--fip", type=int, default=0, help="also reserve N floating IPs")
    a.add_argument("--start", default="now", help="now (default), +2h, or 'YYYY-MM-DD HH:MM' local")
    a.add_argument("-d", "--duration", default=None, help="length: 6h, 3d, 1w (default: defaults.lease_days days)")
    a.add_argument("--end", default=None, help="absolute end instead of --duration")
    a.add_argument("--before-end", default=None, help="Blazar before_end action: email, or '' to disable")
    a.add_argument("--wait", action="store_true", help="wait until the lease is ACTIVE")
    a.add_argument("--timeout", type=int, default=900, help="seconds to wait with --wait (default 900)")
    a.add_argument("--dry-run", action="store_true", help="print the lease request, create nothing")


def cmd_list(ctx, args):
    rows = [lease_row(s, l) for s, l in all_leases(ctx.sites(), ctx.out.warn)]
    if not args.all:
        rows = [r for r in rows if r["status"] not in ("TERMINATED", "ERROR") and r["remaining"] != "expired"]
    rows.sort(key=lambda r: (r["site"], r["end"] or now_utc()))
    if any(r["source"] == "allocations" for r in rows) and not ctx.out.as_json:
        ctx.out.warn("lease API denied by policy; showing leases reconstructed from host allocations "
                     "(names unavailable). Run 'cctl config check'.")
    ctx.out.rows(rows, LEASE_COLUMNS)


def cmd_show(ctx, args):
    site, lease = resolve_lease(ctx.sites(), args.lease, ctx.out.warn)
    if lease.get("_source") != "allocations":
        lease = get_lease(site, lease["id"])
    if ctx.out.as_json:
        lease = dict(lease, site=site.name)
        ctx.out.obj(lease)
        return
    row = lease_row(site, lease)
    ctx.out.obj({k: row[k] for k in ("site", "name", "id", "status", "degraded", "start", "end", "remaining")})
    print()
    rows = [{"id": r.get("id"), "type": r.get("resource_type"), "status": r.get("status"),
             "what": __import__("cctl.leases", fromlist=["describe_reservation"]).describe_reservation(r),
             "missing": r.get("missing_resources"), "changed": r.get("resources_changed")}
            for r in lease.get("reservations", [])]
    ctx.out.rows(rows, [("id", "RESERVATION ID"), ("type", "TYPE"), ("status", "STATUS"), ("what", "WHAT"),
                        ("missing", "MISSING"), ("changed", "CHANGED")], title="reservations")
    if lease.get("_nodes"):
        print("nodes:", ", ".join(map(str, lease["_nodes"])))


def plan_window(ctx, args):
    start = parse_when(args.start)
    if args.end:
        end = parse_when(args.end)
    else:
        dur = parse_duration(args.duration) if args.duration else timedelta(days=ctx.defaults.lease_days)
        end = start + dur
    return start, end


def cmd_create(ctx, args):
    site = ctx.one_site()
    start, end = plan_window(ctx, args)
    reservations = build_reservations(site, node_type=args.node_type, count=args.count, node_name=args.node,
                                      properties=args.properties, fips=args.fip)
    label = args.node or args.node_type or ("fip" if args.fip else "custom")
    name = args.name or f"cctl-{label}-{now_utc().astimezone().strftime('%m%d-%H%M')}"
    if args.dry_run:
        ctx.out.obj({"site": site.name, "name": name, "start": fmt_dt(start), "end": fmt_dt(end), "reservations": reservations})
        return 0
    ctx.out.info(f"{site.name}: creating lease '{name}' {fmt_dt(start)} -> {fmt_dt(end)} ({len(reservations)} reservation(s))")
    lease = create_lease(site, name, start, end, reservations, before_end=args.before_end)
    if args.wait:
        lease = wait_lease(site, lease["id"], timeout=args.timeout, log=ctx.out.info)
    if ctx.out.as_json:
        ctx.out.obj(dict(lease, site=site.name))
    else:
        ctx.out.rows([lease_row(site, lease)], LEASE_COLUMNS)
    return 0


def _new_end(args, lease):
    cur_end = from_blazar(lease.get("end_date"))
    if args.until:
        return parse_when(args.until)
    if args.extend:
        return (cur_end or now_utc()) + parse_duration(args.extend)
    return now_utc() + timedelta(days=7)


def cmd_renew(ctx, args):
    sites = ctx.sites()
    targets = []
    if args.all:
        limit = now_utc() + parse_duration(args.within)
        for site, lease in all_leases(sites, ctx.out.warn):
            end = from_blazar(lease.get("end_date"))
            if lease.get("status") in ("ACTIVE", "PENDING", None) and end and now_utc() < end <= limit:
                targets.append((site, lease))
        if not targets:
            ctx.out.info(f"no lease expires within {args.within}")
            return 0
    else:
        if not args.lease:
            raise CctlError("give a lease name/id or --all")
        targets.append(resolve_lease(sites, args.lease, ctx.out.warn))

    rows = []
    for site, lease in targets:
        new_end = _new_end(args, lease)
        ctx.out.info(f"{site.name}: {lease.get('name') or lease['id']}: {fmt_dt(from_blazar(lease.get('end_date')))} -> {fmt_dt(new_end)}")
        try:
            updated = set_end(site, lease, new_end)
        except CctlError as exc:
            if "7" in exc.message or "max" in exc.message.lower() or "limit" in exc.message.lower():
                exc.hint = ("Chameleon caps bare-metal leases at 7 days total and only allows extending within "
                            "the last 48 hours of a lease; try a shorter --extend.")
            raise
        rows.append(lease_row(site, updated))
    ctx.out.rows(rows, LEASE_COLUMNS)


def cmd_delete(ctx, args):
    site, lease = resolve_lease(ctx.sites(), args.lease, ctx.out.warn)
    if not ctx.confirm(f"Delete lease '{lease.get('name') or lease['id']}' on {site.name}? Nodes will be released."):
        return 1
    delete_lease(site, lease["id"])
    ctx.out.info(f"deleted lease {lease.get('name') or lease['id']} on {site.name}")
    if ctx.out.as_json:
        ctx.out.obj({"deleted": lease["id"], "site": site.name})
