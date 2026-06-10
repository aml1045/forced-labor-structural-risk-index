"""ILOSTAT connector -- Layer-1 plumbing for the FLSRI indicator data layer.

Pulls each CONFIRMED series declared in config/api-config/ILOSTAT.yaml from the
ILOSTAT rplumber endpoint, normalizes country codes to ISO3, takes the most
recent non-NaN year per country, standardizes to a 0-1 per-exposure absolute-
anchored risk scale, writes data/processed/ilostat.csv (one column per series),
and records provenance + coverage to the per-source register fragment
config/data_register.d/ilostat.csv.

Follows the shape of pipeline/sources/_core_smoke.py:
    load raw -> normalize to ISO3 -> anchor_scale (0-1) ->
    write data/processed/ilostat.csv -> register provenance + coverage.

Reuses (never re-derives):
    pipeline.iso_utils    -- normalize_to_iso3, load_sample
    pipeline.standardize  -- AnchorSpec, anchor_scale (ScaleResult.register_row/.meta)
    pipeline.register     -- upsert_rows(rows, path=...)

Carried over from the prior connector + ILOSTAT.yaml. The HTTP
fetch, the REQUIRED custom User-Agent (rplumber 403s the default urllib UA),
the per-series filter step, and graceful handling of pre-aggregated ("NOC")
series that ship without sex/classif columns are all retained.

Scoring rules (docs/scoring-rules.md):
  - 0-1 per-exposure absolute-anchored scale (rule 1); anchors in ILOSTAT.yaml.
  - direction set explicitly per series (rule: higher = more risk).
  - missing = drop-and-re-average with >=50%/>=2 floor; never missing->0; flag
    below-floor (rule 9). anchor_scale leaves missing as None and flags floors.

Endpoint: https://rplumber.ilo.org/data/indicator?id=<CODE>&format=.csv
  Public, no auth, custom User-Agent REQUIRED.

Run:  python -m pipeline.sources.ilostat
      python -m pipeline.sources.ilostat --dry-run   # plan, no network calls
"""
from __future__ import annotations

import argparse
import csv
import io
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd
import yaml

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = _REPO_ROOT / "config" / "api-config" / "ILOSTAT.yaml"
OUT_PATH = _REPO_ROOT / "data" / "processed" / "ilostat.csv"
REGISTER_FRAGMENT = _REPO_ROOT / "config" / "data_register.d" / "ilostat.csv"

SOURCE = "ILOSTAT (ILO, rplumber API)"
LICENSE = "CC BY 4.0 (ILO open data)"

API_BASE = "https://rplumber.ilo.org/data/indicator"
TIMEOUT = 120
DELAY_BETWEEN_SERIES = 0.5
RETRIES = 1

# rplumber rejects the default urllib User-Agent with HTTP 403 -- a CUSTOM
# User-Agent is REQUIRED. Carried verbatim from the prior connector.
_HEADERS = {
    "User-Agent": "FLSRI-pipeline/1.0 (academic research)",
    "Accept": "text/csv,application/octet-stream,*/*",
}

# Python on macOS often can't find system root certs; use certifi's bundle.
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()

ISO3_COL = "ref_area"
TIME_COL = "time"
VALUE_COL = "obs_value"
REQUIRED_COLS = (ISO3_COL, TIME_COL, VALUE_COL)


# --- config ----------------------------------------------------------------

def _load_series() -> list[dict]:
    if not CONFIG_PATH.exists():
        sys.exit(f"Config not found: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    series = data.get("series") if isinstance(data, dict) else None
    if not isinstance(series, list) or not series:
        sys.exit(f"{CONFIG_PATH.name}: must define a non-empty `series:` list.")
    return series


def _confirmed_series(series: list[dict]) -> list[dict]:
    """Only entries with a real code AND confirmed: true are wired."""
    return [s for s in series if s.get("code") and s.get("confirmed") is True]


# --- HTTP (ported) ----------------------------------------------------------

def _series_url(code: str) -> str:
    return f"{API_BASE}?id={code}&format=.csv"


def _http_get_bytes(url: str) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CONTEXT) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_exc = e
            if attempt < RETRIES:
                time.sleep(2)
    raise RuntimeError(f"HTTP failed after {RETRIES + 1} attempts: {last_exc}")


def _apply_filters(df: pd.DataFrame, filters: dict | None) -> pd.DataFrame:
    """Restrict df to rows matching each key=value in filters.

    A None value or a dimension absent from the response (pre-aggregated "NOC"
    series ship without sex/classif columns) is silently skipped -- the data is
    already at the cut the filter would have selected. (Ported behavior.)
    """
    if not filters:
        return df
    for col, want in filters.items():
        if want is None or col not in df.columns:
            continue
        df = df[df[col].astype(str) == str(want)]
    return df


def _pull_series(code: str, filters: dict | None) -> pd.DataFrame:
    raw = _http_get_bytes(_series_url(code))
    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    missing = set(REQUIRED_COLS) - set(df.columns)
    if missing:
        raise RuntimeError(
            f"{code}: response missing required columns {sorted(missing)}. "
            f"Got: {list(df.columns)[:20]}"
        )
    return _apply_filters(df, filters)


def _most_recent_by_iso3(df: pd.DataFrame) -> tuple[dict, int | None, int | None]:
    """Return ({iso3: value} most-recent non-NaN year per country, year_min, year_max)."""
    df = df.dropna(subset=[VALUE_COL]).copy()
    df["_iso3"] = df[ISO3_COL].map(iso_utils.normalize_to_iso3)
    df = df.dropna(subset=["_iso3"])
    df[TIME_COL] = pd.to_numeric(df[TIME_COL], errors="coerce")
    df = df.dropna(subset=[TIME_COL])
    if df.empty:
        return {}, None, None
    idx = df.groupby("_iso3")[TIME_COL].idxmax()
    mr = df.loc[idx]
    out = {row["_iso3"]: float(row[VALUE_COL]) for _, row in mr.iterrows()}
    return out, int(mr[TIME_COL].min()), int(mr[TIME_COL].max())


# --- run --------------------------------------------------------------------

def run(dry_run: bool = False):
    series = _confirmed_series(_load_series())
    if not series:
        sys.exit("No confirmed series in ILOSTAT.yaml (need code + confirmed: true).")

    print("Plan -- confirmed series to pull:")
    for s in series:
        print(f"    - {s['code']:<24} used_by={s.get('used_by')}  dir={s.get('direction')}")
    if dry_run:
        print("\n--dry-run: no network calls made.")
        return None

    countries = iso_utils.load_sample()
    n_total = len(countries)

    results: dict[str, object] = {}     # code -> ScaleResult
    years: dict[str, tuple] = {}        # code -> (year_min, year_max)
    col_order: list[str] = []

    for i, s in enumerate(series):
        code = s["code"]
        print(f"[{i+1}/{len(series)}] pulling {code} ...", end=" ", flush=True)
        df = _pull_series(code, s.get("filters") or {})
        raw_by_iso3, y_min, y_max = _most_recent_by_iso3(df)
        if not raw_by_iso3:
            sys.exit(f"\n{code}: pull returned no usable rows -- ABORT (a failing pull must not be treated as success).")

        spec = AnchorSpec(
            indicator=code,
            floor=float(s["floor"]),
            ceiling=float(s["ceiling"]),
            direction=s.get("direction", "high_risk"),
            unit=s.get("unit", ""),
            anchor_source=s.get("anchor_source", ""),
        )
        res = anchor_scale(raw_by_iso3, spec, sample=countries)
        results[code] = res
        years[code] = (y_min, y_max)
        col_order.append(code)
        m = res.meta
        print(f"{len(raw_by_iso3)} countries | coverage {m['coverage_pct']:.1f}% "
              f"({m['n_present']}/{n_total})"
              + ("  [BELOW FLOOR]" if m["below_floor"] else ""))
        if i < len(series) - 1:
            time.sleep(DELAY_BETWEEN_SERIES)

    # --- write data/processed/ilostat.csv (iso3 + one column per series) ----
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3"] + col_order)
        for iso3 in countries:
            row = [iso3]
            for code in col_order:
                v = results[code].get(iso3)
                row.append("" if v is None else round(v, 4))
            w.writerow(row)
    print(f"\n[ilostat] wrote {OUT_PATH}")

    # --- register provenance + coverage to the per-source fragment ----------
    rows = []
    for code in col_order:
        res = results[code]
        row = res.register_row(source=SOURCE, series_id=code, license=LICENSE)
        y_min, y_max = years[code]
        row["year_min"], row["year_max"] = y_min, y_max
        rows.append(row)
    register.upsert_rows(rows, path=str(REGISTER_FRAGMENT))
    print(f"[ilostat] wrote {len(rows)} register rows -> {REGISTER_FRAGMENT}")

    for code in col_order:
        m = results[code].meta
        print(f"   {code:<24} dir={m['direction']:<9} anchor={m['anchor']:<40} "
              f"cov={m['coverage_pct']:.1f}%  flags={m['flags'] or '-'}")
    return results


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true",
                   help="Print the plan without network calls.")
    args = p.parse_args()
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
