"""Monetization Domain B connector -- Cash & Informal-Economy Retention.

DOMAIN: monetization/domain-b-cash-informal-retention (Layer-2 data map).
  Product-2 by default (scoring rule 7; see docs/scoring-rules.md). Product-1
  inclusion is CONDITIONAL on the three-condition test (docs/METHODS.md) and is
  NOT resolved here -- the module is operator-agnostic and only produces the
  standardized signal layer; the Product-1 gate is a downstream pending decision.

WHAT THIS PRODUCES (two of Domain B's conceptual signals, the operationalizable
subset -- see config/api-config/monetization_b.yaml for the full map and the
unwired signals flagged for data-stage review):

  monet_b_shadow_economy   D1 s1.2  shadow/informal-economy size (% of GDP)
                           SOURCE: World Bank Informal Economy Database (DGE),
                           data360 API (LIVE). OWNED by this module -- the new
                           connector this domain needed. Publish-safe.

  monet_b_financial_exclusion  D2 s2.1  formal-credit absence / financial exclusion
                           SOURCE: World Bank Global Findex 2025, 1 - account_t_d
                           (on-disk direct upload). This is the SAME variable as
                           Recruitment-Debt's findex_account_exclusion -- a
                           CROSS-DOMAIN SHARED signal. It is written here under a
                           distinct Monetization indicator id and flagged
                           DUPLICATE-OF-FINDEX so the data-stage correlation
                           screen de-duplicates the shared variance. This module
                           does NOT rebuild the
                           sibling findex.py connector; it reads the same source
                           file directly so Domain B has its D2 input regardless
                           of sibling build order.

SCORING-RULE CONFORMANCE (see docs/scoring-rules.md)
  Rule 1 : per-exposure (both signals are population/GDP shares already);
           absolute anchors via standardize.anchor_scale; clamp to [0,1].
  Rule 9 : missing stays missing (never -> 0); coverage_pct recorded; below-floor
           flagged. The Domain-B within-driver/within-domain averaging
           (drop-and-re-average, the corruption modulator, the inclusion
           defeater, the criminal-proceeds gate) is applied DOWNSTREAM at
           aggregation -- this module emits the standardized per-signal layer
           only, which is the data-stage deliverable.
  Direction : set explicitly per signal (both high_risk).

REUSES the repo's iso_utils (ISO3 normalization), standardize (anchor_scale),
and register (provenance fragment); see docs/METHODS.md.

Run:  python -m pipeline.sources.monetization_b
      python -m pipeline.sources.monetization_b --no-network   # shadow econ skipped if no net
"""

from __future__ import annotations

from pathlib import Path
import argparse
import csv
import json
import ssl
import sys
import urllib.error
import urllib.request

import pandas as pd
import yaml

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = _REPO_ROOT / "data" / "processed" / "monetization_b.csv"
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "monetization_b.csv"
CONFIG_PATH = _REPO_ROOT / "config" / "api-config" / "monetization_b.yaml"

# Findex direct upload (same source file findex.py reads), staged in-repo.
_FINDEX_FALLBACK = _REPO_ROOT / "data" / "raw" / "GlobalFindexDatabase2025.csv"

API_TIMEOUT = 90
API_PAGE = 1000  # data360 returns up to 1000 rows/page

_PRIOR_ANCHOR_FLAG = (
    "PRIOR-ANCHOR: floor/ceiling are a data-stage starting point -- re-examine"
)
_PRODUCT1_FLAG = (
    "PRODUCT-1-CONDITIONAL: Domain B is Product-2 by default (scoring rule 7); "
    "Product-1 inclusion is subject to the three-condition test (non-circular "
    "indicator; separable local slice; controllable normative-split gate -- "
    "docs/METHODS.md) and is NOT resolved at the data layer. The criminal-"
    "proceeds-context gate (c_proceeds) and the coercion-context gate (c_coercion) "
    "are UNWIRED here -- raw signal capacity only, gating applied downstream"
)


# --------------------------------------------------------------------------
# D1 s1.2 -- shadow-economy size (OWNED, live data360 pull).
# --------------------------------------------------------------------------

def _data360_pull(api_base, database_id, indicator) -> list[dict]:
    """Pull all country-year obs for one Informal Economy DB indicator.

    Returns [{iso3, year, value}]. Paginates via skip. Raises on failure so the
    caller can try the fallback indicator.
    """
    rows: list[dict] = []
    skip = 0
    while True:
        url = (f"{api_base}?DATABASE_ID={database_id}&INDICATOR={indicator}"
               f"&skip={skip}")
        req = urllib.request.Request(
            url, headers={"User-Agent": "FLSRI-pipeline/1.0 (academic research)",
                          "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=API_TIMEOUT, context=_SSL_CTX) as resp:
            body = json.loads(resp.read())
        if not isinstance(body, dict):
            raise RuntimeError(f"{indicator}: unexpected response shape")
        vals = body.get("value") or []
        for it in vals:
            rows.append({
                "iso3": it.get("REF_AREA") or "",
                "year": it.get("TIME_PERIOD") or "",
                "value": it.get("OBS_VALUE"),
            })
        count = int(body.get("count", 0))
        skip += len(vals)
        if len(vals) == 0 or skip >= count:
            break
    return rows


def _most_recent_by_iso3(rows: list[dict]) -> tuple[dict, int | None, int | None]:
    best: dict = {}  # iso3 -> (year, value)
    for r in rows:
        v = r.get("value")
        if v in (None, ""):
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        try:
            yr = int(str(r.get("year")).strip())
        except (TypeError, ValueError):
            continue
        iso3 = iso_utils.normalize_to_iso3(r.get("iso3"))
        if not iso3:
            continue
        cur = best.get(iso3)
        if cur is None or yr > cur[0]:
            best[iso3] = (yr, fv)
    if not best:
        return {}, None, None
    values = {k: yv[1] for k, yv in best.items()}
    years = [yv[0] for yv in best.values()]
    return values, min(years), max(years)


def _shadow_economy(cfg: dict, sample, use_network: bool):
    spec_cfg = cfg["shadow_economy"]
    raw, ymin, ymax, used_indicator = {}, None, None, None

    if use_network:
        for key, label in (("indicator_primary", "DGE"),
                           ("indicator_fallback", "MIMIC")):
            ind = spec_cfg[key]
            try:
                rows = _data360_pull(spec_cfg["api_base"],
                                     spec_cfg["database_id"], ind)
                vals, ymin, ymax = _most_recent_by_iso3(rows)
                n_in = sum(1 for c in sample if c in vals)
                print(f"[monet_b] shadow-economy {ind} ({label}): "
                      f"{len(rows)} raw rows, {n_in}/{len(sample)} in-sample, "
                      f"years {ymin}-{ymax}")
                if n_in >= 2:
                    raw, used_indicator = vals, ind
                    break
            except (urllib.error.URLError, urllib.error.HTTPError,
                    TimeoutError, RuntimeError) as e:
                print(f"[monet_b] shadow-economy {ind} pull failed: {e}",
                      file=sys.stderr)
    else:
        print("[monet_b] --no-network: shadow-economy pull SKIPPED "
              "(data360 live API; no on-disk cache)", file=sys.stderr)

    flags = [_PRIOR_ANCHOR_FLAG, _PRODUCT1_FLAG,
             "CONNECTING-FLAG: shadow/informal economy connects -> Exploitation "
             "informality reading; scored in Monetization (retention) form only "
             "-- cross-phase de-dup is a data-stage correlation/synthesis step",
             "OWNED-CONNECTOR: WB Informal Economy DB (data360 API, live)"]
    if not raw:
        flags.append("PULL-EMPTY: shadow-economy not retrieved this run "
                     "(network/API) -- column blank, NOT zero-filled")

    spec = AnchorSpec(
        indicator="monet_b_shadow_economy",
        floor=spec_cfg["floor"], ceiling=spec_cfg["ceiling"],
        direction=spec_cfg["direction"], unit=spec_cfg["unit"],
        anchor_source=spec_cfg["anchor_source"],
    )
    res = anchor_scale(raw, spec, sample=sample)
    src = spec_cfg["source"]
    if used_indicator:
        src = f"{src} [{used_indicator}]"
    row = res.register_row(source=src,
                           series_id=used_indicator or spec_cfg["indicator_primary"],
                           license=spec_cfg["license"], extra_flags=flags)
    row["year_min"] = ymin if ymin is not None else ""
    row["year_max"] = ymax if ymax is not None else ""
    return res, row


# --------------------------------------------------------------------------
# D2 s2.1 -- financial exclusion (SHARED Findex variable, on-disk).
# --------------------------------------------------------------------------

def _financial_exclusion(cfg: dict, sample):
    spec_cfg = cfg["financial_exclusion"]
    path = _FINDEX_FALLBACK
    if not path.exists():
        print(f"[monet_b] financial-exclusion: Findex file not found at {path}",
              file=sys.stderr)
        spec = AnchorSpec(
            indicator="monet_b_financial_exclusion",
            floor=spec_cfg["floor"], ceiling=spec_cfg["ceiling"],
            direction=spec_cfg["direction"], unit=spec_cfg["unit"],
            anchor_source=spec_cfg["anchor_source"])
        res = anchor_scale({}, spec, sample=sample)
        row = res.register_row(source=spec_cfg["source"],
                               series_id=spec_cfg["column"],
                               license=spec_cfg["license"],
                               extra_flags=["PULL-EMPTY: Findex source file absent"])
        row["year_min"], row["year_max"] = "", ""
        return res, row

    df = pd.read_csv(path, low_memory=False)
    allc = df[df[spec_cfg["group_col"]] == spec_cfg["group_all"]].copy()
    col = spec_cfg["column"]
    allc = allc[allc[col].notna()].copy()
    allc["iso3"] = allc[spec_cfg["iso_col"]].map(iso_utils.normalize_to_iso3)
    # fall back to country name for any code that did not resolve
    miss = allc["iso3"].isna()
    if miss.any():
        allc.loc[miss, "iso3"] = allc.loc[miss, spec_cfg["name_col"]].map(
            iso_utils.normalize_to_iso3)
    allc = allc[allc["iso3"].notna()]

    # most-recent wave per country
    allc = allc.sort_values(spec_cfg["year_col"]).groupby("iso3", as_index=False).last()
    raw = {}
    for _, r in allc.iterrows():
        val = float(r[col])
        if spec_cfg.get("transform") == "complement":
            val = 1.0 - val
        raw[r["iso3"]] = val
    raw = {k: v for k, v in raw.items() if k in set(sample)}
    ymin = int(allc[allc["iso3"].isin(raw)][spec_cfg["year_col"]].min()) if raw else None
    ymax = int(allc[allc["iso3"].isin(raw)][spec_cfg["year_col"]].max()) if raw else None

    flags = [
        _PRODUCT1_FLAG,
        "DUPLICATE-OF-FINDEX: same variable as Recruitment-Debt's "
        "findex_account_exclusion (1 - account_t_d). SHARED cross-domain signal "
        "-- the data-stage correlation screen must de-duplicate the shared "
        "variance; do NOT double-count across Recruitment-Debt and Monetization-B",
        "CONNECTING-FLAG: financial exclusion connects -> Recruitment Debt domain; "
        "scored in Monetization (D2 s2.1) form only",
        "DEFEATER-NOTE: this exclusion signal is the inverse of Domain B's z.1 "
        "inclusion DEFEATER -- the defeater is applied downstream at aggregation, "
        "not double-entered here",
        "SHARED-CONNECTOR: reads the same Findex direct upload as findex.py; "
        "does not rebuild the sibling connector",
    ]
    spec = AnchorSpec(
        indicator="monet_b_financial_exclusion",
        floor=spec_cfg["floor"], ceiling=spec_cfg["ceiling"],
        direction=spec_cfg["direction"], unit=spec_cfg["unit"],
        anchor_source=spec_cfg["anchor_source"])
    res = anchor_scale(raw, spec, sample=sample)
    row = res.register_row(source=spec_cfg["source"], series_id=col,
                           license=spec_cfg["license"], extra_flags=flags)
    row["year_min"] = ymin if ymin is not None else ""
    row["year_max"] = ymax if ymax is not None else ""
    print(f"[monet_b] financial-exclusion (Findex 1-account_t_d): "
          f"{len(raw)}/{len(sample)} in-sample, years {ymin}-{ymax}")
    return res, row


# --------------------------------------------------------------------------
# Run.
# --------------------------------------------------------------------------

def run(use_network: bool = True):
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    sample = iso_utils.load_sample()

    columns, scored, register_rows = [], {}, []

    for res, row in (_shadow_economy(cfg, sample, use_network),
                     _financial_exclusion(cfg, sample)):
        columns.append(row["indicator"])
        scored[row["indicator"]] = res
        register_rows.append(row)
        m = res.meta
        print(f"[monet_b] {row['indicator']} dir={m['direction']} "
              f"anchor={m['anchor']} coverage {m['coverage_pct']:.1f}% "
              f"({m['n_present']}/{m['n_total']}) below_floor={m['below_floor']}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3"] + columns)
        for iso3 in sample:
            line = [iso3]
            for col in columns:
                v = scored[col].get(iso3)
                line.append("" if v is None else round(v, 4))
            w.writerow(line)

    register.ensure_header(path=str(FRAGMENT_PATH))
    register.upsert_rows(register_rows, path=str(FRAGMENT_PATH))

    print(f"\n[monet_b] wrote {OUT_PATH} "
          f"({len(sample)} rows x {len(columns)} signals)")
    print(f"[monet_b] wrote register fragment {FRAGMENT_PATH} "
          f"({len(register_rows)} rows)")
    return scored, register_rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Monetization Domain B connector")
    ap.add_argument("--no-network", action="store_true",
                    help="Skip the live shadow-economy pull (no on-disk cache).")
    args = ap.parse_args()
    run(use_network=not args.no_network)
