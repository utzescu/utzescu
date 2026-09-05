"""Claude Code token usage, read from the JSONL transcripts on this machine.

Options (dashboard.config.json -> sources[].options):
    dir       transcript directory (default: ~/.claude/projects)
    tz        local | utc | IANA zone name (default: local)
    pricing   path to a rate table (default: pricing.json beside the config)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .. import spec

TITLE = "Token usage"
DESCRIPTION = "Claude Code API tokens and estimated spend"

DEFAULT_DIR = Path.home() / ".claude" / "projects"
DEFAULT_PRICING = Path(__file__).resolve().parents[2] / "pricing.json"


# ------------------------------------------------------------------- pricing

def _load_pricing(path):
    with Path(path).expanduser().open() as fh:
        return json.load(fh)["models"]


def _resolve_rates(model, speed, table, cache):
    key = (model, speed)
    if key in cache:
        return cache[key]
    row = table.get(model)
    if row is None:
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


def _cost(rates, input_, output, cw5m, cw1h, cread):
    if not rates:
        return 0.0
    return (input_ * rates["input"] + output * rates["output"]
            + cw5m * rates["cache_write_5m"] + cw1h * rates["cache_write_1h"]
            + cread * rates["cache_read"]) / 1_000_000


# --------------------------------------------------------------- transcripts

def _decode_project(dirname):
    return "/" + dirname.lstrip("-").replace("-", "/") if dirname.startswith("-") else dirname


def _parse_ts(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _iter_usage(paths, warn):
    """Yield one record per billed assistant message, de-duplicated."""
    seen = set()
    for path in paths:
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError as exc:
            warn(f"skipping {path}: {exc}")
            continue
        with fh:
            for line in fh:
                if '"usage"' not in line:
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
                # Streaming writes the same assistant message several times;
                # message id + request id identifies one API call.
                dedup = (msg.get("id"), rec.get("requestId"))
                if dedup in seen:
                    continue
                seen.add(dedup)
                ts = _parse_ts(rec.get("timestamp", ""))
                if ts is None:
                    continue
                creation = usage.get("cache_creation") or {}
                cw5m = int(creation.get("ephemeral_5m_input_tokens") or 0)
                cw1h = int(creation.get("ephemeral_1h_input_tokens") or 0)
                if not cw5m and not cw1h:
                    cw5m = int(usage.get("cache_creation_input_tokens") or 0)
                yield {
                    "ts": ts,
                    "model": msg.get("model") or "unknown",
                    "speed": usage.get("speed") or usage.get("service_tier") or "standard",
                    "session": rec.get("sessionId") or path.stem,
                    "project": rec.get("cwd") or _decode_project(path.parent.name),
                    "input": int(usage.get("input_tokens") or 0),
                    "output": int(usage.get("output_tokens") or 0),
                    "cread": int(usage.get("cache_read_input_tokens") or 0),
                    "cw5m": cw5m,
                    "cw1h": cw1h,
                    "thinking": int((usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0),
                }


def _tz(name):
    if name in ("local", "", None):
        return datetime.now().astimezone().tzinfo
    if str(name).lower() == "utc":
        return timezone.utc
    from zoneinfo import ZoneInfo
    return ZoneInfo(name)


# ------------------------------------------------------------------- collect

def collect(options, warn):
    directory = Path(options.get("dir") or DEFAULT_DIR).expanduser()
    if not directory.exists():
        warn(f"no transcript directory at {directory}")
        return None
    paths = sorted(directory.rglob("*.jsonl"))
    if not paths:
        warn(f"no .jsonl transcripts under {directory}")
        return None

    tz = _tz(options.get("tz", "local"))
    pricing = _load_pricing(options.get("pricing") or DEFAULT_PRICING)
    rate_cache, unpriced = {}, set()

    rs = spec.RowSet(["d", "h", "p", "s", "m"], ["n", "i", "o", "cw", "cr", "th", "c"])
    sessions = {}
    for rec in _iter_usage(paths, warn):
        local = rec["ts"].astimezone(tz)
        rates = _resolve_rates(rec["model"], rec["speed"], pricing, rate_cache)
        if rates is None:
            unpriced.add(rec["model"])
        key = (local.strftime("%Y-%m-%d"), local.hour, rec["project"], rec["session"], rec["model"])
        rs.add(key, {
            "n": 1, "i": rec["input"], "o": rec["output"],
            "cw": rec["cw5m"] + rec["cw1h"], "cr": rec["cread"], "th": rec["thinking"],
            "c": _cost(rates, rec["input"], rec["output"], rec["cw5m"], rec["cw1h"], rec["cread"]),
        })
        stamp = local.strftime("%Y-%m-%d %H:%M")
        sess = sessions.setdefault(rec["session"],
                                   {"start": stamp, "end": stamp, "project": rec["project"]})
        sess["start"] = min(sess["start"], stamp)
        sess["end"] = max(sess["end"], stamp)

    rows = rs.rows(sort_key=lambda r: (r["d"], r["h"]))
    if not rows:
        warn(f"transcripts under {directory} carried no usage records")
        return None

    notes = [
        "Token counts come from each assistant message's usage block in " + str(directory) +
        ", priced with the rates in pricing.json. If your usage is covered by a Claude "
        "subscription, this is what the same tokens would have cost on the API, not a bill.",
        "Cache writes bill at 1.25× (5-minute TTL) or 2× (1-hour TTL) the input rate; "
        "cache reads at 0.1×. Streaming duplicates in the transcripts are de-duplicated "
        "on message id + request id.",
    ]
    if unpriced:
        notes.insert(0, "No rates found for " + ", ".join(sorted(unpriced)) +
                        " — counted as $0. Add them to pricing.json.")

    return spec.report(
        id="claude_tokens",
        title=TITLE,
        subtitle=DESCRIPTION,
        rows=rows,
        date_field="d",
        hour_field="h",
        dimensions=[
            spec.dimension("m", "Model", short="prefix:claude-"),
            spec.dimension("p", "Project", short="path"),
        ],
        measures={
            "cost": spec.measure("Estimated spend", "c", format="money"),
            "tokens": spec.measure("Total tokens", ["i", "o", "cw", "cr"], format="compact"),
            "calls": spec.measure("API calls", "n"),
            "input": spec.measure("Input", "i"),
            "output": spec.measure("Output", "o", format="compact"),
            "thinking": spec.measure("Thinking", "th", format="compact"),
            "cache_write": spec.measure("Cache write", "cw", format="compact"),
            "cache_read": spec.measure("Cache read", "cr", format="compact"),
            "input_side": spec.measure("Input-side tokens", ["i", "cw", "cr"], format="compact"),
            "cache_share": spec.ratio("Cache reads", "cache_read", "input_side"),
            "tokens_per_day": spec.per_day("Tokens per active day", "tokens"),
        },
        dim_meta={"s": sessions},
        panels=[
            spec.hero("cost", label="Estimated spend",
                      meta=["{tokens:int} tokens across {calls:int} API calls"], spark="cost"),
            spec.tiles([
                spec.tile("Total tokens", "tokens", meta="{tokens:int} exact"),
                spec.tile("Output tokens", "output", meta="{thinking:compact} of it thinking"),
                spec.tile("Cache reads", "cache_share", meta="of all input-side tokens"),
                spec.tile("Active days", "@days", meta="{tokens_per_day} tokens/day"),
                spec.tile("Sessions", "@dim:s", meta="{calls:int} calls total"),
                spec.tile("Busiest period", "@peak:tokens", meta="{@peak_label:tokens}"),
            ]),
            spec.timeseries(title="Tokens over time", mode="stacked", toggle=True, height=300,
                            note="Stacked by token type",
                            series=[spec.series("i", "Input"), spec.series("o", "Output"),
                                    spec.series("cw", "Cache write"), spec.series("cr", "Cache read")]),
            spec.timeseries(title="Cost per period", mode="bars", measure="cost",
                            note="Estimated, at the rates in pricing.json."),
            spec.timeseries(title="Cumulative cost", mode="cumulative", measure="cost",
                            note="Running total across the selected range."),
            spec.category(title="By model", dimension="m", measure="tokens",
                          note="Total tokens; cost and share on hover.",
                          extras=["cost", "calls"]),
            spec.category(title="By project", dimension="p", measure="tokens",
                          note="Total tokens per working directory.",
                          extras=["cost", "calls"]),
            spec.hourly(title="Time of day", measure="tokens",
                        note="Tokens by hour, summed over the selected range."),
            spec.table(title="Daily detail", group="day", copy=True,
                       note="Every number in the charts above, in full.",
                       columns=[
                           spec.column("Date", dim=True),
                           spec.column("Calls", measure="calls"),
                           spec.column("Input", measure="input"),
                           spec.column("Output", measure="output", format="int"),
                           spec.column("Cache write", measure="cache_write", format="int"),
                           spec.column("Cache read", measure="cache_read", format="int"),
                           spec.column("Total", measure="tokens", format="int"),
                           spec.column("Cost", measure="cost"),
                       ]),
            spec.table(title="Sessions", group="dimension", dimension="s", sort="meta:start",
                       note="Newest first.",
                       columns=[
                           spec.column("Started", meta="start"),
                           spec.column("Ended", meta="end"),
                           spec.column("Project", meta="project", short="path"),
                           spec.column("Session", dim=True, short="prefix-8"),
                           spec.column("Calls", measure="calls"),
                           spec.column("Tokens", measure="tokens", format="int"),
                           spec.column("Cost", measure="cost"),
                       ]),
        ],
        notes=notes,
    )
