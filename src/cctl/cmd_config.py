"""cctl config: show/validate the site profiles."""

from __future__ import annotations

from .cloud import POLICY_HINT, run_parallel
from .errors import PolicyDenied


def register(sub, parents):
    p = sub.add_parser("config", parents=parents, help="show or validate site profiles (RC files)")
    s = p.add_subparsers(dest="subcommand", metavar="<action>")
    s.required = True

    a = s.add_parser("list", parents=parents, help="list configured sites")
    a.set_defaults(func=cmd_list)
    a = s.add_parser("path", parents=parents, help="print the config directory")
    a.set_defaults(func=cmd_path)
    a = s.add_parser("check", parents=parents, help="authenticate against each site and verify Blazar access")
    a.set_defaults(func=cmd_check)


def cmd_path(ctx, args):
    print(ctx.config_dir)


def cmd_list(ctx, args):
    rows = [{
        "site": p.name, "slug": p.slug, "region": p.region, "auth": p.auth_type,
        "auth_url": p.auth_url, "file": str(p.path),
    } for p in ctx.profiles]
    d = ctx.defaults
    if not ctx.out.as_json:
        ctx.out.info(f"config dir: {ctx.config_dir}  defaults: site={d.site or '-'} image={d.image} key={d.key_name or '-'} network={d.network}")
    ctx.out.rows(rows, [("site", "SITE"), ("slug", "SLUG"), ("region", "REGION"), ("auth", "AUTH"), ("file", "FILE")])


def cmd_check(ctx, args):
    sites = ctx.sites()

    def probe(site):
        info = site.token_info()
        info["nova"] = "ok"
        try:
            site.conn.compute.servers(limit=1)
        except Exception as exc:  # noqa: BLE001
            info["nova"] = f"FAIL: {exc}"[:80]
        try:
            site.call(lambda: site.blazar.lease.list(), "lease list")
            info["blazar_leases"] = "ok"
        except PolicyDenied:
            info["blazar_leases"] = "DENIED (needs reader role / unrestricted)"
        except Exception as exc:  # noqa: BLE001
            info["blazar_leases"] = f"FAIL: {exc}"[:80]
        try:
            site.call(lambda: site.blazar.host.list(), "host list")
            info["blazar_hosts"] = "ok"
        except Exception as exc:  # noqa: BLE001
            info["blazar_hosts"] = f"FAIL: {exc}"[:80]
        return info

    rows = []
    problems = 0
    for site, info, err in run_parallel(sites, probe):
        if err:
            rows.append({"site": site.name, "user": "-", "project": "-", "roles": "-", "restricted": "-",
                         "nova": f"AUTH FAIL: {err}"[:80], "blazar_leases": "-", "blazar_hosts": "-"})
            problems += 1
            continue
        if "reader" not in info["roles"] or info.get("restricted") or "ok" not in str(info["blazar_leases"]):
            problems += 1
        rows.append(info)
    ctx.out.rows(rows, [("site", "SITE"), ("user", "USER"), ("project", "PROJECT"), ("roles", "ROLES"),
                        ("restricted", "RESTRICTED"), ("nova", "NOVA"), ("blazar_hosts", "BLAZAR HOSTS"),
                        ("blazar_leases", "BLAZAR LEASES")])
    if problems and not ctx.out.as_json:
        ctx.out.warn(POLICY_HINT)
    return 2 if problems else 0
