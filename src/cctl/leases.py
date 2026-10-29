"""Blazar lease helpers shared by `cctl leases`, `cctl up`, `cctl status`."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

from .cloud import Site, run_parallel
from .errors import Ambiguous, CctlError, NotFound, PolicyDenied
from .output import humanize
from .timeutil import from_blazar, now_utc, to_blazar

LEASE_COLUMNS = [("site", "SITE"), ("name", "NAME"), ("status", "STATUS"), ("resources", "RESOURCES"),
                 ("start", "START"), ("end", "END"), ("remaining", "REMAINING"), ("id", "ID")]


def parse_properties(text: str | None) -> list | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def describe_reservation(r: dict) -> str:
    rt = r.get("resource_type", "?")
    if rt == "physical:host":
        props = parse_properties(r.get("resource_properties"))
        what = "host"
        if isinstance(props, list) and len(props) == 3 and isinstance(props[1], str):
            what = f"{props[1].lstrip('$')}={props[2]}"
        elif props:
            what = json.dumps(props)
        n = r.get("min") if r.get("min") == r.get("max") else f"{r.get('min')}-{r.get('max')}"
        return f"{n}x {what}"
    if rt == "virtual:floatingip":
        return f"{r.get('amount', 1)} fip"
    if rt == "flavor:instance":
        return f"{r.get('amount', 1)}x {r.get('flavor_id', 'vm')}"
    return rt


def node_type_of(lease: dict) -> str | None:
    for r in lease.get("reservations", []):
        if r.get("resource_type") == "physical:host":
            props = parse_properties(r.get("resource_properties"))
            if isinstance(props, list) and len(props) == 3 and str(props[1]).lstrip("$") == "node_type":
                return props[2]
    return None


def host_reservation(lease: dict) -> dict | None:
    for r in lease.get("reservations", []):
        if r.get("resource_type") in ("physical:host", "flavor:instance"):
            return r
    return None


def lease_row(site: Site, lease: dict) -> dict:
    start, end = from_blazar(lease.get("start_date")), from_blazar(lease.get("end_date"))
    now = now_utc()
    remaining = None
    if end:
        remaining = humanize((end - now).total_seconds()) if end > now else "expired"
        if start and start > now:
            remaining = f"starts in {humanize((start - now).total_seconds())}"
    return {
        "site": site.name,
        "name": lease.get("name"),
        "id": lease.get("id"),
        "status": lease.get("status"),
        "degraded": lease.get("degraded"),
        "resources": "; ".join(describe_reservation(r) for r in lease.get("reservations", [])) or lease.get("_resources"),
        "node_type": node_type_of(lease),
        "start": start,
        "end": end,
        "remaining": remaining,
        "reservation_ids": [r.get("id") for r in lease.get("reservations", [])],
        "reservation_status": [r.get("status") for r in lease.get("reservations", [])],
        "source": lease.get("_source", "blazar"),
    }


def list_leases(site: Site, allow_fallback: bool = True) -> list[dict]:
    """Leases of the project. If the policy denies the lease API, reconstruct
    the user's leases from the (readable) host allocations instead."""
    try:
        return site.call(lambda: site.blazar.lease.list(), "lease list")
    except PolicyDenied:
        if not allow_fallback:
            raise
    user = site.token_info().get("user")
    allocs = site.call(lambda: site.blazar.allocation.list("os-hosts"), "allocation list")
    hosts = {str(h["id"]): h for h in site.call(lambda: site.blazar.host.list(), "host list")}
    leases: dict[str, dict] = {}
    for a in allocs:
        for r in a.get("reservations", []):
            if (r.get("extras") or {}).get("user_name") != user:
                continue
            host = hosts.get(str(a["resource_id"]), {})
            lease = leases.setdefault(r["lease_id"], {
                "id": r["lease_id"], "name": None, "status": None,
                "start_date": r.get("start_date"), "end_date": r.get("end_date"),
                "reservations": [{"id": r["id"], "resource_type": "physical:host", "min": 0, "max": 0,
                                  "resource_properties": json.dumps(["=", "$node_type", host.get("node_type")])}],
                "_nodes": [], "_source": "allocations",
            })
            lease["_nodes"].append(host.get("node_name"))
            lease["reservations"][0]["min"] = lease["reservations"][0]["max"] = len(lease["_nodes"])
    return list(leases.values())


def all_leases(sites: list[Site], warn=None) -> list[tuple[Site, dict]]:
    out = []
    for site, leases, err in run_parallel(sites, list_leases):
        if err:
            if warn:
                warn(f"{site.name}: {err}")
            continue
        out.extend((site, l) for l in leases)
    return out


def resolve_lease(sites: list[Site], name_or_id: str, warn=None) -> tuple[Site, dict]:
    matches = [(s, l) for s, l in all_leases(sites, warn)
               if name_or_id in (l.get("name"), l.get("id"))]
    if not matches:
        raise NotFound(f"lease '{name_or_id}' not found on " + ", ".join(s.name for s in sites),
                       hint="see: cctl leases")
    if len(matches) > 1:
        raise Ambiguous(f"lease '{name_or_id}' exists on several sites: " + ", ".join(s.name for s, _ in matches),
                        hint="add --site uc|tacc")
    return matches[0]


def get_lease(site: Site, lease_id: str) -> dict:
    return site.call(lambda: site.blazar.lease.get(lease_id), f"lease show {lease_id}")


def build_reservations(site: Site, node_type: str | None = None, count: int = 1, node_name: str | None = None,
                       properties: str | None = None, fips: int = 0, flavor: str | None = None) -> list[dict]:
    res: list[dict] = []
    if site.profile.is_kvm:
        if flavor or node_type:
            res.append({"resource_type": "flavor:instance", "amount": count,
                        "flavor_id": site.flavor(flavor or node_type).id, "affinity": "None"})
    else:
        props = properties
        if node_name:
            props = json.dumps(["=", "$node_name", node_name])
        elif node_type and not props:
            props = json.dumps(["=", "$node_type", node_type])
        if props:
            res.append({"resource_type": "physical:host", "min": count, "max": count,
                        "hypervisor_properties": "", "resource_properties": props})
    if fips:
        res.append({"resource_type": "virtual:floatingip", "network_id": site.public_network_id, "amount": fips})
    if not res:
        raise CctlError("nothing to reserve: give --type NODE_TYPE, --node NAME, --properties JSON or --fip N")
    return res


def create_lease(site: Site, name: str, start: datetime, end: datetime, reservations: list[dict],
                 before_end: str | None = None) -> dict:
    if start <= now_utc():
        start = now_utc() + timedelta(minutes=1)
    if end <= start:
        raise CctlError("lease end must be after its start")
    return site.call(lambda: site.blazar.lease.create(name, to_blazar(start), to_blazar(end), reservations, [],
                                                      before_end=before_end), "lease create")


def set_end(site: Site, lease: dict, new_end: datetime) -> dict:
    return site.call(lambda: site.blazar.lease.update(lease["id"], end_date=to_blazar(new_end)), "lease update")


def delete_lease(site: Site, lease_id: str) -> None:
    site.call(lambda: site.blazar.lease.delete(lease_id), "lease delete")


def wait_lease(site: Site, lease_id: str, timeout: int = 600, interval: int = 10, log=None) -> dict:
    """Block until the lease is ACTIVE (or ERROR). Returns the lease."""
    deadline = time.time() + timeout
    last = None
    while True:
        lease = get_lease(site, lease_id)
        status = lease.get("status")
        rstat = [r.get("status") for r in lease.get("reservations", [])]
        if status != last and log:
            log(f"lease {lease.get('name')} is {status} (reservations: {', '.join(map(str, rstat)) or '-'})")
            last = status
        if status == "ACTIVE" and all(s == "active" for s in rstat):
            return lease
        if status in ("ERROR", "TERMINATED") or any(s == "error" for s in rstat):
            raise CctlError(f"lease {lease.get('name')} ended in status {status} / {rstat}")
        if time.time() > deadline:
            raise CctlError(f"timed out after {timeout}s waiting for lease {lease.get('name')} (status {status})")
        time.sleep(interval)
