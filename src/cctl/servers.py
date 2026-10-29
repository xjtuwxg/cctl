"""Nova/Neutron helpers: servers and floating IPs."""

from __future__ import annotations

import socket
import time
from typing import Any

from .cloud import Site, run_parallel
from .errors import Ambiguous, CctlError, NotFound

SERVER_COLUMNS = [("site", "SITE"), ("name", "NAME"), ("status", "STATUS"), ("floating_ip", "PUBLIC IP"),
                  ("fixed_ip", "FIXED IP"), ("image", "IMAGE"), ("flavor", "FLAVOR"), ("key_name", "KEY"),
                  ("created", "CREATED"), ("id", "ID")]


def _addresses(server) -> tuple[list[str], list[str]]:
    fixed, floating = [], []
    for _net, addrs in (server.addresses or {}).items():
        for a in addrs:
            if a.get("OS-EXT-IPS:type") == "floating":
                floating.append(a.get("addr"))
            else:
                fixed.append(a.get("addr"))
    return fixed, floating


def server_row(site: Site, server, image_names: dict[str, str] | None = None) -> dict:
    fixed, floating = _addresses(server)
    img = server.image or {}
    img_id = img.get("id") if isinstance(img, dict) else getattr(img, "id", None)
    flavor = server.flavor or {}
    return {
        "site": site.name,
        "name": server.name,
        "id": server.id,
        "status": server.status,
        "task_state": getattr(server, "task_state", None),
        "floating_ip": floating[0] if floating else None,
        "floating_ips": floating,
        "fixed_ip": fixed[0] if fixed else None,
        "image": (image_names or {}).get(img_id) or img_id,
        "image_id": img_id,
        "flavor": flavor.get("original_name") or flavor.get("name") or flavor.get("id"),
        "key_name": server.key_name,
        "created": server.created_at,
        "ssh": f"{DEFAULT_USER}@{floating[0]}" if floating else None,
    }


DEFAULT_USER = "cc"


def image_names(site: Site) -> dict[str, str]:
    try:
        return {i.id: i.name for i in site.conn.image.images()}
    except Exception:  # noqa: BLE001
        return {}


def list_servers(site: Site) -> list[dict]:
    servers = list(site.call(lambda: site.conn.compute.servers(details=True), "server list"))
    names = image_names(site) if servers else {}
    return [server_row(site, s, names) for s in servers]


def all_servers(sites: list[Site], warn=None) -> list[dict]:
    rows = []
    for site, result, err in run_parallel(sites, list_servers):
        if err:
            if warn:
                warn(f"{site.name}: {err}")
            continue
        rows.extend(result)
    return rows


def find_server(sites: list[Site], name_or_id: str, warn=None) -> tuple[Site, Any]:
    hits = []
    for site, servers, err in run_parallel(sites, lambda s: list(s.conn.compute.servers(details=True))):
        if err:
            if warn:
                warn(f"{site.name}: {err}")
            continue
        hits.extend((site, s) for s in servers if name_or_id in (s.name, s.id))
    if not hits:
        raise NotFound(f"server '{name_or_id}' not found on " + ", ".join(s.name for s in sites), hint="see: cctl servers")
    if len(hits) > 1:
        raise Ambiguous(f"server '{name_or_id}' exists on several sites: " + ", ".join(s.name for s, _ in hits),
                        hint="add --site uc|tacc")
    return hits[0]


def create_server(site: Site, name: str, image: str, flavor: str, network: str, key_name: str | None,
                  reservation_id: str | None, user_data: str | None = None) -> Any:
    img = site.image(image)
    net = site.network(network)
    kwargs: dict[str, Any] = {
        "name": name,
        "image_id": img.id,
        "networks": [{"uuid": net.id}],
    }
    if site.profile.is_kvm and reservation_id and not flavor.startswith("reservation:"):
        flavor = f"reservation:{reservation_id}"
        reservation_id = None
    fl = site.flavor(flavor)
    kwargs["flavor_id"] = fl.id
    if key_name:
        kwargs["key_name"] = site.keypair(key_name).name
    if reservation_id:
        kwargs["scheduler_hints"] = {"reservation": reservation_id}
    if user_data:
        import base64

        kwargs["user_data"] = base64.b64encode(user_data.encode()).decode()
    return site.call(lambda: site.conn.compute.create_server(**kwargs), "server create")


def wait_server(site: Site, server, timeout: int = 1800, log=None) -> Any:
    """Bare-metal deploys take 10-20 minutes."""
    deadline = time.time() + timeout
    last = None
    while True:
        server = site.conn.compute.get_server(server.id)
        state = f"{server.status}/{getattr(server, 'task_state', None) or '-'}"
        if state != last and log:
            log(f"server {server.name}: {state}")
            last = state
        if server.status == "ACTIVE":
            return server
        if server.status == "ERROR":
            fault = getattr(server, "fault", None) or {}
            raise CctlError(f"server {server.name} went to ERROR: {fault.get('message', fault)}")
        if time.time() > deadline:
            raise CctlError(f"timed out after {timeout}s waiting for server {server.name} ({state})")
        time.sleep(15)


def wait_ssh(host: str, port: int = 22, timeout: int = 600, log=None) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5):
                if log:
                    log(f"ssh port open on {host}")
                return True
        except OSError:
            time.sleep(10)
    return False


# -- floating IPs ----------------------------------------------------------

def list_fips(site: Site) -> list[dict]:
    rows = []
    servers = {}
    for ip in site.call(lambda: list(site.conn.network.ips()), "floating ip list"):
        server_name = None
        if ip.port_id:
            if ip.port_id not in servers:
                try:
                    port = site.conn.network.get_port(ip.port_id)
                    dev = port.device_id
                    servers[ip.port_id] = site.conn.compute.get_server(dev).name if dev else None
                except Exception:  # noqa: BLE001
                    servers[ip.port_id] = None
            server_name = servers[ip.port_id]
        rows.append({"site": site.name, "ip": ip.floating_ip_address, "status": ip.status,
                     "fixed_ip": ip.fixed_ip_address, "server": server_name, "id": ip.id,
                     "created": ip.created_at})
    return rows


def free_fip(site: Site):
    for ip in site.conn.network.ips():
        if not ip.port_id and ip.floating_network_id == site.public_network_id:
            return ip
    return None


def allocate_fip(site: Site):
    return site.call(lambda: site.conn.network.create_ip(floating_network_id=site.public_network_id), "floating ip create")


def attach_fip(site: Site, server, ip=None, reuse: bool = True):
    """Attach a floating IP (new or free existing) to the server's first port."""
    ports = list(site.conn.network.ports(device_id=server.id))
    if not ports:
        raise CctlError(f"server {server.name} has no network port yet; wait for ACTIVE")
    if ip is None and reuse:
        ip = free_fip(site)
    if ip is None:
        ip = allocate_fip(site)
    elif isinstance(ip, str):
        found = site.conn.network.find_ip(ip)
        if not found:
            raise NotFound(f"floating ip {ip} not found on {site.name}")
        ip = found
    return site.call(lambda: site.conn.network.update_ip(ip, port_id=ports[0].id), "floating ip attach")


def detach_fip(site: Site, ip_address: str):
    ip = site.conn.network.find_ip(ip_address)
    if not ip:
        raise NotFound(f"floating ip {ip_address} not found on {site.name}")
    return site.call(lambda: site.conn.network.update_ip(ip, port_id=None), "floating ip detach")


def release_fip(site: Site, ip_address: str) -> None:
    ip = site.conn.network.find_ip(ip_address)
    if not ip:
        raise NotFound(f"floating ip {ip_address} not found on {site.name}")
    site.call(lambda: site.conn.network.delete_ip(ip), "floating ip delete")


def delete_server(site: Site, server, release_ips: bool = False, wait: bool = True) -> list[str]:
    _, floating = _addresses(server)
    site.call(lambda: site.conn.compute.delete_server(server), "server delete")
    if wait:
        site.conn.compute.wait_for_delete(server, wait=600)
    released = []
    if release_ips:
        for addr in floating:
            try:
                release_fip(site, addr)
                released.append(addr)
            except CctlError:
                pass
    return released
