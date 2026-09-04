#!/usr/bin/env python3
"""Build an HTML dashboard of Claude Code token usage over time.

Reads the JSONL transcripts Claude Code writes under ~/.claude/projects/ and
aggregates every assistant message's `usage` block into per-hour buckets keyed
by day, project, session and model. The buckets are embedded into a
self-contained HTML report (no network, no CDN) that charts usage over time and
shows historical statistics.

Usage:
    python3 claude_usage.py                       # -> usage-report.html
    python3 claude_usage.py -o ~/report.html --open
    python3 claude_usage.py --dir ~/.claude/projects --tz Europe/Bucharest
    python3 claude_usage.py --json usage.json     # also dump the aggregate
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:  # stdlib since 3.9; only needed for --tz <IANA name>
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

HERE = Path(__file__).resolve().parent
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
TEMPLATE = HERE / "report_template.html"
PRICING = HERE / "pricing.json"


# --------------------------------------------------------------------------- #
# pricing
# --------------------------------------------------------------------------- #

def load_pricing(path: Path) -> dict:
    with path.open() as fh:
        return json.load(fh)["models"]


def resolve_rates(model: str, speed: str, table: dict, cache: dict) -> dict | None:
    """Map a model id to a rate row, tolerating date suffixes and aliases."""
    key = (model, speed)
    if key in cache:
        return cache[key]

    row = table.get(model)
    if row is None:
        # strip a trailing -YYYYMMDD snapshot suffix, then try family prefixes
        stem = model.rsplit("-", 1)[0] if model.rsplit("-", 1)[-1].isdigit() else model
        row = table.get(stem)
    if row is None:
        candidates = [name for name in table if model.startswith(name)]
        if candidates:
            row = table[max(candidates, key=len)]
    if row is not None and speed == "fast" and isinstance(row.get("fast"), dict):
        row = row["fast"]

    cache[key] = row
    return row


def cost_of(rates: dict | None, tok: dict) -> float:
    if not rates:
        return 0.0
    return (
        tok["input"] * rates["input"]
        + tok["output"] * rates["output"]
        + tok["cw5m"] * rates["cache_write_5m"]
        + tok["cw1h"] * rates["cache_write_1h"]
        + tok["cread"] * rates["cache_read"]
    ) / 1_000_000


# --------------------------------------------------------------------------- #
# transcript scanning
# --------------------------------------------------------------------------- #

def decode_project(dirname: str) -> str:
    """`-home-user-utzescu` -> `/home/user/utzescu` (Claude Code's cwd encoding)."""
    return "/" + dirname.lstrip("-").replace("-", "/") if dirname.startswith("-") else dirname


def parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def iter_usage(paths, warn):
    """Yield one dict per billed assistant message, de-duplicated."""
    seen = set()
    for path in paths:
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError as exc:
            warn(f"skipping {path}: {exc}")
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line or '"usage"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                usage = msg.get("usage") or {}
                if not usage:
                    continue

                # Streaming writes the same assistant message more than once;
                # message id + request id is the stable identity of one API call.
                dedup = (msg.get("id"), rec.get("requestId"))
                if dedup in seen:
                    continue
                seen.add(dedup)

                ts = parse_ts(rec.get("timestamp", ""))
                if ts is None:
                    continue
                creation = usage.get("cache_creation") or {}
                yield {
                    "ts": ts,
                    "model": msg.get("model") or "unknown",
                    "speed": usage.get("speed") or usage.get("service_tier") or "standard",
                    "session": rec.get("sessionId") or path.stem,
                    "project": rec.get("cwd") or decode_project(path.parent.name),
                    "sidechain": bool(rec.get("isSidechain")),
                    "input": int(usage.get("input_tokens") or 0),
                    "output": int(usage.get("output_tokens") or 0),
                    "cread": int(usage.get("cache_read_input_tokens") or 0),
                    "cw5m": int(creation.get("ephemeral_5m_input_tokens") or 0),
                    "cw1h": int(creation.get("ephemeral_1h_input_tokens") or 0),
                    "cwtot": int(usage.get("cache_creation_input_tokens") or 0),
                    "thinking": int((usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0),
                }


def build(paths, tz, pricing, warn):
    buckets: dict[tuple, dict] = {}
    sessions: dict[str, dict] = {}
    unpriced: set[str] = set()
    rate_cache: dict = {}
    total_messages = 0

    for row in iter_usage(paths, warn):
        # If the two TTL fields are absent, fall back to the aggregate write count.
        cw5m, cw1h = row["cw5m"], row["cw1h"]
        if not cw5m and not cw1h and row["cwtot"]:
            cw5m = row["cwtot"]

        local = row["ts"].astimezone(tz)
        model = row["model"]
        rates = resolve_rates(model, row["speed"], pricing, rate_cache)
        if rates is None:
            unpriced.add(model)

        tok = {"input": row["input"], "output": row["output"],
               "cw5m": cw5m, "cw1h": cw1h, "cread": row["cread"]}
        cost = cost_of(rates, tok)

        key = (local.strftime("%Y-%m-%d"), local.hour, row["project"], row["session"], model)
        b = buckets.get(key)
        if b is None:
            b = buckets[key] = {
                "d": key[0], "h": key[1], "p": key[2], "s": key[3], "m": key[4],
                "n": 0, "i": 0, "o": 0, "cw": 0, "cr": 0, "th": 0, "c": 0.0, "sc": 0,
            }
        b["n"] += 1
        b["i"] += row["input"]
        b["o"] += row["output"]
        b["cw"] += cw5m + cw1h
        b["cr"] += row["cread"]
        b["th"] += row["thinking"]
        b["c"] += cost
        b["sc"] += 1 if row["sidechain"] else 0
        total_messages += 1

        sess = sessions.get(row["session"])
        stamp = local.isoformat(timespec="seconds")
        if sess is None:
            sessions[row["session"]] = {"id": row["session"], "p": row["project"],
                                        "start": stamp, "end": stamp}
        else:
            sess["start"] = min(sess["start"], stamp)
            sess["end"] = max(sess["end"], stamp)

    rows = sorted(buckets.values(), key=lambda b: (b["d"], b["h"]))
    for b in rows:
        b["c"] = round(b["c"], 6)

    return {
        "generated": datetime.now(tz).isoformat(timespec="seconds"),
        "tz": str(tz),
        "messages": total_messages,
        "unpriced": sorted(unpriced),
        "buckets": rows,
        "sessions": sorted(sessions.values(), key=lambda s: s["start"]),
        "projects": sorted({b["p"] for b in rows}),
        "models": sorted({b["m"] for b in rows}),
    }


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def resolve_tz(name: str):
    if name in ("local", ""):
        return datetime.now().astimezone().tzinfo
    if name.lower() == "utc":
        return timezone.utc
    if ZoneInfo is None:
        raise SystemExit("zoneinfo unavailable; use --tz local or --tz utc")
    return ZoneInfo(name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_PROJECTS_DIR,
                    help=f"transcript directory (default: {DEFAULT_PROJECTS_DIR})")
    ap.add_argument("-o", "--out", type=Path, default=Path("usage-report.html"),
                    help="HTML report to write (default: usage-report.html)")
    ap.add_argument("--json", type=Path, help="also write the aggregate as JSON")
    ap.add_argument("--tz", default="local", help="local | utc | IANA name (default: local)")
    ap.add_argument("--pricing", type=Path, default=PRICING, help="rate table to use")
    ap.add_argument("--template", type=Path, default=TEMPLATE, help="HTML template to fill")
    ap.add_argument("--open", action="store_true", help="open the report when done")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    def warn(msg):
        if not args.quiet:
            print(f"warning: {msg}", file=sys.stderr)

    if not args.dir.exists():
        raise SystemExit(f"no transcript directory at {args.dir}")
    paths = sorted(args.dir.rglob("*.jsonl"))
    if not paths:
        raise SystemExit(f"no .jsonl transcripts under {args.dir}")

    data = build(paths, resolve_tz(args.tz), load_pricing(args.pricing), warn)
    data["source"] = str(args.dir)
    data["files"] = len(paths)

    if not data["buckets"]:
        raise SystemExit("transcripts contained no assistant usage records")
    if data["unpriced"]:
        warn("no rates for: " + ", ".join(data["unpriced"]) + " (counted as $0; add them to pricing.json)")

    template = args.template.read_text(encoding="utf-8")
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    report = template.replace("/*__USAGE_DATA__*/null", payload)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")

    if args.json:
        args.json.write_text(json.dumps(data, indent=2), encoding="utf-8")

    if not args.quiet:
        days = len({b["d"] for b in data["buckets"]})
        tokens = sum(b["i"] + b["o"] + b["cw"] + b["cr"] for b in data["buckets"])
        cost = sum(b["c"] for b in data["buckets"])
        print(f"{data['messages']:,} API calls · {tokens:,} tokens · ${cost:,.2f} "
              f"· {days} day(s) · {len(data['sessions'])} session(s)")
        print(f"wrote {args.out}")
    if args.open:
        webbrowser.open(args.out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
