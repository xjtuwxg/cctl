"""cctl servers / ips / images / keys."""

from __future__ import annotations

from .errors import CctlError, NotFound
from .leases import host_reservation, resolve_lease
from .servers import (SERVER_COLUMNS, all_servers, attach_fip, create_server, delete_server, detach_fip, find_server,
                      list_fips, release_fip, server_row, wait_server, wait_ssh, allocate_fip)
from .cloud import run_parallel

FIP_COLUMNS = [("site", "SITE"), ("ip", "IP"), ("status", "STATUS"), ("server", "SERVER"), ("fixed_ip", "FIXED IP"), ("id", "ID")]


def register(sub, parents):
    p = sub.add_parser("servers", parents=parents, help="list, launch, delete bare-metal/VM servers")
    s = p.add_subparsers(dest="subcommand", metavar="<action>")
    p.set_defaults(func=cmd_list)

    a = s.add_parser("list", parents=parents, help="servers on every site (default)")
    a.set_defaults(func=cmd_list)

    a = s.add_parser("show", parents=parents, help="details of one server")
    a.add_argument("server", help="name or id")
    a.set_defaults(func=cmd_show)

    a = s.add_parser("create", parents=parents, help="launch a server on a lease's reservation")
    add_create_args(a)
    a.add_argument("-l", "--lease", help="lease name/id whose host reservation to use (required on bare-metal sites)")
    a.add_argument("--reservation", help="reservation id (alternative to --lease)")
    a.set_defaults(func=cmd_create)

    a = s.add_parser("delete", parents=parents, help="delete a server")
    a.add_argument("server", help="name or id")
    a.add_argument("--release-ip", action="store_true", help="also release its floating IP(s)")
    a.set_defaults(func=cmd_delete)

    a = s.add_parser("wait", parents=parents, help="wait until a server is ACTIVE (and optionally sshable)")
    a.add_argument("server")
    a.add_argument("--ssh", action="store_true", help="also wait for port 22")
    a.add_argument("--timeout", type=int, default=1800)
    a.set_defaults(func=cmd_wait)

    a = s.add_parser("reboot", parents=parents, help="reboot a server")
    a.add_argument("server")
    a.add_argument("--hard", action="store_true")
    a.set_defaults(func=cmd_reboot)

    p = sub.add_parser("ips", parents=parents, help="floating IPs: list, create, attach, detach, release")
    s = p.add_subparsers(dest="subcommand", metavar="<action>")
    p.set_defaults(func=cmd_ips_list)
    a = s.add_parser("list", parents=parents, help="floating IPs on every site (default)")
    a.set_defaults(func=cmd_ips_list)
    a = s.add_parser("attach", parents=parents, help="attach a floating IP (new or a free one) to a server")
    a.add_argument("server")
    a.add_argument("ip", nargs="?", help="specific address; default: reuse a free one or allocate")
    a.set_defaults(func=cmd_ips_attach)
    a = s.add_parser("detach", parents=parents, help="detach a floating IP from its server")
    a.add_argument("ip")
    a.set_defaults(func=cmd_ips_detach)
    a = s.add_parser("create", parents=parents, help="allocate a floating IP without attaching it")
    a.set_defaults(func=cmd_ips_create)
    a = s.add_parser("release", parents=parents, help="release (delete) a floating IP")
    a.add_argument("ip")
    a.set_defaults(func=cmd_ips_release)

    p = sub.add_parser("images", parents=parents, help="list images (CC-* by default)")
    p.add_argument("-f", "--filter", default="CC-", help="substring filter on the name (default CC-; '' for all)")
    p.set_defaults(func=cmd_images)

    p = sub.add_parser("keys", parents=parents, help="list SSH keypairs")
    p.set_defaults(func=cmd_keys)


def add_create_args(a, shared: bool = True):
    """``shared=False`` skips options the lease parser already defines (for `cctl up`)."""
    if shared:
        a.add_argument("-n", "--name", help="server name (default: lease name)")
    a.add_argument("-i", "--image", help="image name (default: defaults.image, CC-Ubuntu24.04)")
    a.add_argument("-k", "--key", help="keypair name (default: defaults.key_name)")
    a.add_argument("--network", help="network name (default sharednet1)")
    a.add_argument("--flavor", help="flavor (default baremetal; on KVM sites give a VM flavor)")
    a.add_argument("--user-data", help="path to a cloud-init user-data file")
    a.add_argument("--no-ip", dest="attach_ip", action="store_false", default=True, help="do not attach a floating IP")
    if shared:
        a.add_argument("--wait", action="store_true", help="wait until ACTIVE and ssh port open")
        a.add_argument("--timeout", type=int, default=1800, help="seconds to wait (default 1800)")
        a.add_argument("--dry-run", action="store_true", help="print what would be created, do nothing")


def cmd_list(ctx, args):
    rows = all_servers(ctx.sites(), ctx.out.warn)
    rows.sort(key=lambda r: (r["site"], r["name"] or ""))
    ctx.out.rows(rows, SERVER_COLUMNS)


def cmd_show(ctx, args):
    site, server = find_server(ctx.sites(), args.server, ctx.out.warn)
    if ctx.out.as_json:
        data = server.to_dict()
        data["site"] = site.name
        ctx.out.obj(data)
    else:
        row = server_row(site, server, {})
        row["image"] = server.image.get("id") if isinstance(server.image, dict) else row["image"]
        try:
            row["image"] = site.conn.image.get_image(row["image_id"]).name
        except Exception:  # noqa: BLE001
            pass
        row.pop("floating_ips", None)
        ctx.out.obj(row)


def _resolve_reservation(ctx, site, args):
    if args.reservation:
        return args.reservation, None
    if args.lease:
        lsite, lease = resolve_lease([site], args.lease, ctx.out.warn)
        r = host_reservation(lease)
        if not r:
            raise CctlError(f"lease {args.lease} has no host reservation")
        if lease.get("status") not in (None, "ACTIVE"):
            raise CctlError(f"lease {args.lease} is {lease.get('status')}, not ACTIVE yet",
                            hint=f"cctl leases show {args.lease}; or re-run with --wait after it starts")
        return r["id"], lease
    if site.profile.is_kvm:
        return None, None
    raise CctlError("bare-metal sites need --lease NAME (or --reservation ID)", hint="see: cctl leases")


def launch(ctx, site, args, reservation_id, name):
    d = ctx.defaults
    image = args.image or d.image
    key = args.key or d.key_name
    network = args.network or d.network
    flavor = args.flavor or d.flavor
    if not key:
        raise CctlError("no keypair: pass --key NAME or set key_name in config.toml", hint=f"cctl -s {site.name} keys")
    user_data = None
    if args.user_data:
        from pathlib import Path

        user_data = Path(args.user_data).expanduser().read_text()
    plan = {"site": site.name, "name": name, "image": image, "flavor": flavor, "key": key, "network": network,
            "reservation": reservation_id, "floating_ip": args.attach_ip}
    if args.dry_run:
        ctx.out.obj(plan)
        return None
    ctx.out.info(f"{site.name}: creating server {name} (image {image}, key {key}, reservation {reservation_id})")
    server = create_server(site, name, image, flavor, network, key, reservation_id, user_data)
    if args.attach_ip or args.wait:
        server = wait_server(site, server, timeout=args.timeout, log=ctx.out.info)
    ip = None
    if args.attach_ip:
        fip = attach_fip(site, server)
        ip = fip.floating_ip_address
        ctx.out.info(f"attached floating ip {ip}")
        if args.wait:
            wait_ssh(ip, timeout=600, log=ctx.out.info)
    server = site.conn.compute.get_server(server.id)
    row = server_row(site, server)
    if ip:
        row["floating_ip"] = ip
        row["ssh"] = f"cc@{ip}"
    return row


def cmd_create(ctx, args):
    site = ctx.one_site()
    reservation_id, lease = _resolve_reservation(ctx, site, args)
    name = args.name or (lease or {}).get("name") or f"cctl-{site.name}"
    row = launch(ctx, site, args, reservation_id, name)
    if row is None:
        return 0
    if ctx.out.as_json:
        ctx.out.obj(row)
    else:
        ctx.out.rows([row], SERVER_COLUMNS)
        if row.get("ssh"):
            print(f"\nssh {row['ssh']}")


def cmd_delete(ctx, args):
    site, server = find_server(ctx.sites(), args.server, ctx.out.warn)
    if not ctx.confirm(f"Delete server '{server.name}' on {site.name}?"):
        return 1
    released = delete_server(site, server, release_ips=args.release_ip)
    ctx.out.info(f"deleted server {server.name}" + (f", released {', '.join(released)}" if released else ""))
    if ctx.out.as_json:
        ctx.out.obj({"deleted": server.id, "site": site.name, "released_ips": released})


def cmd_wait(ctx, args):
    site, server = find_server(ctx.sites(), args.server, ctx.out.warn)
    server = wait_server(site, server, timeout=args.timeout, log=ctx.out.info)
    row = server_row(site, server)
    if args.ssh and row["floating_ip"]:
        wait_ssh(row["floating_ip"], timeout=args.timeout, log=ctx.out.info)
    ctx.out.rows([row], SERVER_COLUMNS)


def cmd_reboot(ctx, args):
    site, server = find_server(ctx.sites(), args.server, ctx.out.warn)
    site.call(lambda: site.conn.compute.reboot_server(server, "HARD" if args.hard else "SOFT"), "reboot")
    ctx.out.info(f"reboot requested for {server.name}")


def cmd_ips_list(ctx, args):
    rows = []
    for site, result, err in run_parallel(ctx.sites(), list_fips):
        if err:
            ctx.out.warn(f"{site.name}: {err}")
        else:
            rows.extend(result)
    ctx.out.rows(rows, FIP_COLUMNS)


def cmd_ips_attach(ctx, args):
    site, server = find_server(ctx.sites(), args.server, ctx.out.warn)
    fip = attach_fip(site, server, ip=args.ip)
    ctx.out.info(f"{fip.floating_ip_address} -> {server.name}")
    ctx.out.obj({"site": site.name, "ip": fip.floating_ip_address, "server": server.name})


def cmd_ips_detach(ctx, args):
    site = ctx.one_site()
    detach_fip(site, args.ip)
    ctx.out.info(f"detached {args.ip}")


def cmd_ips_create(ctx, args):
    site = ctx.one_site()
    fip = allocate_fip(site)
    ctx.out.obj({"site": site.name, "ip": fip.floating_ip_address, "id": fip.id})


def cmd_ips_release(ctx, args):
    site = ctx.one_site()
    if not ctx.confirm(f"Release floating IP {args.ip} on {site.name}?"):
        return 1
    release_fip(site, args.ip)
    ctx.out.info(f"released {args.ip}")


def cmd_images(ctx, args):
    rows = []
    for site, imgs, err in run_parallel(ctx.sites(), lambda s: list(s.conn.image.images())):
        if err:
            ctx.out.warn(f"{site.name}: {err}")
            continue
        for i in imgs:
            if args.filter and args.filter.lower() not in (i.name or "").lower():
                continue
            rows.append({"site": site.name, "name": i.name, "status": i.status, "size_gb": round((i.size or 0) / 1e9, 1),
                         "created": i.created_at, "id": i.id})
    rows.sort(key=lambda r: (r["site"], r["name"] or ""), reverse=False)
    ctx.out.rows(rows, [("site", "SITE"), ("name", "NAME"), ("status", "STATUS"), ("size_gb", "GB"), ("created", "CREATED"), ("id", "ID")])


def cmd_keys(ctx, args):
    rows = []
    for site, keys, err in run_parallel(ctx.sites(), lambda s: list(s.conn.compute.keypairs())):
        if err:
            ctx.out.warn(f"{site.name}: {err}")
            continue
        rows.extend({"site": site.name, "name": k.name, "fingerprint": k.fingerprint, "type": k.type} for k in keys)
    ctx.out.rows(rows, [("site", "SITE"), ("name", "NAME"), ("type", "TYPE"), ("fingerprint", "FINGERPRINT")])
