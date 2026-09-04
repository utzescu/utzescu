# Claude Code token usage dashboard

A single command turns the JSONL transcripts Claude Code writes under
`~/.claude/projects/` into a self-contained HTML dashboard: tokens over time,
estimated cost, and historical statistics per day, model, project and session.

```bash
python3 claude_usage.py --open
```

That writes `usage-report.html` in the current directory and opens it. No
dependencies (Python 3.9+ standard library only), no network calls — the report
embeds its own data and charts, so it works offline and can be mailed or
archived as a single file.

## What the report shows

- **Estimated spend** as the headline figure, with a sparkline of daily cost.
- **Tokens over time** — stacked columns of input / output / cache write /
  cache read. Cache reads normally dwarf everything else, so click a series in
  the legend to hide it. Buckets switch from days to weeks to months
  automatically as the range grows.
- **Cost per period** and **cumulative cost** (two charts, one scale each).
- **By model** and **by project** totals, **time-of-day** distribution.
- **Daily detail** and **sessions** tables — every number in the charts, in
  full, plus a "Copy TSV" button for pasting into a spreadsheet.
- Filters for date range, model and project scope the whole page at once.
  Light/dark follows the OS and has a manual toggle.

## Options

```
--dir PATH        transcript directory (default: ~/.claude/projects)
-o, --out PATH    HTML file to write (default: usage-report.html)
--json PATH       also dump the aggregate as JSON
--tz NAME         local | utc | IANA zone, e.g. Europe/Bucharest (default: local)
--pricing PATH    rate table to use (default: pricing.json)
--template PATH   HTML template to fill (default: report_template.html)
--open            open the report when it is written
-q, --quiet       no stdout summary
```

Regenerate whenever you want a fresh view; add it to a cron job or a shell
alias if you want it kept up to date.

## How usage is counted

Every `assistant` record in a transcript carries a `usage` block from the API
response. The script reads `input_tokens`, `output_tokens`,
`cache_creation_input_tokens` (split by 5-minute / 1-hour TTL when present),
`cache_read_input_tokens` and the thinking-token detail, and de-duplicates on
`message.id` + `requestId` — streaming writes the same assistant message to the
transcript more than once, and counting those twice would inflate every number.

Records are bucketed by local day, hour, project (`cwd`), session and model.
Subagent (sidechain) calls are included: they cost tokens too.

## Costs are estimates

Prices come from `pricing.json` — Anthropic first-party API rates in USD per
million tokens, cached 2026-06-24. Cache writes bill at 1.25× the input rate on
the 5-minute TTL and 2× on the 1-hour TTL; cache reads at 0.1× (0.025× on
Claude Fable 5.1). Edit the file to change rates or add a model; unknown models
are counted as $0 and named in a warning and in the report footer.

If your Claude Code usage is covered by a subscription, the figure is what the
same tokens would have cost on the API — not a bill.

## Files

| File | Purpose |
|---|---|
| `claude_usage.py` | scans transcripts, aggregates, fills the template |
| `report_template.html` | the dashboard: styles, charts, interaction |
| `pricing.json` | per-model rates, editable |

The template is filled by replacing the single `/*__USAGE_DATA__*/null` token
with the aggregate JSON, so you can restyle the report without touching the
Python, and open the template directly to see its structure.
