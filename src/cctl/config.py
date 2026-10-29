"""Profiles (one per Chameleon site) and optional defaults.

A profile is an OpenStack RC file stored in the config directory, e.g.
``~/.config/cctl/uc.sh``. The file is parsed, never sourced, so it does not
need to be executable and cannot run arbitrary shell. Optional defaults live
in ``~/.config/cctl/config.toml``.
"""

from __future__ import annotations

import os
import re
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import CctlError

ENV_CONFIG_DIR = "CCTL_CONFIG_DIR"
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "cctl"

_RC_LINE = re.compile(r"^\s*(?:export\s+)?(OS_[A-Z0-9_]+)\s*=\s*(.*?)\s*$")


def config_dir() -> Path:
    return Path(os.environ.get(ENV_CONFIG_DIR) or DEFAULT_CONFIG_DIR).expanduser()


def parse_openrc(path: Path) -> dict[str, str]:
    """Return the OS_* variables assigned in an RC file."""
    env: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _RC_LINE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        try:
            parts = shlex.split(value, comments=True)
        except ValueError:
            parts = [value]
        env[key] = parts[0] if parts else ""
    return env


def site_slug(auth_url: str, region: str) -> str:
    """Short site name derived from the auth URL: chi.uc -> uc, kvm.tacc -> kvm."""
    m = re.search(r"https?://([a-z0-9-]+)\.([a-z0-9-]+)\.chameleoncloud\.org", auth_url)
    if m:
        first, second = m.group(1), m.group(2)
        return second if first == "chi" else first
    if region:
        return region.split("@")[-1].lower()
    return "unknown"


@dataclass
class Profile:
    name: str
    path: Path
    env: dict[str, str] = field(default_factory=dict)

    @property
    def auth_url(self) -> str:
        return self.env.get("OS_AUTH_URL", "")

    @property
    def region(self) -> str:
        return self.env.get("OS_REGION_NAME", "")

    @property
    def slug(self) -> str:
        return site_slug(self.auth_url, self.region)

    @property
    def auth_type(self) -> str:
        return self.env.get("OS_AUTH_TYPE", "password")

    @property
    def is_kvm(self) -> bool:
        return self.slug.startswith("kvm")

    def validate(self) -> None:
        if not self.auth_url:
            raise CctlError(f"profile '{self.name}': OS_AUTH_URL missing in {self.path}")
        if self.auth_type == "v3applicationcredential":
            for k in ("OS_APPLICATION_CREDENTIAL_ID", "OS_APPLICATION_CREDENTIAL_SECRET"):
                if not self.env.get(k):
                    raise CctlError(f"profile '{self.name}': {k} missing in {self.path}")


@dataclass
class Defaults:
    site: str | None = None
    image: str = "CC-Ubuntu24.04"
    key_name: str | None = None
    network: str = "sharednet1"
    flavor: str = "baremetal"
    ssh_user: str = "cc"
    lease_days: int = 1

    @classmethod
    def load(cls, path: Path) -> "Defaults":
        d = cls()
        if not path.exists():
            return d
        try:
            data = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise CctlError(f"cannot parse {path}: {exc}")
        section = data.get("defaults", data)
        for k in list(d.__dict__):
            if k in section:
                setattr(d, k, section[k])
        return d


def load_profiles(directory: Path | None = None) -> list[Profile]:
    directory = directory or config_dir()
    if not directory.is_dir():
        raise CctlError(
            f"config directory not found: {directory}",
            hint="Download an application-credential RC file per site from the Chameleon portal "
            f"(Identity → Application Credentials) and save it as {directory}/<site>.sh, e.g. uc.sh, tacc.sh.",
        )
    profiles: list[Profile] = []
    for p in sorted(directory.glob("*.sh")):
        env = parse_openrc(p)
        if "OS_AUTH_URL" not in env:
            continue
        profiles.append(Profile(name=p.stem, path=p, env=env))
    if not profiles:
        raise CctlError(
            f"no RC files (*.sh with OS_AUTH_URL) found in {directory}",
            hint="Save the Chameleon RC files as uc.sh / tacc.sh in that directory.",
        )
    return profiles


def load_defaults(directory: Path | None = None) -> Defaults:
    directory = directory or config_dir()
    return Defaults.load(directory / "config.toml")


def select_profiles(profiles: list[Profile], selector: str | None, default: str | None = None) -> list[Profile]:
    """Pick profiles by name/slug. ``None``/``all`` -> every profile."""
    if not selector:
        selector = default or "all"
    if selector == "all":
        return profiles
    wanted = [s.strip().lower() for s in selector.split(",") if s.strip()]
    chosen: list[Profile] = []
    for w in wanted:
        match = [p for p in profiles if p.name.lower() == w or p.slug == w]
        if not match:
            raise CctlError(
                f"unknown site '{w}'",
                hint="configured sites: " + ", ".join(f"{p.name} ({p.slug})" for p in profiles),
            )
        chosen.extend(m for m in match if m not in chosen)
    return chosen
