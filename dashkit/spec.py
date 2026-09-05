"""Builders for the declarative report spec the dashboard renderer consumes.

A source module returns one report: a flat table of `rows` plus a description of
how to aggregate them. The browser-side renderer knows nothing about tokens, git
or anything else - it only knows dimensions, measures and panel types - so a new
data source is pure Python: collect rows, name the measures, list the panels.

Row fields are short keys. Every row must carry the report's `date_field`
("YYYY-MM-DD"); `hour_field` (0-23) is optional and enables the hourly panel.
"""

from __future__ import annotations


def report(*, id, title, rows, date_field="d", hour_field=None, subtitle="",
           dimensions=(), measures=None, panels=(), dim_meta=None, notes=()):
    return {
        "id": id,
        "title": title,
        "subtitle": subtitle,
        "date_field": date_field,
        "hour_field": hour_field,
        "dimensions": list(dimensions),
        "measures": measures or {},
        "panels": list(panels),
        "dim_meta": dim_meta or {},
        "notes": list(notes),
        "rows": rows,
    }


def dimension(field, label, *, short=None):
    """A filterable column. `short` trims long values: 'path' | 'prefix:<s>'."""
    return {"field": field, "label": label, "short": short}


def measure(label, fields, *, format="int"):
    """A sum over one or more row fields."""
    return {"label": label, "fields": [fields] if isinstance(fields, str) else list(fields),
            "format": format}


def ratio(label, num, den, *, format="percent"):
    """A measure derived from two others, e.g. cache reads / all input tokens."""
    return {"label": label, "expr": "ratio", "num": num, "den": den, "format": format}


def per_day(label, of, *, format="compact"):
    return {"label": label, "expr": "per_day", "of": of, "format": format}


# --------------------------------------------------------------------- panels

def hero(measure, *, label, meta=(), spark=None):
    return {"type": "hero", "measure": measure, "label": label,
            "meta": list(meta), "spark": spark or measure}


def tiles(items):
    return {"type": "tiles", "tiles": list(items)}


def tile(label, measure, *, meta=""):
    return {"label": label, "measure": measure, "meta": meta}


def timeseries(*, title, mode="stacked", series=(), measure=None, note="",
               toggle=False, height=None):
    """mode: stacked (multi-series columns) | bars | cumulative (area+line)."""
    return {"type": "timeseries", "title": title, "mode": mode,
            "series": list(series), "measure": measure, "note": note,
            "toggle": toggle, "height": height}


def series(field, label):
    return {"field": field, "label": label}


def category(*, title, dimension, measure, note="", limit=8, extras=()):
    """`extras` are extra measure names shown in the hover tooltip."""
    return {"type": "category", "title": title, "dimension": dimension,
            "measure": measure, "note": note, "limit": limit, "extras": list(extras)}


def hourly(*, title, measure, note=""):
    return {"type": "hourly", "title": title, "measure": measure, "note": note}


def table(*, title, group, columns, note="", dimension=None, sort="desc", limit=500,
          copy=False):
    """group: 'day' | 'dimension'. Columns are built with `column`."""
    return {"type": "table", "title": title, "group": group, "dimension": dimension,
            "columns": list(columns), "note": note, "sort": sort, "limit": limit,
            "copy": copy}


def column(label, *, measure=None, kind=None, meta=None, short=None, dim=False,
           format=None):
    """kind: 'key' (the group key) or None; `meta` reads dim_meta[<dimension>].

    `format` overrides the measure's own format - a table usually wants exact
    integers where a stat tile wants 4.5M.
    """
    return {"label": label, "measure": measure, "kind": kind or ("key" if dim else "value"),
            "meta": meta, "short": short, "format": format}


# ----------------------------------------------------------------- row helper

class RowSet:
    """Accumulates rows keyed by a tuple, summing numeric fields."""

    def __init__(self, key_fields, sum_fields):
        self.key_fields = list(key_fields)
        self.sum_fields = list(sum_fields)
        self._rows = {}

    def add(self, key, values):
        row = self._rows.get(key)
        if row is None:
            row = self._rows[key] = dict(zip(self.key_fields, key))
            for f in self.sum_fields:
                row[f] = 0
        for f, v in values.items():
            row[f] = row.get(f, 0) + v

    def rows(self, sort_key=None):
        out = list(self._rows.values())
        out.sort(key=sort_key or (lambda r: tuple(r[f] for f in self.key_fields)))
        for r in out:
            for f in self.sum_fields:
                if isinstance(r[f], float):
                    r[f] = round(r[f], 6)
        return out
