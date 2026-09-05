"""Runs the configured sources and writes the dashboard HTML."""

from __future__ import annotations

import json
import re
import socket
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, sources

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "dashboard.config.json"
TEMPLATE = ROOT / "dashkit" / "shell_template.html"
PLACEHOLDER = "/*__DASHBOARD_DATA__*/null"


def device_name(config=None):
    """This machine's name, as it appears in the Device filter."""
    raw = (config or {}).get("device") or socket.gethostname() or "this-device"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-") or "this-device"


def resolve_path(value, base):
    """Relative paths in the config are relative to the config file itself, so
    the same folder works on every device and on any drive letter."""
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (base / path)


def load_config(path=None):
    path = Path(path or DEFAULT_CONFIG).expanduser().resolve()
    with path.open() as fh:
        config = json.load(fh)
    config.setdefault("title", "Dashboard")
    config.setdefault("output", str(Path.home() / "Dashboard" / "dashboard.html"))
    config.setdefault("sources", [])
    config.setdefault("data_dir", "data")
    config["_path"] = str(path)
    config["_dir"] = str(path.parent)
    config["_output_path"] = str(resolve_path(config["output"], path.parent))
    config["_data_path"] = (str(resolve_path(config["data_dir"], path.parent))
                            if config["data_dir"] else "")
    config["_device"] = device_name(config)
    return config


# --------------------------------------------------------- device snapshots

def save_snapshot(config, reports):
    """Each device keeps its own copy of what it collected, so a shared folder
    (Drive, Dropbox, a synced network share) accumulates every machine."""
    data_dir = config.get("_data_path")
    if not data_dir or not reports:
        return None
    target = Path(data_dir) / config["_device"]
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec="minutes")
    for report in reports:
        payload = dict(report)
        payload["generated"] = stamp
        (target / f"{report['id']}.json").write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    (target / "_device.json").write_text(
        json.dumps({"device": config["_device"], "generated": stamp,
                    "reports": [r["id"] for r in reports]}, indent=2), encoding="utf-8")
    return target


def load_snapshots(config, warn):
    """{report_id: [(device, report), ...]} across every device in the folder."""
    data_dir = config.get("_data_path")
    found, devices = {}, []
    if not data_dir or not Path(data_dir).exists():
        return found, devices
    for device_dir in sorted(p for p in Path(data_dir).iterdir() if p.is_dir()):
        summary = {"device": device_dir.name, "generated": "", "reports": [], "rows": 0}
        for snapshot in sorted(device_dir.glob("*.json")):
            if snapshot.name == "_device.json":
                try:
                    summary["generated"] = json.loads(snapshot.read_text())["generated"]
                except (OSError, ValueError, KeyError):
                    pass
                continue
            try:
                report = json.loads(snapshot.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                warn(f"ignoring unreadable snapshot {snapshot}: {exc}")
                continue
            found.setdefault(report["id"], []).append((device_dir.name, report))
            summary["reports"].append(report["id"])
            summary["rows"] += len(report.get("rows", []))
        if summary["reports"]:
            devices.append(summary)
    return found, devices


def merge_reports(snapshots, warn, order=()):
    """One report per id, rows tagged with the device they came from."""
    merged = []
    for report_id, entries in snapshots.items():
        entries.sort(key=lambda e: e[1].get("generated", ""))
        base = dict(entries[-1][1])           # newest spec wins
        devices = sorted({device for device, _ in entries})
        rows, dim_meta = [], {}
        for device, report in entries:
            for row in report.get("rows", []):
                row = dict(row)
                row["_dev"] = device
                rows.append(row)
            for field, values in (report.get("dim_meta") or {}).items():
                dim_meta.setdefault(field, {}).update(values)
        base["rows"] = rows
        base["dim_meta"] = dim_meta
        base.pop("generated", None)
        if len(devices) > 1:
            base["dimensions"] = [{"field": "_dev", "label": "Device", "short": None}] + \
                                 list(base.get("dimensions") or [])
            hero = next((p["measure"] for p in base.get("panels", []) if p.get("type") == "hero"), None)
            if hero:
                base["panels"] = list(base["panels"]) + [{
                    "type": "category", "title": "By device", "dimension": "_dev",
                    "measure": hero, "note": "Every device writing to this folder.",
                    "limit": 12, "extras": [],
                }]
        merged.append(base)
    # Keep the config's source order; anything only present in a snapshot follows.
    rank = {name: i for i, name in enumerate(order)}
    merged.sort(key=lambda r: (rank.get(r["id"], len(rank)), r.get("title", "")))
    return merged


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

    save_snapshot(config, reports)
    snapshots, devices = load_snapshots(config, warn)
    order = [e.get("module") for e in config["sources"] if e.get("module")]
    combined = merge_reports(snapshots, warn, order) if snapshots else reports

    return {
        "title": config["title"],
        "generated": datetime.now().astimezone().isoformat(timespec="minutes"),
        "version": __version__,
        "config_path": config.get("_path", ""),
        "device": config.get("_device", ""),
        "devices": devices,
        "reports": combined,
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
