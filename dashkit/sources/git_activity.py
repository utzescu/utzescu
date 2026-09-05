"""Git commit activity across local repositories - a second, worked example of a
data source, and a demonstration that a source only has to produce rows.

Options (dashboard.config.json -> sources[].options):
    repos       list of paths; a path may be a repo or a directory of repos
    depth       how deep to look for repos under each path (default: 2)
    since_days  history window to read (default: 365)
    mine_only   count only commits whose author email matches git config (default: false)
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .. import spec

TITLE = "Git activity"
DESCRIPTION = "Commits, insertions and deletions across your repositories"

SEP = "\x1f"


def _run(args, cwd=None):
    try:
        out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _find_repos(paths, depth, warn):
    found = []
    for raw in paths:
        base = Path(raw).expanduser()
        if not base.exists():
            warn(f"git_activity: no such path {base}")
            continue
        if (base / ".git").exists():
            found.append(base)
            continue
        for level in range(1, max(1, depth) + 1):
            for child in sorted(base.glob("/".join(["*"] * level))):
                if (child / ".git").exists():
                    found.append(child)
    return sorted(set(found))


def _log(repo, since, mine_only):
    args = ["git", "log", f"--since={since}", "--no-merges", "--numstat",
            "--pretty=format:%x1fC%x1f%aI%x1f%aE%x1f%aN"]
    if mine_only:
        email = (_run(["git", "config", "user.email"], cwd=repo) or "").strip()
        if email:
            args.insert(2, f"--author={email}")
    return _run(args, cwd=repo)


def collect(options, warn):
    repos = _find_repos(options.get("repos") or [], int(options.get("depth", 2)), warn)
    if not repos:
        warn("git_activity: no repositories configured or found (set sources[].options.repos)")
        return None

    since_days = int(options.get("since_days", 365))
    since = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d")
    mine_only = bool(options.get("mine_only", False))

    rs = spec.RowSet(["d", "h", "r", "a"], ["commits", "ins", "del", "files"])
    seen_any = False
    for repo in repos:
        text = _log(repo, since, mine_only)
        if text is None:
            warn(f"git_activity: could not read {repo}")
            continue
        name, date, author = repo.name, None, None
        for line in text.splitlines():
            if line.startswith(SEP + "C" + SEP):
                fields = line.split(SEP)          # ['', 'C', iso date, email, author]
                if len(fields) < 5:
                    date = None
                    continue
                author = fields[4] or fields[3]
                try:
                    when = datetime.fromisoformat(fields[2])
                except ValueError:
                    date = None
                    continue
                date = (when.strftime("%Y-%m-%d"), when.hour)
                rs.add((date[0], date[1], name, author), {"commits": 1})
                seen_any = True
            elif date and line.strip():
                parts = line.split("\t")
                if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                    rs.add((date[0], date[1], name, author),
                           {"ins": int(parts[0]), "del": int(parts[1]), "files": 1})

    rows = rs.rows(sort_key=lambda r: (r["d"], r["h"]))
    if not seen_any or not rows:
        warn(f"git_activity: no commits in the last {since_days} days across {len(repos)} repo(s)")
        return None

    return spec.report(
        id="git_activity",
        title=TITLE,
        subtitle=f"{len(repos)} repositories · last {since_days} days",
        rows=rows,
        date_field="d",
        hour_field="h",
        dimensions=[spec.dimension("r", "Repository"), spec.dimension("a", "Author")],
        measures={
            "commits": spec.measure("Commits", "commits"),
            "ins": spec.measure("Lines added", "ins", format="compact"),
            "dele": spec.measure("Lines removed", "del", format="compact"),
            "churn": spec.measure("Lines changed", ["ins", "del"], format="compact"),
            "files": spec.measure("Files touched", "files"),
            "commits_per_day": spec.per_day("Commits per active day", "commits", format="int"),
        },
        panels=[
            spec.hero("commits", label="Commits", meta=["{churn:int} lines changed across {files:int} file edits"],
                      spark="commits"),
            spec.tiles([
                spec.tile("Lines added", "ins", meta="{ins:int} exact"),
                spec.tile("Lines removed", "dele", meta="{dele:int} exact"),
                spec.tile("Active days", "@days", meta="{commits_per_day} commits/day"),
                spec.tile("Repositories", "@dim:r", meta="with commits in range"),
                spec.tile("Authors", "@dim:a", meta="distinct commit authors"),
                spec.tile("Busiest period", "@peak:commits", meta="{@peak_label:commits}"),
            ]),
            spec.timeseries(title="Commits over time", mode="bars", measure="commits",
                            note="One column per period."),
            spec.timeseries(title="Lines changed", mode="stacked", toggle=True,
                            note="Insertions and deletions",
                            series=[spec.series("ins", "Added"), spec.series("del", "Removed")]),
            spec.category(title="By repository", dimension="r", measure="commits",
                          note="Commits; churn and share on hover.",
                          extras=["ins", "dele"]),
            spec.category(title="By author", dimension="a", measure="commits",
                          note="Commits per author.", extras=["churn"]),
            spec.hourly(title="Time of day", measure="commits",
                        note="When commits land, by hour."),
            spec.table(title="Daily detail", group="day", copy=True, note="",
                       columns=[
                           spec.column("Date", dim=True),
                           spec.column("Commits", measure="commits"),
                           spec.column("Added", measure="ins", format="int"),
                           spec.column("Removed", measure="dele", format="int"),
                           spec.column("Files", measure="files"),
                       ]),
        ],
        notes=["Merge commits are excluded. Insertions and deletions come from "
               "`git log --numstat`, so binary files count as zero."],
    )
