"""Data-register helpers: append provenance + coverage rows to
config/data_register.csv on a fixed schema.

Schema (fixed column order -- source connectors must not reorder):
    indicator, source, series_id, countries, year_min, year_max,
    license, direction, anchor, coverage_pct, flags

Each connector module (pipeline/sources/*.py), after standardizing, calls
`upsert_rows(rows)` with one row per indicator it produced. `upsert_rows`
replaces any existing rows for the same (indicator, source) pair so re-runs
are idempotent and don't duplicate.

Build a row most easily from a standardize.ScaleResult:

    from pipeline.standardize import anchor_scale
    from pipeline import register
    res = anchor_scale(raw, spec, sample=countries)
    row = res.register_row(source="World Bank WDI", series_id="SI.POV.GINI",
                           license="CC BY 4.0")
    row["year_min"], row["year_max"] = 2010, 2024   # set the years you pulled
    register.upsert_rows([row])
"""

from pathlib import Path
import csv

_REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTER_PATH = _REPO_ROOT / "config" / "data_register.csv"

COLUMNS = [
    "indicator", "source", "series_id", "countries", "year_min", "year_max",
    "license", "direction", "anchor", "coverage_pct", "flags",
]


def ensure_header(path=None):
    """Create config/data_register.csv with the header if it does not exist."""
    p = Path(path) if path else REGISTER_PATH
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=COLUMNS).writeheader()
    return p


def read_rows(path=None):
    p = ensure_header(path)
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def upsert_rows(new_rows, path=None):
    """Insert/replace rows keyed by (indicator, source). Idempotent on re-run."""
    p = ensure_header(path)
    existing = read_rows(p)
    keys = {(r["indicator"], r["source"]) for r in new_rows}
    kept = [r for r in existing if (r["indicator"], r["source"]) not in keys]
    out = kept + [{c: r.get(c, "") for c in COLUMNS} for r in new_rows]
    out.sort(key=lambda r: (str(r["indicator"]), str(r["source"])))
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out)
    return p
