"""UNHCR Population Statistics connector (Layer-1 DATA plumbing).

Carried over from the prior connector + `UNHCR.yaml`. Reuses the
repo core (iso_utils, standardize, register) -- nothing re-derived here.

Five displacement series (2010-2025), each aggregated per-country along ONE
axis of UNHCR's (origin x asylum x year) cube:

    unhcr_refugees_by_coa        refugees hosted, by country of asylum
    unhcr_refugees_by_coo        refugees originating, by country of origin
    unhcr_idps_by_country        internally displaced persons
    unhcr_asylum_seekers_by_coa  asylum-seekers hosted, by country of asylum
    unhcr_stateless_by_country   stateless persons

Each series is standardized to a 0-1 risk score keyed by ISO3 and written as one
column of data/processed/unhcr.csv. Provenance + coverage go to the per-source
register fragment config/data_register.d/unhcr.csv.

---------------------------------------------------------------------------
DATA PATH (matches _core_smoke.py shape):
    load raw long table -> aggregate to {iso3: stock} (most-recent year) ->
    per-exposure base -> standardize 0-1 -> write CSV -> register.

SOURCE OF RAW DATA (two modes):
  - DEFAULT: read the fresh on-disk cache (unhcr_cache.csv), the long-format
    table the prior connector produced (iso3, country_name, year,
    series, value). This is the on-disk fresh pull (vintage 2026-05-28).
  - --refresh: re-pull live from the UNHCR API, keeping the year-by-year loop
    workaround (the server-side year-range param is broken) and the coa_all /
    coo_all per-country aggregation flags. No auth required.

PER-EXPOSURE BASE + DIRECTION (per docs/scoring-rules.md, Rule 1):
  Displacement stocks are raw head-counts spanning many orders of magnitude;
  a raw count makes every large country look maximal. The correct per-exposure
  base is PER-CAPITA (stock / population). Population (WB SP.POP.TOTL) is NOT an
  owned input of this connector and is not present as a usable raw denominator
  in data/processed/ (the processed worldbank table holds standardized 0-1
  scores, not raw population). So:
    - IF a raw population file is provided at
      data/processed/unhcr_population.csv  (columns: iso3, population)
      OR data/aux/worldbank_population.csv, the connector computes per-capita
      stock (per 100k) and anchors it absolutely (Rule 1 preferred path).
    - ELSE it FALLS BACK to a winsorized relative scale on the raw stock
      (Rule 1's justified no-absolute-anchor fallback) AND raises an explicit
      POPULATION-DEPENDENCY flag in the register so reviewers know the
      per-exposure base is missing.
  Direction for all five series is HIGH_RISK: a larger displaced/stateless
  population is a larger structural-exposure signal (higher = more risk). This
  is a plumbing default for the data layer; the eventual indicator mapping
  (e.g. hosting burden vs. origin pressure) may re-point a series and is a
  mapping-layer call, not decided here.

Run:
    python -m pipeline.sources.unhcr              # build from on-disk cache
    python -m pipeline.sources.unhcr --refresh    # re-pull live from the API
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import math
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale, relative_scale

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = _REPO_ROOT / "data" / "processed" / "unhcr.csv"
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "unhcr.csv"
CONFIG_PATH = _REPO_ROOT / "config" / "api-config" / "UNHCR.yaml"

# Fresh on-disk cache (long-format: iso3, country_name, year, series, value),
# staged in-repo.
_CACHE_PATH = _REPO_ROOT / "data" / "raw" / "unhcr_cache.csv"

# Optional raw-population denominator (NOT owned by this connector). If present
# with columns (iso3, population), enables the preferred per-capita anchor path.
_POP_CANDIDATES = [
    _REPO_ROOT / "data" / "processed" / "unhcr_population.csv",
    _REPO_ROOT / "data" / "aux" / "worldbank_population.csv",
]

SOURCE = "UNHCR Population Statistics API"
LICENSE = "Open / CC BY (attribution required)"
YEAR_MIN, YEAR_MAX = 2010, 2025

# Series spec: code -> (human name, aggregate axis, field, direction)
SERIES = [
    ("unhcr_refugees_by_coa",       "Refugees by country of asylum",  "coa_iso", "refugees",       "high_risk"),
    ("unhcr_refugees_by_coo",       "Refugees by country of origin",  "coo_iso", "refugees",       "high_risk"),
    ("unhcr_idps_by_country",       "Internally displaced persons",   "coa_iso", "idps",           "high_risk"),
    ("unhcr_asylum_seekers_by_coa", "Asylum-seekers by asylum country","coa_iso", "asylum_seekers", "high_risk"),
    ("unhcr_stateless_by_country",  "Stateless persons",              "coa_iso", "stateless",      "high_risk"),
]

# --- live API (re-pull mode) -----------------------------------------------

_API_BASE = "https://api.unhcr.org/population/v1"
_PER_PAGE = 1000
_TIMEOUT = 60
try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL = ssl.create_default_context()


def _http_get_json(url: str) -> dict:
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "FLSRI-pipeline/1.0 (academic research)",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            if attempt == 0:
                time.sleep(2)
            else:
                raise RuntimeError(f"UNHCR HTTP failed: {e}")
    raise RuntimeError("unreachable")


def _pull_series_live(aggregate_by: str, field: str) -> list[dict]:
    """Year-by-year per-country pull (server-side year-range is broken)."""
    all_flag = "coa_all" if aggregate_by == "coa_iso" else "coo_all"
    name_key = "coa_name" if aggregate_by == "coa_iso" else "coo_name"
    rows: list[dict] = []
    for year in range(YEAR_MIN, YEAR_MAX + 1):
        page = 1
        while True:
            params = {"page": page, "limit": _PER_PAGE, "year": year, all_flag: "true"}
            qs = urllib.parse.urlencode(params)
            body = _http_get_json(f"{_API_BASE}/population/?{qs}")
            items = body.get("items") or []
            for item in items:
                iso3 = (item.get(aggregate_by) or "").strip()
                if not iso3 or iso3 == "-":   # handle '-' / blank ISO fields
                    continue
                val = item.get(field)
                try:
                    val = float(val) if val not in (None, "") else 0.0
                except (TypeError, ValueError):
                    val = 0.0
                rows.append({"iso3": iso3, "country_name": (item.get(name_key) or "").strip(),
                             "year": year, "series_field": field, "value": val,
                             "aggregate_by": aggregate_by})
            if page >= int(body.get("maxPages") or 1):
                break
            page += 1
    return rows


# --- raw loading -----------------------------------------------------------

def _load_cache_rows() -> list[dict]:
    """Read the prior long-format cache. Returns list of dict rows."""
    if not _CACHE_PATH.exists():
        sys.exit(f"[unhcr] cache not found: {_CACHE_PATH}\n"
                 f"        run with --refresh to pull live from the UNHCR API.")
    out = []
    with open(_CACHE_PATH, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.append(r)
    return out


def _aggregate_latest(rows: list[dict], code: str) -> tuple[dict, int, int]:
    """From cache rows (iso3,country_name,year,series,value) for one series code,
    take the MOST-RECENT year's stock per ISO3 (normalized to FLSRI ISO3).

    Handles '-'/blank ISO3 (skipped). Returns ({iso3: stock}, year_min, year_max).
    """
    by_iso: dict[str, tuple[int, float]] = {}  # iso3 -> (year, value)
    ymin, ymax = None, None
    for r in rows:
        if r.get("series") != code:
            continue
        raw_iso = (r.get("iso3") or "").strip()
        if not raw_iso or raw_iso == "-":
            continue
        iso3 = iso_utils.normalize_to_iso3(raw_iso)
        if not iso3:
            continue
        try:
            year = int(r.get("year"))
            val = float(r.get("value"))
        except (TypeError, ValueError):
            continue
        ymin = year if ymin is None else min(ymin, year)
        ymax = year if ymax is None else max(ymax, year)
        prev = by_iso.get(iso3)
        if prev is None or year > prev[0]:
            by_iso[iso3] = (year, val)
    return {iso: v for iso, (yr, v) in by_iso.items()}, ymin, ymax


def _aggregate_latest_live(rows: list[dict]) -> tuple[dict, int, int]:
    """Same most-recent-year aggregation for live-pulled rows."""
    by_iso: dict[str, tuple[int, float]] = {}
    ymin, ymax = None, None
    for r in rows:
        iso3 = iso_utils.normalize_to_iso3(r["iso3"])
        if not iso3:
            continue
        year, val = int(r["year"]), float(r["value"])
        ymin = year if ymin is None else min(ymin, year)
        ymax = year if ymax is None else max(ymax, year)
        prev = by_iso.get(iso3)
        if prev is None or year > prev[0]:
            by_iso[iso3] = (year, val)
    return {iso: v for iso, (yr, v) in by_iso.items()}, ymin, ymax


# --- population denominator (optional) -------------------------------------

def _load_population() -> dict | None:
    """Return {iso3: population} if a raw-population file is on disk, else None."""
    for p in _POP_CANDIDATES:
        if p.exists():
            pop = {}
            with open(p, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    iso3 = iso_utils.normalize_to_iso3(r.get("iso3") or r.get("ISO3") or "")
                    try:
                        val = float(r.get("population") or r.get("value"))
                    except (TypeError, ValueError):
                        continue
                    if iso3 and val > 0:
                        pop[iso3] = val
            if pop:
                print(f"[unhcr] population denominator loaded from {p} ({len(pop)} countries)")
                return pop
    return None


# --- standardization -------------------------------------------------------

def _standardize_series(code, stock_by_iso, pop_by_iso, countries):
    """Return a ScaleResult for one series.

    Preferred (Rule 1): per-capita stock per 100k -> absolute anchor.
    Fallback: winsorized relative scale on raw stock + population-dependency flag.
    """
    direction = "high_risk"  # larger displaced/stateless stock = more exposure
    if pop_by_iso:
        per100k = {}
        for iso3, stock in stock_by_iso.items():
            pop = pop_by_iso.get(iso3)
            if pop and pop > 0:
                per100k[iso3] = 100000.0 * stock / pop
        # Absolute anchor: 0 = none; ceiling = 5,000 displaced/stateless per
        # 100k (5% of population) = a society-scale displacement crisis. This
        # is a plumbing anchor; the mapping layer may revise per indicator.
        spec = AnchorSpec(
            indicator=code, floor=0.0, ceiling=5000.0, direction=direction,
            unit="persons per 100k population",
            anchor_source="plumbing anchor: 0 = none; 5,000/100k (=5% of pop) "
                          "= society-scale displacement; per-exposure per Rule 1. "
                          "Mapping-layer may revise per indicator.",
        )
        return anchor_scale(per100k, spec, sample=countries), []
    # Fallback: no population denominator available.
    spec = AnchorSpec(
        indicator=code, floor=None, ceiling=None, direction=direction,
        unit="raw stock (head-count, most-recent year)",
        anchor_source="winsorized relative fallback (Rule 1) -- raw displacement "
                      "head-counts have no defensible absolute per-country anchor "
                      "and the per-capita denominator (WB SP.POP.TOTL) is unavailable.",
    )
    res = relative_scale(stock_by_iso, spec, method="winsor_minmax", sample=countries)
    flag = ("POPULATION-DEPENDENCY: per-capita base unavailable (no raw WB "
            "SP.POP.TOTL on disk); used winsorized relative fallback on raw stock. "
            "Provide data/processed/unhcr_population.csv (iso3,population) to switch "
            "to the preferred absolute per-capita anchor.")
    return res, [flag]


# --- run -------------------------------------------------------------------

def run(refresh: bool = False):
    countries = iso_utils.load_sample()
    pop_by_iso = _load_population()

    cache_rows = None if refresh else _load_cache_rows()

    results = {}          # code -> ScaleResult
    extra_flags = {}      # code -> list[str]
    year_bounds = {}      # code -> (ymin, ymax)
    refreshed_rows = {}   # code -> long rows, for the cache write-back

    for code, name, aggregate_by, field, _dir in SERIES:
        if refresh:
            print(f"[unhcr] pulling {code} live ...", flush=True)
            live = _pull_series_live(aggregate_by, field)
            stock_by_iso, ymin, ymax = _aggregate_latest_live(live)
            refreshed_rows[code] = [
                {"iso3": r["iso3"], "country_name": r["country_name"],
                 "year": r["year"], "value": r["value"]} for r in live]
        else:
            stock_by_iso, ymin, ymax = _aggregate_latest(cache_rows, code)
        if not stock_by_iso:
            sys.exit(f"[unhcr] FAILED: no rows aggregated for {code} -- aborting "
                     f"(a failing pull must not be treated as success).")
        res, flags = _standardize_series(code, stock_by_iso, pop_by_iso, countries)
        results[code] = res
        extra_flags[code] = flags
        year_bounds[code] = (ymin or YEAR_MIN, ymax or YEAR_MAX)
        print(f"[unhcr] {code}: {res.meta['n_present']}/{res.meta['n_total']} "
              f"({res.meta['coverage_pct']:.0f}%) method={res.meta['method']} "
              f"below_floor={res.meta['below_floor']}")

    # --- cache write-back: a successful refresh restores/updates the on-disk
    # cache so subsequent default (cache) runs reproduce exactly this data ----
    if refresh and refreshed_rows:
        from pipeline import raw_cache
        raw_cache.upsert_series(_CACHE_PATH, refreshed_rows,
                                source_label="unhcr (population API, 5 series)")

    # --- write data/processed/unhcr.csv (one column per series) ------------
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    codes = [c for c, *_ in SERIES]
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3"] + codes)
        for iso3 in countries:
            row = [iso3]
            for c in codes:
                v = results[c].get(iso3)
                row.append("" if v is None else round(v, 4))
            w.writerow(row)
    print(f"[unhcr] wrote {OUT_PATH}")

    # --- register provenance + coverage to the PER-SOURCE FRAGMENT ---------
    reg_rows = []
    for code, *_ in SERIES:
        res = results[code]
        row = res.register_row(source=SOURCE, series_id=code, license=LICENSE,
                               extra_flags=extra_flags[code])
        row["year_min"], row["year_max"] = year_bounds[code]
        reg_rows.append(row)
    FRAGMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    register.upsert_rows(reg_rows, path=str(FRAGMENT_PATH))
    print(f"[unhcr] wrote register fragment {FRAGMENT_PATH} ({len(reg_rows)} rows)")
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true",
                    help="Re-pull live from the UNHCR API instead of reading the cache.")
    args = ap.parse_args()
    run(refresh=args.refresh)
