"""Core smoke source -- proves the framework round-trips.

This is NOT a real indicator. It is a trivial loader that demonstrates the full
path a real connector follows:

    load raw -> normalize to ISO3 -> per-exposure -> anchor_scale (0-1) ->
    write data/processed/<source>.csv -> register provenance + coverage.

Real source connectors (WB, UNHCR, ILOSTAT, V-Dem, STATIC, AUX) copy this
shape: import iso_utils + standardize + register, never re-derive any of them.

Run:  python -m pipeline.sources._core_smoke
"""

from pathlib import Path
import csv

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = _REPO_ROOT / "data" / "processed" / "_core_smoke.csv"

SOURCE = "core smoke (synthetic)"
SERIES_ID = "core_smoke_demo"
LICENSE = "N/A (synthetic demo)"

# A handful of raw values keyed by mixed identifier forms, to exercise
# normalize_to_iso3 (ISO3 passthrough, name, World Bank suffix, rename).
_RAW = {
    "USA": 5.0,
    "Russian Federation": 20.0,
    "Congo, Dem. Rep.": 48.0,
    "Turkiye": 12.0,
    "BGD": 33.0,
}


def load():
    """Return {iso3: raw_value} normalized to the FLSRI sample."""
    out = {}
    for ident, val in _RAW.items():
        iso3 = iso_utils.normalize_to_iso3(ident)
        if iso3:
            out[iso3] = val
    return out


def run():
    countries = iso_utils.load_sample()
    raw = load()

    spec = AnchorSpec(
        indicator="_core_smoke",
        floor=0.0, ceiling=50.0,
        direction="high_risk",
        unit="demo units per 100k",
        anchor_source="synthetic demo anchors (not a real indicator)",
    )
    result = anchor_scale(raw, spec, sample=countries)

    # write data/processed/<source>.csv  (iso3, value)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3", "_core_smoke"])
        for iso3 in countries:
            v = result.get(iso3)
            w.writerow([iso3, "" if v is None else round(v, 4)])

    # register provenance + coverage
    row = result.register_row(source=SOURCE, series_id=SERIES_ID, license=LICENSE)
    row["year_min"], row["year_max"] = 2026, 2026
    register.upsert_rows([row])

    print(f"[_core_smoke] wrote {OUT_PATH}")
    print(f"[_core_smoke] coverage {result.meta['coverage_pct']:.1f}% "
          f"({result.meta['n_present']}/{result.meta['n_total']}), "
          f"below_floor={result.meta['below_floor']}")
    print(f"[_core_smoke] flags: {result.meta['flags']}")
    return result


if __name__ == "__main__":
    run()
