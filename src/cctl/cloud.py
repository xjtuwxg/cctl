"""One ``Site`` per profile: lazy OpenStack SDK connection + Blazar client."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import cached_property
from typing import Any, Callable, Iterable

from .config import Profile
from .errors import CctlError, NotFound, PolicyDenied

POLICY_HINT = (
    "Chameleon now needs the application credential to carry the 'reader' role in addition to 'member' "
    "and (for creating/updating leases) the 'unrestricted' box ticked. Create a new credential at "
    "https://chi.<site>.chameleoncloud.org/identity/application_credentials/ with roles member+reader and "
    "Unrestricted enabled, download its RC file and replace the one in your config directory. "
    "Run 'cctl config check' afterwards."
)


def _is_policy_error(exc: BaseException) -> bool:
    text = str(exc)
    return "Policy doesn't allow" in text or "policy does not allow" in text.lower() or " 403" in text or "Forbidden" in text


class Site:
    def __init__(self, profile: Profile):
        profile.validate()
        self.profile = profile
        self.name = profile.name
        self.slug = profile.slug
        self.region = profile.region

    def __repr__(self) -> str:
        return f"Site({self.name})"

    # -- connections ------------------------------------------------------
    @cached_property
    def conn(self):
        import openstack

        env = self.profile.env
        auth_type = self.profile.auth_type
        auth: dict[str, Any] = {"auth_url": self.profile.auth_url}
        if auth_type == "v3applicationcredential":
            auth["application_credential_id"] = env["OS_APPLICATION_CREDENTIAL_ID"]
            auth["application_credential_secret"] = env["OS_APPLICATION_CREDENTIAL_SECRET"]
        else:
            for src, dst in (
                ("OS_USERNAME", "username"),
                ("OS_PASSWORD", "password"),
                ("OS_PROJECT_NAME", "project_name"),
                ("OS_PROJECT_ID", "project_id"),
                ("OS_USER_DOMAIN_NAME", "user_domain_name"),
                ("OS_PROJECT_DOMAIN_NAME", "project_domain_name"),
                ("OS_USER_DOMAIN_ID", "user_domain_id"),
                ("OS_PROJECT_DOMAIN_ID", "project_domain_id"),
                ("OS_TOKEN", "token"),
            ):
                if env.get(src):
                    auth[dst] = env[src]
        return openstack.connect(
            auth_type=auth_type,
            auth=auth,
            region_name=self.region or None,
            interface=env.get("OS_INTERFACE", "public"),
            identity_api_version=env.get("OS_IDENTITY_API_VERSION", "3"),
            app_name="cctl",
        )

    @cached_property
    def session(self):
        return self.conn.session

    @cached_property
    def blazar(self):
        from blazarclient import client as blazar_client

        return blazar_client.Client(
            session=self.session,
            service_type="reservation",
            region_name=self.region or None,
            interface=self.profile.env.get("OS_INTERFACE", "public"),
        )

    def call(self, fn: Callable[[], Any], what: str = "request") -> Any:
        """Run an API call, translating policy errors into a helpful message."""
        try:
            return fn()
        except CctlError:
            raise
        except Exception as exc:  # noqa: BLE001 - SDK raises many types
            if _is_policy_error(exc):
                raise PolicyDenied(f"{self.name}: {what} denied by policy: {exc}", hint=POLICY_HINT) from exc
            raise CctlError(f"{self.name}: {what} failed: {exc}") from exc

    # -- identity ---------------------------------------------------------
    def token_info(self) -> dict[str, Any]:
        token = self.session.get_token()
        resp = self.session.get(self.profile.auth_url.rstrip("/") + "/auth/tokens",
                                headers={"X-Subject-Token": token})
        t = resp.json()["token"]
        appcred = t.get("application_credential") or {}
        return {
            "site": self.name,
            "region": self.region,
            "user": (t.get("user") or {}).get("name"),
            "project": (t.get("project") or {}).get("name"),
            "project_id": (t.get("project") or {}).get("id"),
            "roles": sorted(r["name"] for r in t.get("roles", [])),
            "app_credential": bool(appcred),
            "restricted": appcred.get("restricted"),
            "expires": t.get("expires_at"),
        }

    # -- lookups ----------------------------------------------------------
    def network(self, name_or_id: str):
        net = self.conn.network.find_network(name_or_id)
        if not net:
            raise NotFound(f"{self.name}: network '{name_or_id}' not found")
        return net

    @cached_property
    def public_network_id(self) -> str:
        return self.network("public").id

    def image(self, name_or_id: str):
        img = self.conn.image.find_image(name_or_id)
        if not img:
            raise NotFound(f"{self.name}: image '{name_or_id}' not found",
                           hint=f"try: cctl -s {self.name} images --filter {name_or_id.split('-')[0]}")
        return img

    def flavor(self, name_or_id: str):
        fl = self.conn.compute.find_flavor(name_or_id)
        if not fl:
            raise NotFound(f"{self.name}: flavor '{name_or_id}' not found")
        return fl

    def keypair(self, name: str):
        kp = self.conn.compute.find_keypair(name)
        if not kp:
            raise NotFound(f"{self.name}: keypair '{name}' not found", hint=f"see: cctl -s {self.name} keys")
        return kp


def run_parallel(sites: Iterable[Site], fn: Callable[[Site], Any], max_workers: int = 6) -> list[tuple[Site, Any, BaseException | None]]:
    """Apply ``fn`` to every site concurrently. Returns (site, result, error) triples in input order."""
    sites = list(sites)

    def wrapped(site: Site):
        try:
            return site, fn(site), None
        except BaseException as exc:  # noqa: BLE001
            return site, None, exc

    if len(sites) <= 1:
        return [wrapped(s) for s in sites]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(sites))) as pool:
        return list(pool.map(wrapped, sites))
