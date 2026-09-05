# Dashboard

One locally built HTML dashboard with a tab per data source. Token usage from
Claude Code is the first source; git activity is the second; adding a third is a
single Python file.

```bash
python3 install.py       # build it, put a "Dashboard" shortcut on your desktop,
                         # and refresh it every 6 hours
```

Everything is Python 3.9+ standard library and one self-contained HTML file — no
packages to install, no network calls, no data leaving the machine.

## Install on Windows, with the files in Google Drive

Put the toolkit in a synced folder and every device you run it on adds to the
same dashboard:

```
python install.py --install-to "G:\My Drive\Claude_Drive\Claude\Dashboard"
```

That copies everything into `...\Claude\Dashboard`, builds
`dashboard.html` there, puts a **Dashboard** shortcut on your desktop
(`C:\Users\<you>\Desktop\Dashboard.url`), writes a `refresh.cmd` you can
double-click any time, and registers a Scheduled Task that rebuilds every 6
hours. Add `--desktop "C:\Users\radu\Desktop"` if your desktop lives
somewhere unusual (a OneDrive-redirected Desktop, for instance).

On the **next device**, once Drive has synced the folder: open it and
double-click **Install-Dashboard.cmd**. Same build, that device's own desktop
shortcut, that device's own 6-hourly task.

### What "across devices" means here

Each device reads its own local data — your Claude transcripts live in
`%USERPROFILE%\.claude\projects`, not in Drive — and writes a snapshot of what
it collected to `data\<device-name>\` inside the shared folder. Every build
merges every device's snapshot, so the dashboard shows all machines at once with
a **Device** filter, a "By device" chart, and a device list on the Sources tab.
A device that is switched off keeps contributing its last snapshot instead of
disappearing from the history.

Nothing is uploaded anywhere: Drive is only a folder to both machines. Paths in
the config are relative to the config file, and `refresh.cmd` resolves paths
from its own location, so a different drive letter on another device is fine.

## Install (any platform)

| Command | What it does |
|---|---|
| `python3 install.py` | build + desktop shortcut + 6-hourly refresh |
| `python3 install.py --install-to DIR` | copy the toolkit to DIR (Drive, a share, a stick) and install from there |
| `python3 install.py --desktop DIR` | put the shortcut somewhere other than the detected desktop |
| `python3 install.py --interval 12` | refresh every 12 hours instead |
| `python3 install.py --no-schedule` | build + shortcut only |
| `python3 install.py --status` | where the dashboard, shortcut and schedule are |
| `python3 install.py --uninstall` | remove the shortcut and the schedule (keeps the HTML) |
| `python3 dashboard.py --open` | rebuild by hand and open it |
| `python3 dashboard.py --list` | list the data sources available |

The shortcut is called **Dashboard** and lands on your desktop: a `.desktop`
link on Linux, a `.webloc` on macOS, a `.url` on Windows. The refresh is a
crontab entry on Linux (a systemd user timer where cron is absent), a launchd
agent on macOS, and a Scheduled Task on Windows. Re-running the installer
replaces the existing entry rather than stacking a second one.

The dashboard is written to `~/Dashboard/dashboard.html` by default; after
`--install-to`, it is `dashboard.html` inside that folder. Change `output` in
`dashboard.config.json` to put it elsewhere — a relative path is taken relative
to the config file.

## Configuration

`dashboard.config.json` lists the sources to build and their options:

```json
{
  "title": "Dashboard",
  "output": "dashboard.html",
  "data_dir": "data",
  "device": null,
  "sources": [
    {"module": "claude_tokens", "enabled": true,
     "options": {"dir": "~/.claude/projects", "tz": "local"}},
    {"module": "git_activity", "enabled": true,
     "options": {"repos": ["~/code"], "since_days": 365}}
  ]
}
```

`device` names this machine in the Device filter (default: its hostname), and
`data_dir` is where per-device snapshots are kept — set it to `null` to keep the
dashboard single-machine. Each enabled source becomes a tab. A source that finds nothing is skipped with a
warning shown on the dashboard's **Sources** tab, so one broken source never
takes the dashboard down with it.

## Sources that ship

**Token usage** (`claude_tokens`) — Claude Code API tokens and estimated spend,
from the JSONL transcripts under `~/.claude/projects/`. Estimated spend as the
headline, then tokens over time stacked by type (input / output / cache write /
cache read — click a series to hide it), cost per period, cumulative cost,
totals by model and project, time of day, and full daily and session tables.

Counting: every `assistant` record carries the API's `usage` block. Records are
de-duplicated on `message.id` + `requestId`, because streaming writes the same
assistant message to the transcript several times and counting those twice
inflates every number. Cache writes are split by 5-minute and 1-hour TTL where
the transcript records it.

Costs are estimates from `pricing.json` — Anthropic first-party rates in USD per
million tokens, cached 2026-06-24. Cache writes bill at 1.25× (5m TTL) or 2×
(1h TTL) the input rate, cache reads at 0.1× (0.025× on Claude Fable 5.1). Edit
that file to change a rate or add a model; unknown models count as $0 and are
named on the dashboard. If your usage is covered by a subscription, the figure
is what the same tokens would have cost on the API, not a bill.

**Git activity** (`git_activity`) — commits, insertions and deletions across
local repositories, by repo, author, day and hour. Point `repos` at individual
repositories or at a directory containing them (`depth` controls how deep the
search goes). Disabled by default.

## Adding a data source

A source is one file in `dashkit/sources/` exposing `collect(options, warn)`
that returns rows plus a description of the panels to draw from them. The
renderer knows nothing about tokens or commits — only dimensions, measures and
panel types — so you never write chart code:

```python
from .. import spec

TITLE = "Downloads"
DESCRIPTION = "Files landing in ~/Downloads"

def collect(options, warn):
    rows = [{"d": "2026-09-04", "h": 14, "kind": "pdf", "n": 3, "bytes": 91234}, ...]
    return spec.report(
        id="downloads", title=TITLE, rows=rows, date_field="d", hour_field="h",
        dimensions=[spec.dimension("kind", "Type")],
        measures={"files": spec.measure("Files", "n"),
                  "bytes": spec.measure("Bytes", "bytes", format="compact")},
        panels=[
            spec.hero("files", label="Files saved", meta=["{bytes} on disk"]),
            spec.timeseries(title="Over time", mode="bars", measure="files"),
            spec.category(title="By type", dimension="kind", measure="files"),
        ],
    )
```

Drop the file in, add `{"module": "downloads", "enabled": true}` to the config,
rebuild. It gets its own tab, the date-range and dimension filters, hover
tooltips, light and dark themes, and the table view for free.

The pieces:

- **rows** — a flat table. Every row needs `date_field` (`YYYY-MM-DD`);
  `hour_field` (0–23) unlocks the time-of-day panel.
- **dimensions** — the columns you want to filter and group by.
- **measures** — named sums over row fields (`spec.measure`), or derived values
  (`spec.ratio`, `spec.per_day`). Formats: `int`, `compact`, `money`, `percent`.
- **panels** — `hero`, `tiles`, `timeseries` (`stacked` / `bars` / `cumulative`),
  `category`, `hourly`, `table`, in the order you want them laid out.
- Context measures the renderer computes itself: `@days` (active days),
  `@dim:<field>` (distinct values), `@peak:<measure>` and `@peak_label:<measure>`.

## Files

| Path | Purpose |
|---|---|
| `dashboard.py` | build the dashboard |
| `install.py` | build + desktop shortcut + refresh schedule |
| `dashboard.config.json` | which sources run, with what options, and where the HTML goes |
| `Install-Dashboard.cmd` | double-click setup for a Windows device, from inside the shared folder |
| `refresh.cmd` / `refresh.sh` | written at install time; rebuilds on demand and on schedule |
| `data/<device>/` | each device's snapshot of what it collected |
| `dashkit/spec.py` | the builders a source uses to describe its report |
| `dashkit/build.py` | runs the sources and fills the template |
| `dashkit/sources/` | the data sources |
| `dashkit/shell_template.html` | tabs, charts, filters, tables — the whole GUI |
| `pricing.json` | per-model token rates, editable |
