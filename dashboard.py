#!/usr/bin/env python3
"""Build the dashboard.

    python3 dashboard.py                 # build to the path in dashboard.config.json
    python3 dashboard.py --open          # build and open it
    python3 dashboard.py --only claude_tokens
    python3 dashboard.py --list          # what data sources exist
    python3 dashboard.py --json out.json # also dump the collected data
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dashkit import build as builder, sources  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", type=Path, help="config file (default: dashboard.config.json)")
    ap.add_argument("-o", "--out", type=Path, help="override the output path")
    ap.add_argument("--only", nargs="+", metavar="MODULE", help="build just these sources")
    ap.add_argument("--json", type=Path, help="also write the collected data as JSON")
    ap.add_argument("--list", action="store_true", help="list available data sources and exit")
    ap.add_argument("--open", action="store_true", help="open the dashboard when it is written")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.list:
        for name, title, desc in sources.available():
            print(f"{name:16} {title:16} {desc}")
        return 0

    def warn(msg):
        if not args.quiet:
            print(f"warning: {msg}", file=sys.stderr)

    config = builder.load_config(args.config)
    data = builder.build(config, only=args.only, warn=warn)
    if not data["reports"]:
        print("no data collected — nothing written. Check dashboard.config.json.", file=sys.stderr)
        for problem in data["problems"]:
            print("  " + problem, file=sys.stderr)
        return 1

    out = builder.render(data, args.out or config["output"])
    if args.json:
        Path(args.json).write_text(json.dumps(data, indent=2), encoding="utf-8")
    if not args.quiet:
        print(builder.summarize(data))
        print(f"wrote {out}")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
