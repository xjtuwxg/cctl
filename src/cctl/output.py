"""Table / JSON output. Humans get tables; agents pass --json."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Iterable


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return fmt_dt(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(_cell(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value)


def fmt_dt(dt: datetime | None, local: bool = True) -> str:
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if local:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d %H:%M")


def humanize(seconds: float) -> str:
    """'3d 4h', '2h 10m', '-1h' for negatives."""
    sign = "-" if seconds < 0 else ""
    s = abs(int(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return f"{sign}{d}d {h}h"
    if h:
        return f"{sign}{h}h {m}m"
    return f"{sign}{m}m"


def render_table(rows: Iterable[dict], columns: list[tuple[str, str]]) -> str:
    rows = list(rows)
    headers = [h for _, h in columns]
    keys = [k for k, _ in columns]
    matrix = [[_cell(r.get(k)) for k in keys] for r in rows]
    widths = [len(h) for h in headers]
    for line in matrix:
        for i, c in enumerate(line):
            widths[i] = max(widths[i], len(c))
    fmt = "  ".join("{:<%d}" % w for w in widths)
    out = [fmt.format(*headers).rstrip(), fmt.format(*["-" * w for w in widths]).rstrip()]
    for line in matrix:
        out.append(fmt.format(*line).rstrip())
    return "\n".join(out)


def _json_default(o: Any):
    if isinstance(o, datetime):
        if o.tzinfo is None:
            o = o.replace(tzinfo=timezone.utc)
        return o.isoformat()
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "__dict__"):
        return o.__dict__
    return str(o)


class Output:
    def __init__(self, as_json: bool = False, quiet: bool = False):
        self.as_json = as_json
        self.quiet = quiet

    # -- data -------------------------------------------------------------
    def rows(self, rows: list[dict], columns: list[tuple[str, str]], title: str | None = None,
             empty: str = "(none)") -> None:
        if self.as_json:
            self.obj(rows)
            return
        if title:
            print(f"== {title}")
        if not rows:
            print(empty)
        else:
            print(render_table(rows, columns))

    def obj(self, data: Any) -> None:
        if self.as_json:
            print(json.dumps(data, indent=2, default=_json_default, sort_keys=False))
        else:
            if isinstance(data, dict):
                width = max((len(k) for k in data), default=0)
                for k, v in data.items():
                    print(f"{k:<{width}}  {_cell(v)}")
            else:
                print(json.dumps(data, indent=2, default=_json_default))

    # -- messages (always stderr so JSON stdout stays clean) ---------------
    def info(self, msg: str) -> None:
        if not self.quiet:
            print(f"==> {msg}", file=sys.stderr)

    def warn(self, msg: str) -> None:
        print(f"warning: {msg}", file=sys.stderr)

    def error(self, msg: str, hint: str | None = None) -> None:
        if self.as_json:
            print(json.dumps({"error": msg, "hint": hint}), file=sys.stderr)
        else:
            print(f"error: {msg}", file=sys.stderr)
            if hint:
                print(f"hint: {hint}", file=sys.stderr)
