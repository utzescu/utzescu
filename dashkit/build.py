"""Runs the configured sources and writes the dashboard HTML."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, sources

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "dashboard.config.json"
TEMPLATE = ROOT / "dashkit" / "shell_template.html"
PLACEHOLDER = "/*__DASHBOARD_DATA__*/null"


def load_config(path=None):
    path = Path(path or DEFAULT_CONFIG).expanduser()
    with path.open() as fh:
        config = json.load(fh)
    config.setdefault("title", "Dashboard")
    config.setdefault("output", str(Path.home() / "Dashboard" / "dashboard.html"))
    config.setdefault("sources", [])
    config["_path"] = str(path)
    return config


def build(config, *, only=None, warn=None, template=None):
    warn = warn or (lambda msg: print(f"warning: {msg}", file=sys.stderr))
    reports, problems = [], []

    for entry in config["sources"]:
        name = entry.get("module")
        if not name or entry.get("enabled") is False:
            continue
        if only and name not in only:
            continue
        try:
            module = sources.load(name)
        except ImportError as exc:
            problems.append(f"{name}: {exc}")
            warn(f"{name}: cannot import ({exc})")
            continue

        collected = []

        def scoped(msg, _n=name):
            collected.append(msg)
            warn(f"{_n}: {msg}" if not str(msg).startswith(_n) else msg)

        try:
            report = module.collect(entry.get("options") or {}, scoped)
        except Exception as exc:  # a failing source must not sink the dashboard
            problems.append(f"{name}: {exc}")
            warn(f"{name}: failed ({exc.__class__.__name__}: {exc})")
            continue
        if report is None:
            problems.append(f"{name}: no data — " + ("; ".join(collected) or "source returned nothing"))
            continue
        if entry.get("title"):
            report["title"] = entry["title"]
        reports.append(report)

    return {
        "title": config["title"],
        "generated": datetime.now().astimezone().isoformat(timespec="minutes"),
        "version": __version__,
        "config_path": config.get("_path", ""),
        "reports": reports,
        "problems": problems,
        "catalog": [{"module": m, "title": t, "description": d} for m, t, d in sources.available()],
    }


def render(data, out_path, template=None):
    template_path = Path(template or TEMPLATE)
    html = template_path.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise SystemExit(f"{template_path} has no {PLACEHOLDER} placeholder")
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    out = Path(out_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html.replace(PLACEHOLDER, payload), encoding="utf-8")
    return out


def summarize(data):
    if not data["reports"]:
        return "no reports produced"
    parts = []
    for report in data["reports"]:
        parts.append(f"{report['title']}: {len(report['rows']):,} rows")
    return " · ".join(parts)
