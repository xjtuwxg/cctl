"""cctl nodes: what hardware exists, what is free, when."""

from __future__ import annotations

from datetime import datetime, timedelta

from .cloud import run_parallel
from .hardware import fetch_hosts, host_matches, host_rows, summarize_types
from .output import fmt_dt
from .timeutil import now_utc, parse_duration, parse_when

TYPE_COLUMNS = [("site", "SITE"), ("node_type", "NODE_TYPE"), ("total", "TOTAL"), ("reservable", "RESERVABLE"),
                ("free_now", "FREE NOW"), ("free_in_window", "FREE IN WINDOW"), ("gpu", "GPU"), ("gpu_count", "#GPU"),
                ("cpus", "CPUS"), ("ram", "RAM")]
HOST_COLUMNS = [("site", "SITE"), ("name", "NODE"), ("node_type", "NODE_TYPE"), ("gpu", "GPU"),
                ("reservable", "RESERVABLE"), ("free_in_window", "FREE"), ("busy_until", "BUSY UNTIL"),
                ("next_free", "NEXT FREE"), ("uid", "UID")]


def _window_args(p):
    p.add_argument("-t", "--type", dest="node_type", help="exact node_type, e.g. gpu_rtx_6000, compute_skylake")
    p.add_argument("-g", "--grep", dest="contains", help="substring match on node_type or GPU model, e.g. a100")
    p.add_argument("--gpu", action="store_true", help="GPU nodes only")
    p.add_argument("--start", default="now", help="window start: now, +2h, 'YYYY-MM-DD HH:MM' (local)")
    p.add_argument("-d", "--duration", default="1d", help="window length: 2h, 3d, 1w (default 1d)")
    p.add_argument("--free", action="store_true", help="only rows that are free in the window")


def register(sub, parents):
    p = sub.add_parser("nodes", parents=parents, help="hardware types, free counts and availability windows")
    s = p.add_subparsers(dest="subcommand", metavar="<action>")
    a = s.add_parser("types", parents=parents, help="summary per node_type (default)")
    _window_args(a)
    a.set_defaults(func=cmd_types)
    a = s.add_parser("list", parents=parents, help="individual nodes with next free time")
    _window_args(a)
    a.add_argument("--limit", type=int, default=0, help="max rows (0 = all)")
    a.set_defaults(func=cmd_list)
    a = s.add_parser("show", parents=parents, help="all Blazar properties of one node (name or uid)")
    a.add_argument("node")
    a.set_defaults(func=cmd_show)
    # `cctl nodes` with no action == types
    _window_args(p)
    p.set_defaults(func=cmd_types)


def _window(args) -> tuple[datetime, timedelta]:
    start = parse_when(getattr(args, "start", "now"))
    dur = parse_duration(getattr(args, "duration", "1d"))
    return start, dur


def _collect(ctx, args):
    start, dur = _window(args)
    sites = ctx.sites()
    hosts = []
    for site, result, err in run_parallel(sites, fetch_hosts):
        if err:
            ctx.out.warn(f"{site.name}: {err}")
            continue
        hosts.extend(h for h in result if host_matches(h, args.node_type, True if args.gpu else None, args.contains))
    return hosts, start, dur


def cmd_types(ctx, args):
    hosts, start, dur = _collect(ctx, args)
    rows = summarize_types(hosts, start, dur)
    if args.free:
        rows = [r for r in rows if r["free_in_window"]]
    if not ctx.out.as_json:
        ctx.out.info(f"window: {fmt_dt(start)} + {args.duration} (local time)")
    ctx.out.rows(rows, TYPE_COLUMNS)


def cmd_list(ctx, args):
    hosts, start, dur = _collect(ctx, args)
    rows = host_rows(hosts, start, dur)
    if args.free:
        rows = [r for r in rows if r["free_in_window"]]
    if args.limit:
        rows = rows[: args.limit]
    if not ctx.out.as_json:
        ctx.out.info(f"window: {fmt_dt(start)} + {args.duration} (local time)")
    ctx.out.rows(rows, HOST_COLUMNS)


def cmd_show(ctx, args):
    for site, hosts, err in run_parallel(ctx.sites(), fetch_hosts):
        if err:
            ctx.out.warn(f"{site.name}: {err}")
            continue
        for h in hosts:
            if args.node in (h.get("node_name"), h.get("hypervisor_hostname"), str(h.get("id"))):
                data = {k: v for k, v in sorted(h.items()) if not k.startswith("_")}
                data["reservations"] = [{"lease_id": r.get("lease_id"), "start": r.get("start_date"),
                                         "end": r.get("end_date"), "user": (r.get("extras") or {}).get("user_name")}
                                        for r in h.reservations]
                data["site"] = site.name
                ctx.out.obj(data)
                return 0
    from .errors import NotFound
    raise NotFound(f"node '{args.node}' not found on " + ", ".join(s.name for s in ctx.sites()))
