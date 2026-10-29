"""Hardware inventory and availability, computed from Blazar hosts + allocations.

Blazar's host list is readable with a plain member credential, and the
allocation list tells which host is reserved when, so availability can be
computed without the (policy-protected) lease API.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .cloud import Site
from .timeutil import from_blazar, now_utc

PUBLIC_API = "https://api.chameleoncloud.org/sites/{slug}/clusters/chameleon/nodes"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "cctl"
CACHE_TTL = 24 * 3600


def fetch_public_nodes(slug: str, refresh: bool = False) -> dict[str, dict]:
    """Chameleon's public hardware catalogue, keyed by node uid. Cached for a day.
    Blazar's host records miss GPU/RAM fields for newer nodes; this fills them in."""
    cache = CACHE_DIR / f"nodes-{slug}.json"
    if not refresh and cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL:
        try:
            return json.loads(cache.read_text())
        except json.JSONDecodeError:
            pass
    try:
        import requests

        resp = requests.get(PUBLIC_API.format(slug=slug), timeout=30)
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception:  # noqa: BLE001 - catalogue is optional
        if cache.exists():
            try:
                return json.loads(cache.read_text())
            except json.JSONDecodeError:
                pass
        return {}
    by_uid = {n["uid"]: n for n in items if n.get("uid")}
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(by_uid))
    except OSError:
        pass
    return by_uid


def _merge_public(h: "HostInfo", pub: dict | None) -> None:
    """Fill gaps in a Blazar host record from the public catalogue."""
    if not pub:
        return
    gpu = pub.get("gpu") or {}
    if h.get("gpu.gpu") in (None, "") and gpu:
        h["gpu.gpu"] = str(bool(gpu.get("gpu")))
        h.setdefault("gpu.gpu_count", gpu.get("gpu_count"))
        h.setdefault("gpu.gpu_model", gpu.get("gpu_model"))
    if not h.get("gpu.gpu_model") and gpu.get("gpu_model"):
        h["gpu.gpu_model"] = gpu["gpu_model"]
    mem = pub.get("main_memory") or {}
    if not h.get("main_memory.humanized_ram_size") and mem.get("humanized_ram_size"):
        h["main_memory.humanized_ram_size"] = mem["humanized_ram_size"]
    arch = pub.get("architecture") or {}
    if not h.get("architecture.smt_size") and arch.get("smt_size"):
        h["architecture.smt_size"] = arch["smt_size"]
    proc = pub.get("processor") or {}
    if not h.get("processor.model") and proc.get("model"):
        h["processor.model"] = proc["model"]
    if not h.get("node_name") and pub.get("node_name"):
        h["node_name"] = pub["node_name"]
    h.setdefault("infiniband", pub.get("infiniband"))
    disks = pub.get("storage_devices") or []
    if disks and not h.get("storage_devices.0.humanized_size"):
        h["_storage"] = ", ".join(f"{d.get('humanized_size')} {d.get('media_type', '')}".strip() for d in disks)


def _truthy(v: Any) -> bool:
    return str(v).lower() in ("true", "1", "yes")


class HostInfo(dict):
    """A Blazar host enriched with its reservations and a free-window check."""

    @property
    def reservations(self) -> list[dict]:
        return self.get("_reservations", [])

    def busy_in(self, start: datetime, end: datetime) -> list[dict]:
        hits = []
        for r in self.reservations:
            rs, re_ = from_blazar(r.get("start_date")), from_blazar(r.get("end_date"))
            if rs is None or re_ is None:
                continue
            if rs < end and re_ > start:
                hits.append(r)
        return hits

    def next_free(self, start: datetime, duration: timedelta, horizon_days: int = 60) -> datetime | None:
        """Earliest time >= start at which the host is free for ``duration``."""
        t = start
        limit = start + timedelta(days=horizon_days)
        while t < limit:
            hits = self.busy_in(t, t + duration)
            if not hits:
                return t
            t = max(from_blazar(h["end_date"]) for h in hits) + timedelta(minutes=1)
        return None


def fetch_hosts(site: Site, with_allocations: bool = True, refresh: bool = False) -> list[HostInfo]:
    hosts = site.call(lambda: site.blazar.host.list(), "host list")
    infos = [HostInfo(h) for h in hosts]
    public = fetch_public_nodes(site.slug, refresh=refresh)
    for h in infos:
        _merge_public(h, public.get(str(h.get("hypervisor_hostname"))))
    if with_allocations:
        allocs = site.call(lambda: site.blazar.allocation.list("os-hosts"), "allocation list")
        by_id = {str(a["resource_id"]): a.get("reservations", []) for a in allocs}
        for h in infos:
            h["_reservations"] = by_id.get(str(h.get("id")), [])
    for h in infos:
        h["_site"] = site.name
        h["_gpu"] = _truthy(h.get("gpu.gpu"))
        h["_reservable"] = h.get("reservable", True) is not False and not _truthy(h.get("disabled", False))
    return infos


def host_matches(h: HostInfo, node_type: str | None = None, gpu: bool | None = None,
                 contains: str | None = None) -> bool:
    nt = str(h.get("node_type", ""))
    if node_type and node_type.lower() != nt.lower():
        return False
    if gpu is True and not h["_gpu"]:
        return False
    if contains and contains.lower() not in nt.lower() and contains.lower() not in str(h.get("gpu.gpu_model", "")).lower():
        return False
    return True


def summarize_types(hosts: list[HostInfo], start: datetime, duration: timedelta) -> list[dict]:
    """Per (site, node_type): total / reservable / free in window / free now."""
    groups: dict[tuple[str, str], list[HostInfo]] = {}
    for h in hosts:
        groups.setdefault((h["_site"], str(h.get("node_type", "?"))), []).append(h)
    now = now_utc()
    rows = []
    end = start + duration
    for (site, nt), hs in sorted(groups.items()):
        gpu_models = sorted({str(h.get("gpu.gpu_model")) for h in hs if h["_gpu"] and h.get("gpu.gpu_model")})
        reservable = [h for h in hs if h["_reservable"]]
        free_window = [h for h in reservable if not h.busy_in(start, end)]
        free_now = [h for h in reservable if not h.busy_in(now, now + timedelta(minutes=1))]
        ex = hs[0]
        rows.append({
            "site": site,
            "node_type": nt,
            "total": len(hs),
            "reservable": len(reservable),
            "free_now": len(free_now),
            "free_in_window": len(free_window),
            "gpu": ", ".join(gpu_models) or ("yes" if ex["_gpu"] else "-"),
            "gpu_count": ex.get("gpu.gpu_count") if ex["_gpu"] else None,
            "cpus": ex.get("architecture.smt_size") or ex.get("vcpus"),
            "ram": ex.get("main_memory.humanized_ram_size") or ex.get("memory_mb"),
            "cpu_model": ex.get("processor.model"),
            "storage": ex.get("_storage"),
        })
    return rows


def host_rows(hosts: list[HostInfo], start: datetime, duration: timedelta) -> list[dict]:
    rows = []
    for h in hosts:
        busy = h.busy_in(start, start + duration)
        nf = h.next_free(start, duration)
        rows.append({
            "site": h["_site"],
            "name": h.get("node_name"),
            "node_type": h.get("node_type"),
            "blazar_id": h.get("id"),
            "uid": h.get("hypervisor_hostname"),
            "reservable": h["_reservable"],
            "free_in_window": not busy,
            "next_free": nf,
            "busy_until": max((from_blazar(b["end_date"]) for b in busy), default=None),
            "gpu": h.get("gpu.gpu_model") if h["_gpu"] else None,
            "reservations": len(h.reservations),
        })
    rows.sort(key=lambda r: (r["site"], str(r["node_type"]), not r["free_in_window"], str(r["name"])))
    return rows
