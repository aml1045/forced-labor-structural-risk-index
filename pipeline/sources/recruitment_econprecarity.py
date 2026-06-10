"""Recruitment / Economic Precarity -- Layer-2 data-map GAP-signal connector.

Sources the THREE gap signals the Layer-2 crosswalk flagged for the
Recruitment / Economic Precarity domain (see docs/METHODS.md):

  D1  income poverty / deprivation   -> World Bank SI.POV.UMIC
        ($6.85/day, 2017 PPP, poverty headcount ratio)               [GAP]
  D2  livelihood informality          -> ILOSTAT SDG_0831_SEX_ECO_RT_A
        (SDG 8.3.1 informal-employment rate, TOTAL/SEX_T)            [GAP]
  D3  agrarian-livelihood concentration-> World Bank SL.AGR.EMPL.ZS
        (employment in agriculture % of total employment; ILO-modelled,
         mirrored on WB; the S3b agrarian-concentration signal)      [GAP-FILL]
  D5  income volatility / price shock  -> World Bank NY.GDP.MKTP.KD.ZG
        (std-dev of annual real GDP growth = structural income-
         instability proxy, residual-scopable)                       [GAP]

  D3 NOTE: only S3b (agrarian concentration) is groundable on a 195-country
  panel. S3a (landlessness / rural land-tenure insecurity) has NO publish-safe
  global panel -- it is flagged a hard gap, not fabricated; D3 rests on S3b
  alone (a single-signal driver, surfaced low-confidence). D4 (youth/structural
  underemployment) is carried only by the shared worldbank.csv proxies
  (wb_lower_secondary_completion, wb_labor_productivity) -- the direct
  S4a NEET / S4b labour-underutilization signals are NOT wired (flagged gap; an
  ILOSTAT NEET pull is the candidate, deferred to avoid a thin second ILO hit
  here -- flagged for data-stage review).

The already-wired Economic Precarity inputs are NOT re-pulled here (per the
crosswalk): Gini, labour productivity, lower-secondary completion, remittances,
population, and the shared general-governance modulator are owned by the
worldbank.py / vdem.py connectors and are referenced, not duplicated. The
shared general-governance signal is scored ONCE (the downstream data-stage
correlation screen de-dupes it) -- this module does NOT pull or score a
governance index.

DESIGN NOTES (open questions for data-stage review):
  * D1 poverty line.  $6.85 (UMIC) is chosen over $2.15 (extreme/DDAY)
    deliberately: the domain's central nuance (docs/METHODS.md) is that
    recruitment risk concentrates in the LOWER-MIDDLE stratum, not the destitute
    floor (van der Vink et al. 2023; de Haas 2021). The $6.85 headcount captures
    that broader pickable population, conformant to the D1 non-monotone reading
    rule ("lower-middle stratum weighted over the destitute floor; never a
    monotone ramp"). $2.15 (SI.POV.DDAY) and $3.65 (SI.POV.LMIC) are pulled too
    and kept as register NOTE rows so data-stage review can swap the line.
  * D5 income-volatility proxy.  Real GDP-growth volatility (std dev of annual
    %-growth over the recent window) is a STRUCTURAL macro proxy for the
    household income-instability the construct names (S5a). It is NOT yet
    the residual-scoped (net-of-named-hazard) construct docs/METHODS.md
    requires (condition c_C). That residual decomposition vs. Structural
    Disruption is a downstream FORMULA concern -- flagged here, not applied; the
    raw structural-volatility signal is what this connector delivers.

Standardize per scoring-rules v1 (see docs/scoring-rules.md):
  rule 1  0-1 per-exposure absolute-anchored scale (anchors below; PRIORS).
  rule 9  drop-and-re-average with >=50%/>=2 floor; never missing->0; flag
          below-floor. anchor_scale handles this.
  direction set explicitly per signal (all three: higher = MORE risk).

Reuses (never re-derives): pipeline.iso_utils, pipeline.standardize,
pipeline.register. Live pull by default; WB falls back to the prior fresh cache
(worldbank_cache.csv, vintage 2026-05-28) if the network fails, so a run still
produces output. ILOSTAT has no cache fallback -- it pulls live.

CACHE CAVEAT: the prior WB cache only holds the 7 first-effort series, which do
NOT include SI.POV.UMIC or NY.GDP.MKTP.KD.ZG. So --cache yields 0 coverage for
poverty + volatility (informality is unaffected -- it pulls ILOSTAT live). The
cache path is a network-failure floor, not a substitute for the live pull; run
without --cache for real coverage.

Run:  python -m pipeline.sources.recruitment_econprecarity
      python -m pipeline.sources.recruitment_econprecarity --cache  # WB cache, ILO live
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
from statistics import pstdev

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = _REPO_ROOT / "data" / "processed" / "recruitment_econprecarity.csv"
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "recruitment_econprecarity.csv"

# WB cache fallback (same prior fresh cache worldbank.py uses), staged in-repo.
_WB_CACHE_FALLBACK = _REPO_ROOT / "data" / "aux" / "worldbank_cache.csv"

WB_SOURCE = "World Bank WDI"
WB_LICENSE = "CC BY 4.0"
ILO_SOURCE = "ILOSTAT (ILO, rplumber API)"
ILO_LICENSE = "CC BY 4.0 (ILO open data)"

WB_API_BASE = "https://api.worldbank.org/v2"
ILO_API_BASE = "https://rplumber.ilo.org/data/indicator"
PER_PAGE = 20000
TIMEOUT = 120
RETRIES = 1

# rplumber 403s the default urllib UA -- a custom UA is REQUIRED (ported).
_ILO_HEADERS = {
    "User-Agent": "FLSRI-pipeline/1.0 (academic research)",
    "Accept": "text/csv,application/octet-stream,*/*",
}

_PRIOR_ANCHOR_FLAG = (
    "PRIOR-ANCHOR: floor/ceiling a starting point only -- re-examine at the "
    "data-stage review"
)

# D5 volatility window: number of most-recent annual growth observations used to
# compute the structural-volatility std dev (a country needs >= MIN_VOL_OBS).
VOL_WINDOW = 15
MIN_VOL_OBS = 5


# --------------------------------------------------------------------------
# Anchors (scoring rule 1; docs/scoring-rules.md). All signals are high_risk
# (higher = more risk).
# --------------------------------------------------------------------------

POVERTY_SPEC = AnchorSpec(
    indicator="ep_poverty_headcount_685",
    floor=0.0, ceiling=100.0,
    direction="high_risk",
    unit="% of population below $6.85/day (2017 PPP)",
    anchor_source=(
        "Poverty headcount ratio at $6.85/day (SI.POV.UMIC), a population share "
        "0-100% by construction -> full-range absolute anchor (0 = no poverty, "
        "100 = entire population below the line). The $6.85 (upper-middle-income) "
        "line is chosen over $2.15 to capture the LOWER-MIDDLE pickable stratum "
        "the domain's non-monotone reading rule emphasizes (docs/METHODS.md; "
        "van der Vink et al. 2023; de Haas 2021). " + _PRIOR_ANCHOR_FLAG
    ),
)

INFORMAL_SPEC = AnchorSpec(
    indicator="ep_informal_employment_share",
    floor=0.0, ceiling=100.0,
    direction="high_risk",
    unit="% of total employment that is informal (SDG 8.3.1)",
    anchor_source=(
        "Informal employment rate (SDG 8.3.1, ILOSTAT SDG_0831_SEX_ECO_RT_A, "
        "TOTAL economic activity, both sexes), a 0-100% employment share by "
        "construction -> full-range absolute anchor (0 = fully formal workforce, "
        "100 = fully informal). Direction high_risk: a larger unprotected informal "
        "workforce = more precarity exposure (D2; docs/METHODS.md). " + _PRIOR_ANCHOR_FLAG
    ),
)

AGRARIAN_SPEC = AnchorSpec(
    indicator="ep_agrarian_employment_share",
    floor=0.0, ceiling=80.0,
    direction="high_risk",
    unit="% of total employment in agriculture",
    anchor_source=(
        "Employment in agriculture as a share of total employment "
        "(SL.AGR.EMPL.ZS; ILO-modelled estimate, mirrored on WB). Per-exposure "
        "by construction (employment share). Floor 0 = no agrarian dependence; "
        "ceiling 80 ~ the high end of observed agrarian-livelihood concentration "
        "(advanced economies cluster <5%; the most agrarian economies reach "
        "60-80%) so highly agrarian, asset-thin economies saturate near 1.0. "
        "Direction high_risk: dependence on insecure, seasonal, intermediated "
        "agrarian work = absence of an asset buffer (D3, S3b; docs/METHODS.md; "
        "Natarajan et al. 2020; Mosse 2018). "
        "S3b ONLY: landlessness / land-tenure insecurity (S3a) has no publish-safe "
        "global panel and is a flagged gap -- this is the agrarian-concentration "
        "face of D3, a single-signal driver, surfaced low-confidence. "
        + _PRIOR_ANCHOR_FLAG
    ),
)

VOLATILITY_SPEC = AnchorSpec(
    indicator="ep_income_volatility",
    floor=0.0, ceiling=8.0,
    direction="high_risk",
    unit="std-dev of annual real GDP growth (pct points), recent ~15-yr window",
    anchor_source=(
        "Structural income-instability proxy: standard deviation of annual real "
        "GDP growth (NY.GDP.MKTP.KD.ZG) over the most recent ~15 years. Per-exposure "
        "by construction (a rate-of-change dispersion). Floor 0 = perfectly stable "
        "growth (min risk); ceiling 8 pct points ~ the volatile end (stable advanced "
        "economies cluster ~1-2pp; commodity-dependent / fragile economies routinely "
        "4-8pp) so high-instability regimes saturate at 1.0. Direction high_risk. "
        "STRUCTURAL-PROXY: this is NOT yet the residual-scoped (net-of-named-hazard) "
        "construct docs/METHODS.md requires (condition c_C) -- the "
        "residual decomposition vs. Structural Disruption is a downstream formula "
        "step, flagged not applied. " + _PRIOR_ANCHOR_FLAG
    ),
)


# --------------------------------------------------------------------------
# World Bank pull (ported from worldbank.py: auth-free HTTP, pagination, cache
# fallback). Returns raw rows [{iso3, year, series, value}] per code.
# --------------------------------------------------------------------------

def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CTX = _ssl_context()


def _wb_url(code: str, page: int) -> str:
    return (f"{WB_API_BASE}/country/all/indicator/{code}"
            f"?format=json&per_page={PER_PAGE}&page={page}")


def _http_get_json(url: str):
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
    raise RuntimeError(f"HTTP failed after {RETRIES + 1} attempts: {last}")


def _wb_pull_series_live(code: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        body = _http_get_json(_wb_url(code, page))
        if not isinstance(body, list) or len(body) < 2:
            raise RuntimeError(f"{code}: unexpected WB response shape")
        meta, data = body[0], body[1]
        if not isinstance(data, list):
            raise RuntimeError(f"{code}: WB data payload is not a list")
        for item in data:
            rows.append({
                "iso3": item.get("countryiso3code") or "",
                "year": item.get("date", ""),
                "series": code,
                "value": item.get("value", None),
            })
        total_pages = int(meta.get("pages", 1)) if isinstance(meta, dict) else 1
        if page >= total_pages:
            break
        page += 1
    return rows


def _wb_pull_all_live(codes: list[str]) -> dict:
    out = {}
    for i, code in enumerate(codes):
        rows = _wb_pull_series_live(code)
        out[code] = rows
        nn = sum(1 for r in rows if r["value"] not in (None, ""))
        print(f"[econprecarity] WB live pull {code}: {len(rows)} rows ({nn} non-null)")
        if i < len(codes) - 1:
            time.sleep(0.5)
    return out


def _wb_load_from_cache(codes: list[str]) -> dict:
    if not _WB_CACHE_FALLBACK.exists():
        raise FileNotFoundError(f"WB cache fallback not found: {_WB_CACHE_FALLBACK}")
    by_code: dict = {c: [] for c in codes}
    with open(_WB_CACHE_FALLBACK, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            code = row.get("series")
            if code in by_code:
                by_code[code].append({
                    "iso3": row.get("iso3") or "",
                    "year": row.get("year", ""),
                    "series": code,
                    "value": row.get("value", None),
                })
    return by_code


def _wb_most_recent(rows: list[dict]):
    """{iso3: value} most-recent non-NaN year, normalized to ISO3; + year span."""
    best: dict = {}
    for r in rows:
        val = r.get("value")
        if val is None or val == "":
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        if math.isnan(v):
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
            best[iso3] = (yr, v)
    if not best:
        return {}, None, None
    values = {k: yv[1] for k, yv in best.items()}
    years = [yv[0] for yv in best.values()]
    return values, min(years), max(years)


def _wb_volatility(rows: list[dict], window: int, min_obs: int):
    """{iso3: stddev of annual growth over recent `window` years}; + year span.

    Computes a STRUCTURAL income-volatility proxy: the population std dev of the
    most-recent `window` annual real-GDP-growth observations per country. A
    country needs >= `min_obs` observations or it is left missing (never 0).
    """
    series: dict = {}  # iso3 -> [(year, value)]
    for r in rows:
        val = r.get("value")
        if val is None or val == "":
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        if math.isnan(v):
            continue
        try:
            yr = int(str(r.get("year")).strip())
        except (TypeError, ValueError):
            continue
        iso3 = iso_utils.normalize_to_iso3(r.get("iso3"))
        if not iso3:
            continue
        series.setdefault(iso3, []).append((yr, v))

    out: dict = {}
    used_years: list[int] = []
    for iso3, obs in series.items():
        obs.sort(key=lambda t: t[0])
        recent = obs[-window:]
        if len(recent) < min_obs:
            continue
        out[iso3] = pstdev([v for _, v in recent])
        used_years.extend(y for y, _ in recent)
    if not used_years:
        return {}, None, None
    return out, min(used_years), max(used_years)


# --------------------------------------------------------------------------
# ILOSTAT pull (ported from ilostat.py: custom UA, csv parse, sex/classif filter).
# --------------------------------------------------------------------------

def _ilo_pull(code: str) -> list[dict]:
    url = f"{ILO_API_BASE}?id={code}&format=.csv"
    last = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=_ILO_HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
                raw = resp.read().decode("utf-8-sig", "replace")
            return list(csv.DictReader(io.StringIO(raw)))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            if attempt < RETRIES:
                time.sleep(2)
    raise RuntimeError(f"ILOSTAT {code}: HTTP failed after {RETRIES + 1} attempts: {last}")


def _ilo_informal_most_recent(rows: list[dict]):
    """{iso3: value} for SDG 8.3.1 informal rate, sliced to SEX_T + TOTAL economic
    activity, most-recent non-NaN year per country; + year span."""
    best: dict = {}
    for r in rows:
        if r.get("sex") != "SEX_T":
            continue
        if r.get("classif1") != "ECO_AGGREGATE_TOTAL":
            continue
        val = r.get("obs_value")
        if val in (None, ""):
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        if math.isnan(v):
            continue
        try:
            yr = int(str(r.get("time")).strip())
        except (TypeError, ValueError):
            continue
        iso3 = iso_utils.normalize_to_iso3(r.get("ref_area"))
        if not iso3:
            continue
        cur = best.get(iso3)
        if cur is None or yr > cur[0]:
            best[iso3] = (yr, v)
    if not best:
        return {}, None, None
    values = {k: yv[1] for k, yv in best.items()}
    years = [yv[0] for yv in best.values()]
    return values, min(years), max(years)


# --------------------------------------------------------------------------
# Run.
# --------------------------------------------------------------------------

def run(use_cache: bool = False):
    countries = iso_utils.load_sample()

    # Extra poverty lines kept as register NOTE rows so data-stage review can swap
    # the chosen $6.85 line. UMIC is the SCORED one; DDAY/LMIC are note-only.
    wb_codes = ["SI.POV.UMIC", "SI.POV.DDAY", "SI.POV.LMIC",
                "SL.AGR.EMPL.ZS", "NY.GDP.MKTP.KD.ZG"]

    # 1. Acquire WB rows (live, else cache).
    wb_raw = None
    if not use_cache:
        try:
            wb_raw = _wb_pull_all_live(wb_codes)
        except Exception as e:
            print(f"[econprecarity] WB live pull failed ({e}); falling back to cache",
                  file=sys.stderr)
        else:
            # write-back so the shared cache carries this connector's series
            from pipeline import raw_cache
            raw_cache.upsert_series(_WB_CACHE_FALLBACK, wb_raw,
                                    source_label="recruitment_econprecarity (WDI series)")
    if wb_raw is None:
        wb_raw = _wb_load_from_cache(wb_codes)
        print(f"[econprecarity] WB loaded from cache: {_WB_CACHE_FALLBACK}")

    # --- D1 poverty (scored: $6.85 UMIC) ---
    pov_vals, pov_ymin, pov_ymax = _wb_most_recent(wb_raw.get("SI.POV.UMIC", []))
    pov_res = anchor_scale(pov_vals, POVERTY_SPEC, sample=countries)

    # alt poverty lines -- coverage only, for the register note rows
    ddy_vals, ddy_ymin, ddy_ymax = _wb_most_recent(wb_raw.get("SI.POV.DDAY", []))
    lmic_vals, lmic_ymin, lmic_ymax = _wb_most_recent(wb_raw.get("SI.POV.LMIC", []))

    # --- D3 agrarian-livelihood concentration (S3b only) ---
    agr_vals, agr_ymin, agr_ymax = _wb_most_recent(wb_raw.get("SL.AGR.EMPL.ZS", []))
    agr_res = anchor_scale(agr_vals, AGRARIAN_SPEC, sample=countries)

    # --- D5 income volatility (std dev of recent real GDP growth) ---
    vol_vals, vol_ymin, vol_ymax = _wb_volatility(
        wb_raw.get("NY.GDP.MKTP.KD.ZG", []), VOL_WINDOW, MIN_VOL_OBS)
    vol_res = anchor_scale(vol_vals, VOLATILITY_SPEC, sample=countries)

    # --- D2 informality (ILOSTAT SDG 8.3.1) ---
    try:
        ilo_rows = _ilo_pull("SDG_0831_SEX_ECO_RT_A")
    except Exception as e:
        sys.exit(f"[econprecarity] ILOSTAT pull failed ({e}) -- ABORT "
                 f"(never report done on a failing pull).")
    inf_vals, inf_ymin, inf_ymax = _ilo_informal_most_recent(ilo_rows)
    if not inf_vals:
        sys.exit("[econprecarity] ILOSTAT SDG 8.3.1 returned no usable rows -- ABORT.")
    inf_res = anchor_scale(inf_vals, INFORMAL_SPEC, sample=countries)

    # 2. Write processed CSV: iso3 + 3 scored columns.
    scored = {
        "ep_poverty_headcount_685": pov_res,
        "ep_informal_employment_share": inf_res,
        "ep_agrarian_employment_share": agr_res,
        "ep_income_volatility": vol_res,
    }
    col_order = ["ep_poverty_headcount_685", "ep_informal_employment_share",
                 "ep_agrarian_employment_share", "ep_income_volatility"]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3"] + col_order)
        for iso3 in countries:
            line = [iso3]
            for col in col_order:
                v = scored[col].get(iso3)
                line.append("" if v is None else round(v, 4))
            w.writerow(line)
    print(f"\n[econprecarity] wrote {OUT_PATH} "
          f"({len(countries)} rows x {len(col_order)} risk indicators)")

    # 3. Register fragment.
    register_rows = []

    pov_row = pov_res.register_row(source=WB_SOURCE, series_id="SI.POV.UMIC",
                                   license=WB_LICENSE)
    pov_row["year_min"], pov_row["year_max"] = pov_ymin or "", pov_ymax or ""
    pov_row["flags"] = ("; ".join(filter(None, [
        pov_row["flags"],
        "GAP-FILL (Econ Precarity D1 poverty). $6.85/day (UMIC) line chosen over "
        "$2.15 for the lower-middle pickable stratum (docs/METHODS.md); "
        "non-monotone middle-stratum reading is a DOWNSTREAM signal-construction "
        "step (docs/METHODS.md) -- this connector delivers the raw headcount.",
    ])))
    register_rows.append(pov_row)

    inf_row = inf_res.register_row(source=ILO_SOURCE,
                                   series_id="SDG_0831_SEX_ECO_RT_A (TOTAL/SEX_T)",
                                   license=ILO_LICENSE)
    inf_row["year_min"], inf_row["year_max"] = inf_ymin or "", inf_ymax or ""
    inf_row["flags"] = ("; ".join(filter(None, [
        inf_row["flags"],
        "GAP-FILL (Econ Precarity D2 informality). SDG 8.3.1 informal-employment "
        "rate, ECO_AGGREGATE_TOTAL + SEX_T slice.",
    ])))
    register_rows.append(inf_row)

    agr_row = agr_res.register_row(source=WB_SOURCE, series_id="SL.AGR.EMPL.ZS",
                                   license=WB_LICENSE)
    agr_row["year_min"], agr_row["year_max"] = agr_ymin or "", agr_ymax or ""
    agr_row["flags"] = ("; ".join(filter(None, [
        agr_row["flags"],
        "GAP-FILL (Econ Precarity D3 agrarian concentration, S3b). Employment in "
        "agriculture (% of total employment), ILO-modelled, mirrored on WB. "
        "S3b ONLY: landlessness / land-tenure insecurity (S3a) has NO publish-safe "
        "global panel and is a flagged HARD GAP -- not fabricated; D3 rests on this "
        "single agrarian-concentration signal, surfaced low-confidence at the "
        "driver level (docs/METHODS.md).",
    ])))
    register_rows.append(agr_row)

    vol_row = vol_res.register_row(source=WB_SOURCE, series_id="NY.GDP.MKTP.KD.ZG",
                                   license=WB_LICENSE)
    vol_row["year_min"], vol_row["year_max"] = vol_ymin or "", vol_ymax or ""
    vol_row["flags"] = ("; ".join(filter(None, [
        vol_row["flags"],
        f"GAP-FILL (Econ Precarity D5 volatility). STRUCTURAL PROXY = std dev of "
        f"annual real GDP growth over the recent {VOL_WINDOW}-yr window "
        f"(>= {MIN_VOL_OBS} obs). NOT yet residual-scoped net of named hazards "
        f"(docs/METHODS.md, condition c_C) -- residual decomposition "
        f"vs. Structural Disruption is a downstream formula step, flagged not applied.",
    ])))
    register_rows.append(vol_row)

    # NOTE rows: alternate poverty lines (not scored; coverage recorded so the
    # data-stage review can swap the $6.85 line for $2.15 or $3.65).
    def _cov(vals):
        n = sum(1 for c in countries if c in vals)
        return n, round(100.0 * n / len(countries), 1)

    ddy_n, ddy_pct = _cov(ddy_vals)
    lmic_n, lmic_pct = _cov(lmic_vals)
    register_rows.append({
        "indicator": "ep_poverty_headcount_215_NOTE", "source": WB_SOURCE,
        "series_id": "SI.POV.DDAY", "countries": ddy_n,
        "year_min": ddy_ymin or "", "year_max": ddy_ymax or "", "license": WB_LICENSE,
        "direction": "high_risk", "anchor": "[0,100] % below $2.15/day (2017 PPP)",
        "coverage_pct": ddy_pct,
        "flags": "NOTE-ROW / NOT-SCORED: alternate extreme-poverty line kept so the "
                 "data-stage review can swap the chosen $6.85 (UMIC) line. NOT in the risk table.",
    })
    register_rows.append({
        "indicator": "ep_poverty_headcount_365_NOTE", "source": WB_SOURCE,
        "series_id": "SI.POV.LMIC", "countries": lmic_n,
        "year_min": lmic_ymin or "", "year_max": lmic_ymax or "", "license": WB_LICENSE,
        "direction": "high_risk", "anchor": "[0,100] % below $3.65/day (2017 PPP)",
        "coverage_pct": lmic_pct,
        "flags": "NOTE-ROW / NOT-SCORED: alternate lower-middle-income poverty line "
                 "kept for data-stage review. NOT in the risk table.",
    })

    register.ensure_header(path=str(FRAGMENT_PATH))
    register.upsert_rows(register_rows, path=str(FRAGMENT_PATH))
    print(f"[econprecarity] wrote register fragment {FRAGMENT_PATH} "
          f"({len(register_rows)} rows)")

    for col in col_order:
        m = scored[col].meta
        print(f"   {col:<32} dir={m['direction']:<9} cov={m['coverage_pct']:.1f}% "
              f"({m['n_present']}/{m['n_total']})"
              + ("  [BELOW FLOOR]" if m["below_floor"] else ""))
    print(f"   [note] poverty alt-lines: $2.15 {ddy_pct}% | $3.65 {lmic_pct}%")
    return scored, register_rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Recruitment/Economic-Precarity GAP-signal connector")
    ap.add_argument("--cache", action="store_true",
                    help="Skip the WB live pull; use the prior fresh cache "
                         "(ILOSTAT still pulls live).")
    args = ap.parse_args()
    run(use_cache=args.cache)
