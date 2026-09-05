"""Data sources. Each module exposes:

    TITLE, DESCRIPTION           - shown in the tab and the sources list
    collect(options, warn)       - returns a report spec (see dashkit.spec) or None

Dropping a new module in this directory makes it available to the config file;
nothing else needs to change.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path


def available():
    """[(module_name, title, description)] for every source module present."""
    out = []
    for info in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{info.name}")
        except Exception:  # a broken source must not hide the working ones
            continue
        out.append((info.name, getattr(mod, "TITLE", info.name),
                    getattr(mod, "DESCRIPTION", "")))
    return sorted(out)


def load(name):
    return importlib.import_module(f"{__name__}.{name}")
