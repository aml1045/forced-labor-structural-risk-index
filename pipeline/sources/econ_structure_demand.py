"""Economic Structure & Demand (Exploitation) — Layer-2 source connector.

Domain: exploitation/economic-structure-demand.
Within-domain combine (see docs/METHODS.md; design decision):
    M = mean( mean(D1, D2, D3), D4 )            # cluster-equal (business 50 / crime 50)
then domain-level attenuate-only modulators (detectability, shared governance)
applied ONCE each. This connector produces the *standardized 0-1 signal layer*
for the domain's drivers; it does NOT compute the domain composite (that is the
aggregation stage) and it does NOT re-score the shared governance modulator
(scored once globally; de-duplicated downstream -- referenced, not re-pulled).

What this module maps (only the DEFENSIBLE signals; the rest are flagged):

  D1 Hazardous Sectoral Composition (cluster a, business)
    D1.s1 hazardous-sector employment share
      -> World Bank WDI  SL.AGR.EMPL.ZS  (employment in agriculture, % of total).
         Agriculture is the single best-evidenced high-risk sector (ILO/Walk
         Free/IOM 2022; Breman; Mezzadri). PARTIAL proxy: the full hazardous mix
         (construction/domestic/fishing/garments/brick-kiln) is not separable
         from one WB series -> mapped as agriculture-share, FLAGGED partial.
         Per-exposure share, absolute anchor 0-1. CC BY 4.0 (publish-safe).

  D2 Productive-Structure Informality & Fissuring (cluster a, business)
    D2.s1 informal-employment share
      -> ILOSTAT  SDG_0831_SEX_ECO_RT_A  (informal employment rate, SDG 8.3.1),
         slice sex=SEX_T / classif1=ECO_SECTOR_TOTAL. Native 0-100% share,
         absolute anchor. CC BY 4.0 (ILO open). STRONG fit.
    D2.s2 fissuring depth / D2.s3 tier opacity -> GAP (no off-the-shelf country
         series). Flagged, not proxied.

  D3 Buyer-Side Demand & the Coercion Premium (cluster a, demand)
    *** D3 is FLAGGED as weakly measurable at country level (see docs/METHODS.md). ***
    This connector does NOT over-proxy it. The ONLY defensible partial:
    D3.s2 sourcing-squeeze exposure ~ export-product concentration
      -> ALREADY LOADED as unctad_export_concentration (owned by the
         UNCTAD module -- NOT re-pulled here). Referenced as a
         LOW-CONFIDENCE partial proxy for sourcing-squeeze exposure ONLY.
    D3.s1 buyer concentration / lead-firm power -> HARD GAP (no clean country
         measure; candidate OECD-TiVA/Eora GVC is a *position* index, NOT
         buyer power). NOT mapped -> low-confidence / flag.
    D3.s3 demand for controllable labour / D3.s4 coercion-return -> GAP.
    Boundary: D3.s1 is *product-market* buyer power, NOT labour-market
    monopsony (Foreclosed Exit's). Recorded for downstream collinearity test.

  D4 Criminal-Market Embedding (cluster b, crime) -- the WHOLE crime cluster
    D4.s1/s2/s3 -> HARD GAP. GI-TOC human-trafficking criminal-market score is
    OUTCOME-CIRCULAR and EXCLUDED (circularity traps; see docs/scoring-rules.md
    and docs/METHODS.md). No defensible non-circular country dataset
    exists. NOT mapped -> low-confidence / flag; pending decision.

  Modulators (placed once each; NOT scored here):
    detectability gate  ~ LAI_INDE inspectors -- referenced
    shared governance   = wb_wgi_rule_of_law / v2x_rule -- scored ONCE
                          globally; de-duplicated downstream.

Scoring rules (v1; see docs/scoring-rules.md):
  rule 1 -- 0-1 per-exposure absolute-anchored scale (anchors below).
  rule 9 -- missing = drop-and-re-average, >=50%/>=2 floor; never missing->0;
            below-floor flagged. (anchor_scale handles this.)
  direction set explicitly per signal.

Run:  python -m pipeline.sources.econ_structure_demand
      python -m pipeline.sources.econ_structure_demand --dry-run
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = _REPO_ROOT / "data" / "processed" / "econ_structure_demand.csv"
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "econ_structure_demand.csv"

WB_SOURCE = "World Bank WDI (employment by sector, modelled ILO estimate)"
WB_LICENSE = "CC BY 4.0"
ILO_SOURCE = "ILOSTAT (ILO, rplumber API)"
ILO_LICENSE = "CC BY 4.0 (ILO open data)"

WB_API = "https://api.worldbank.org/v2"
ILO_API = "https://rplumber.ilo.org/data/indicator"
TIMEOUT = 120
RETRIES = 1

# rplumber 403s the default urllib UA -- a custom UA is REQUIRED (ported).
_ILO_HEADERS = {
    "User-Agent": "FLSRI-pipeline/1.0 (academic research)",
    "Accept": "text/csv,application/octet-stream,*/*",
}

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()


# --------------------------------------------------------------------------
# Signal specs (the DEFENSIBLE, wired signals only).
# --------------------------------------------------------------------------

# D1.s1 -- hazardous-sector (agriculture) employment share.
_D1_SPEC = AnchorSpec(
    indicator="esd_d1_hazardous_sector_share",
    floor=0.0, ceiling=60.0,
    direction="high_risk",
    unit="employment in agriculture, % of total employment",
    anchor_source=(
        "Per-exposure share. Agriculture is the single best-evidenced high-risk "
        "sector for forced labour (ILO/Walk Free/IOM 2022 treats sectoral "
        "location as an exposure variable; Breman 1996/2007; Mezzadri 2017). "
        "Floor 0 = no agricultural employment (min hazardous-composition risk); "
        "ceiling 60% = agrarian-dominant economies saturate at 1.0 (in-sample "
        "max ~85%, but the risk relationship is treated as saturating well "
        "before total dependence). PRIOR-ANCHOR: starting point for the DATA "
        "gate, not locked. PARTIAL-PROXY: agriculture share is ONE high-risk "
        "sector; the full hazardous mix (construction/domestic/fishing/garments/"
        "brick-kiln) is not separable from a single WB series -- D1.s2 "
        "(labour-intensity) and D1.s3 (seasonality) are UNMAPPED (flag)."
    ),
)

# D2.s1 -- informal-employment share (SDG 8.3.1).
_D2_CODE = "SDG_0831_SEX_ECO_RT_A"
_D2_SPEC = AnchorSpec(
    indicator="esd_d2_informal_employment_share",
    floor=0.0, ceiling=100.0,
    direction="high_risk",
    unit="informal employment, % of total employment (SDG 8.3.1)",
    anchor_source=(
        "Rate is a 0-100% share by construction; full-range absolute anchor "
        "(0 = no informal employment = min risk, 100 = wholly informal "
        "productive structure = max risk). ILO treats informal-economy location "
        "as a forced-labour exposure variable (ILO/Walk Free/IOM 2022; Chen "
        "2012). Slice sex=SEX_T / classif1=ECO_SECTOR_TOTAL. STRONG fit for "
        "D2.s1; D2.s2 fissuring-depth and D2.s3 tier-opacity remain UNMAPPED "
        "(no off-the-shelf country series) -- flagged."
    ),
)


# --------------------------------------------------------------------------
# World Bank pull (auth-free HTTP + pagination; ported pattern).
# --------------------------------------------------------------------------

def _http_json(url: str):
    last = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            if attempt < RETRIES:
                time.sleep(2)
    raise RuntimeError(f"WB HTTP failed after {RETRIES + 1} attempts: {last}")


def _pull_wb_series(code: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        url = (f"{WB_API}/country/all/indicator/{code}"
               f"?format=json&per_page=20000&page={page}")
        body = _http_json(url)
        if not isinstance(body, list) or len(body) < 2 or not isinstance(body[1], list):
            raise RuntimeError(f"{code}: unexpected WB response shape")
        meta, data = body[0], body[1]
        for item in data:
            rows.append({
                "iso3": item.get("countryiso3code") or "",
                "year": item.get("date", ""),
                "value": item.get("value", None),
            })
        total_pages = int(meta.get("pages", 1)) if isinstance(meta, dict) else 1
        if page >= total_pages:
            break
        page += 1
    return rows


def _wb_most_recent(rows: list[dict], sample):
    best: dict = {}
    for r in rows:
        v = r.get("value")
        if v in (None, ""):
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(fv):
            continue
        iso3 = iso_utils.normalize_to_iso3(r.get("iso3"))
        if not iso3 or iso3 not in sample:
            continue
        try:
            yr = int(str(r.get("year")).strip())
        except (TypeError, ValueError):
            continue
        cur = best.get(iso3)
        if cur is None or yr > cur[0]:
            best[iso3] = (yr, fv)
    values = {k: yv[1] for k, yv in best.items()}
    years = [yv[0] for yv in best.values()]
    return values, (min(years) if years else None), (max(years) if years else None)


# --------------------------------------------------------------------------
# ILOSTAT pull (custom UA REQUIRED; post-download filter; ported pattern).
# --------------------------------------------------------------------------

def _http_bytes(url: str) -> bytes:
    last = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=_ILO_HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            if attempt < RETRIES:
                time.sleep(2)
    raise RuntimeError(f"ILOSTAT HTTP failed after {RETRIES + 1} attempts: {last}")


def _pull_ilo_informal(sample):
    raw = _http_bytes(f"{ILO_API}?id={_D2_CODE}&format=.csv")
    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    for col in ("ref_area", "time", "obs_value"):
        if col not in df.columns:
            raise RuntimeError(f"{_D2_CODE}: missing column {col}; got {list(df.columns)[:12]}")
    # Slice: total sex, total economic sector (informal share of ALL employment).
    if "sex" in df.columns:
        df = df[df["sex"].astype(str) == "SEX_T"]
    if "classif1" in df.columns:
        df = df[df["classif1"].astype(str) == "ECO_SECTOR_TOTAL"]
    df = df.dropna(subset=["obs_value"]).copy()
    df["_iso3"] = df["ref_area"].map(iso_utils.normalize_to_iso3)
    df = df.dropna(subset=["_iso3"])
    df = df[df["_iso3"].isin(sample)]
    df["time"] = pd.to_numeric(df["time"], errors="coerce")
    df = df.dropna(subset=["time"])
    if df.empty:
        return {}, None, None
    idx = df.groupby("_iso3")["time"].idxmax()
    mr = df.loc[idx]
    values = {row["_iso3"]: float(row["obs_value"]) for _, row in mr.iterrows()}
    return values, int(mr["time"].min()), int(mr["time"].max())


# --------------------------------------------------------------------------
# D3/D4 gap register rows -- NOTE rows (no data; honest flags).
# --------------------------------------------------------------------------

def _gap_rows():
    return [
        {
            "indicator": "esd_d3_buyer_concentration",
            "source": "(unmapped -- hard gap)",
            "series_id": "NONE",
            "countries": 0, "year_min": "", "year_max": "",
            "license": "n/a",
            "direction": "high_risk",
            "anchor": "n/a (no defensible country measure)",
            "coverage_pct": 0.0,
            "flags": (
                "HARD-GAP / LOW-CONFIDENCE: D3.s1 buyer concentration / lead-firm "
                "power has NO clean off-the-shelf country measure (OECD-TiVA / "
                "UNCTAD-Eora GVC are *position* indices, not buyer power). NOT "
                "mapped -- do NOT over-proxy (D3 weakly-measurable flag). "
                "PARTIAL D3.s2 sourcing-squeeze proxy is the ALREADY-LOADED "
                "unctad_export_concentration, used "
                "LOW-CONFIDENCE only. BOUNDARY: product-market buyer power, NOT "
                "labour-market monopsony (Foreclosed Exit's) -- screen "
                "collinearity vs Foreclosed-Exit monopsony (see docs/METHODS.md). "
                "Pending decision: buyer/criminal-market sourcing."
            ),
        },
        {
            "indicator": "esd_d4_criminal_market_embedding",
            "source": "(unmapped -- hard gap, circularity-constrained)",
            "series_id": "NONE",
            "countries": 0, "year_min": "", "year_max": "",
            "license": "n/a",
            "direction": "high_risk",
            "anchor": "n/a (no non-circular country measure)",
            "coverage_pct": 0.0,
            "flags": (
                "HARD-GAP / LOW-CONFIDENCE: D4 criminal-market embedding (the "
                "WHOLE crime cluster, cluster-weighted 50/50 per design decision "
                "2026-06) has NO defensible non-circular country dataset. GI-TOC "
                "human-trafficking criminal-market score is OUTCOME-CIRCULAR and "
                "EXCLUDED (circularity traps; see docs/scoring-rules.md and "
                "docs/METHODS.md). NOT mapped. CONSEQUENCE: the crime cluster {D4} is "
                "unscorable -> the domain currently rests on the business cluster "
                "{D1,D2(,D3 partial)} alone; flag that cluster-equal 50/50 combine "
                "cannot be realised until D4 is sourced. Pending decision: "
                "buyer/criminal-market sourcing for the evidence base."
            ),
        },
    ]


# --------------------------------------------------------------------------
# Run.
# --------------------------------------------------------------------------

def run(dry_run: bool = False):
    countries = iso_utils.load_sample()
    n_total = len(countries)

    print("Plan -- Economic Structure & Demand defensible signals to pull:")
    print(f"    - D1.s1 hazardous-sector share  : WB SL.AGR.EMPL.ZS")
    print(f"    - D2.s1 informal-employment share: ILOSTAT {_D2_CODE} (SEX_T/ECO_SECTOR_TOTAL)")
    print(f"    - D3.s2 sourcing-squeeze         : REFERENCE existing unctad_export_concentration (low-confidence)")
    print(f"    - D3.s1 buyer concentration      : HARD GAP (unmapped)")
    print(f"    - D4   criminal-market           : HARD GAP, circularity-constrained (unmapped)")
    if dry_run:
        print("\n--dry-run: no network calls made.")
        return None

    columns = []
    scored = {}
    register_rows = []

    # --- D1.s1 : WB agriculture employment share ---------------------------
    print("[esd] pulling WB SL.AGR.EMPL.ZS ...", end=" ", flush=True)
    wb_rows = _pull_wb_series("SL.AGR.EMPL.ZS")
    d1_vals, d1_ymin, d1_ymax = _wb_most_recent(wb_rows, set(countries))
    if not d1_vals:
        sys.exit("\nSL.AGR.EMPL.ZS: pull returned no usable rows -- ABORT (never report done on a failing pull).")
    d1 = anchor_scale(d1_vals, _D1_SPEC, sample=countries)
    scored[_D1_SPEC.indicator] = d1
    columns.append(_D1_SPEC.indicator)
    row = d1.register_row(source=WB_SOURCE, series_id="SL.AGR.EMPL.ZS", license=WB_LICENSE)
    row["year_min"], row["year_max"] = d1_ymin, d1_ymax
    register_rows.append(row)
    print(f"{d1.meta['n_present']}/{n_total}  cov {d1.meta['coverage_pct']:.1f}%"
          + ("  [BELOW FLOOR]" if d1.meta["below_floor"] else ""))
    time.sleep(0.5)

    # --- D2.s1 : ILOSTAT informal employment share -------------------------
    print(f"[esd] pulling ILOSTAT {_D2_CODE} ...", end=" ", flush=True)
    d2_vals, d2_ymin, d2_ymax = _pull_ilo_informal(set(countries))
    if not d2_vals:
        sys.exit(f"\n{_D2_CODE}: pull returned no usable rows -- ABORT (never report done on a failing pull).")
    d2 = anchor_scale(d2_vals, _D2_SPEC, sample=countries)
    scored[_D2_SPEC.indicator] = d2
    columns.append(_D2_SPEC.indicator)
    row = d2.register_row(source=ILO_SOURCE, series_id=_D2_CODE, license=ILO_LICENSE)
    row["year_min"], row["year_max"] = d2_ymin, d2_ymax
    register_rows.append(row)
    print(f"{d2.meta['n_present']}/{n_total}  cov {d2.meta['coverage_pct']:.1f}%"
          + ("  [BELOW FLOOR]" if d2.meta["below_floor"] else ""))

    # --- D3/D4 gap NOTE rows (honest flags, no fabricated data) ------------
    register_rows.extend(_gap_rows())

    # --- write data/processed/econ_structure_demand.csv --------------------
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3"] + columns)
        for iso3 in countries:
            line = [iso3]
            for col in columns:
                v = scored[col].get(iso3)
                line.append("" if v is None else round(v, 4))
            w.writerow(line)
    print(f"\n[esd] wrote {OUT_PATH} ({n_total} rows x {len(columns)} wired signals)")

    # --- write register fragment ------------------------------------------
    register.ensure_header(path=str(FRAGMENT_PATH))
    register.upsert_rows(register_rows, path=str(FRAGMENT_PATH))
    print(f"[esd] wrote register fragment {FRAGMENT_PATH} ({len(register_rows)} rows)")

    for col in columns:
        m = scored[col].meta
        print(f"   {col:<36} dir={m['direction']:<9} cov={m['coverage_pct']:.1f}%  "
              f"floor={'BELOW' if m['below_floor'] else 'ok'}")
    print("   esd_d3_buyer_concentration            HARD-GAP (unmapped; UNCTAD partial referenced)")
    print("   esd_d4_criminal_market_embedding      HARD-GAP (unmapped; GI-TOC excluded for circularity)")

    return scored, register_rows


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true", help="Print the plan, no network calls.")
    args = p.parse_args()
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
