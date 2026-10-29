"""Entry point. ``cctl --help``.

Every command module exposes ``register(subparsers, parents)`` and sets
``func=<callable(ctx, args)>`` on its parser. Commands return an int exit code
(or None for 0). Data goes to stdout (table or JSON); messages go to stderr.
"""

from __future__ import annotations

import argparse
import os
import sys
from functools import cached_property
from pathlib import Path

from . import __version__
from .cloud import Site
from .config import Defaults, Profile, config_dir, load_defaults, load_profiles, select_profiles
from .errors import CctlError
from .output import Output


class Context:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.out = Output(as_json=bool(getattr(args, "json", False)), quiet=bool(getattr(args, "quiet", False)))
        self.debug = bool(getattr(args, "debug", False))
        if getattr(args, "config_dir", None):
            os.environ["CCTL_CONFIG_DIR"] = str(Path(args.config_dir).expanduser())
        self.config_dir: Path = config_dir()
        self._site_cache: dict[str, Site] = {}

    @cached_property
    def profiles(self) -> list[Profile]:
        return load_profiles(self.config_dir)

    @cached_property
    def defaults(self) -> Defaults:
        return load_defaults(self.config_dir)

    def site(self, profile: Profile) -> Site:
        if profile.name not in self._site_cache:
            self._site_cache[profile.name] = Site(profile)
        return self._site_cache[profile.name]

    def sites(self, selector: str | None = None, *, single: bool = False, default_all: bool = True) -> list[Site]:
        """Resolve ``--site``. ``single=True`` demands exactly one site (uses the
        configured default when only one profile exists or ``defaults.site`` is set)."""
        selector = selector or getattr(self.args, "site", None)
        if single and not selector:
            if len(self.profiles) == 1:
                selector = self.profiles[0].name
            elif self.defaults.site:
                selector = self.defaults.site
            else:
                raise CctlError(
                    "this command needs one site: pass --site " + "|".join(p.name for p in self.profiles),
                    hint="or set `site = \"uc\"` under [defaults] in " + str(self.config_dir / "config.toml"),
                )
        profiles = select_profiles(self.profiles, selector, default="all" if default_all else self.defaults.site)
        if single and len(profiles) != 1:
            raise CctlError("this command needs exactly one site, got: " + ", ".join(p.name for p in profiles))
        return [self.site(p) for p in profiles]

    def one_site(self, selector: str | None = None) -> Site:
        return self.sites(selector, single=True)[0]

    def confirm(self, prompt: str) -> bool:
        if getattr(self.args, "yes", False):
            return True
        if not sys.stdin.isatty():
            raise CctlError("refusing destructive action without --yes (stdin is not a terminal)")
        reply = input(f"{prompt} [y/N] ").strip().lower()
        return reply in ("y", "yes")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-s", "--site", default=argparse.SUPPRESS,
                        help="site profile(s): uc, tacc, kvm, 'all' or a comma list (default: all for read commands)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="machine-readable JSON on stdout")
    common.add_argument("-y", "--yes", action="store_true", default=argparse.SUPPRESS, help="do not ask for confirmation")
    common.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help="suppress progress messages")

    parser = argparse.ArgumentParser(
        prog="cctl",
        description="Chameleon Cloud control: find hardware, reserve it, launch bare-metal servers, keep leases alive.",
        epilog="Run 'cctl <command> --help' for details. Agents: add --json to any command.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"cctl {__version__}")
    parser.add_argument("--config-dir", help="directory with <site>.sh RC files (default: $CCTL_CONFIG_DIR or ~/.config/cctl)")
    parser.add_argument("--debug", action="store_true", help="show tracebacks")
    # Global copies of the common flags so `cctl -s uc leases` and `cctl leases -s uc` both work.
    parser.add_argument("-s", "--site", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", default=False, help=argparse.SUPPRESS)
    parser.add_argument("-y", "--yes", action="store_true", default=False, help=argparse.SUPPRESS)
    parser.add_argument("-q", "--quiet", action="store_true", default=False, help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    from . import cmd_config, cmd_hardware, cmd_leases, cmd_servers, cmd_workflows, cmd_ssh

    for mod in (cmd_workflows, cmd_hardware, cmd_leases, cmd_servers, cmd_config, cmd_ssh):
        mod.register(sub, [common])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ctx = Context(args)
    try:
        rc = args.func(ctx, args)
        return int(rc or 0)
    except CctlError as exc:
        ctx.out.error(exc.message, exc.hint)
        if ctx.debug:
            raise
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        if ctx.debug:
            raise
        ctx.out.error(f"{type(exc).__name__}: {exc}", hint="re-run with --debug for a traceback")
        return 1


if __name__ == "__main__":
    sys.exit(main())
