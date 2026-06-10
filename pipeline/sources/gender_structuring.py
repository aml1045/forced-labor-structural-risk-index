"""Recruitment / Gender Structuring -- Layer-2 GAP-signal connector.

Operationalizes the SEX-SYMMETRIC Gender Structuring domain (see docs/METHODS.md;
design decision) -- a Recruitment domain that had NO loaded indicator
(full GAP).

It sources the domain's three generating drivers' conceptual signals from real,
verified, publish-safe series -- per the scoring rules v1 (docs/scoring-rules.md)
-- and writes a 0-1 risk table + provenance/coverage register fragment. Flagged
for review.

--------------------------------------------------------------------------------
SIGNAL -> SOURCE MAP (each verified live, June 2026; none fabricated)
--------------------------------------------------------------------------------
D1  Gendered economic exclusion (operationalized as a GENDERED GAP / RATIO, never
    an absolute level -- anti-double-count vs Economic Precarity; see docs/METHODS.md):
  s1.1  Gendered labour-force-participation GAP
        = World Bank SL.TLF.CACT.MA.ZS  -  SL.TLF.CACT.FE.ZS
          (male minus female LFP rate, % of pop 15+, modelled ILO estimate)
          -> percentage-point gap; larger gap = more gendered exclusion = risk.
  s1.2  Composite gendered-inequality gap = UNDP Gender Inequality Index (GII),
        via Our World in Data's republication of the UNDP HDR GII table.
          GII is itself a gendered-GAP composite (reproductive health, empowerment,
          labour market -- all sex-disaggregated), native 0-1, higher = more
          inequality = risk. Used as the multi-dimensional gendered-gap signal that
          a raw LFP gap alone under-captures.

D2  Patriarchal constraint on autonomy & mobility (carried BEHIND the Cho
    mobility-enablement gate -- this connector delivers the STANDING constraint
    signal; the c_mob gate is a DOWNSTREAM formula step, NOT applied
    here; see docs/METHODS.md):
  s2.2  Standing legal constraint on women's mobility
        = World Bank WBL GD_WBL_MOB_LAW_T (Women, Business and the Law -- Legal
          Framework, Mobility score, scale 0-100). Codifies whether a woman may
          choose where to live / leave the marital home / travel domestically &
          internationally on equal terms. Higher score = MORE legal mobility freedom
          = LESS constraint -> low_risk (inverted). This is the codified, near-
          universal-coverage standing norm/constraint signal the findings call for.

D3  Gendered exposure-channelling pipeline (the domain's LEAST-redundant,
    STRONGEST generating driver -- see docs/METHODS.md):
  s3.2  Structural size of the gendered exploitation-exposed channels, BOTH SEXES
        = ILOSTAT EMP_TEMP_SEX_ECO_NB_A (employment by sex x economic activity,
          thousands), from which this connector computes two STRUCTURAL composition
          shares:
            (a) MALE channel  = (AGR + CON + MAN) / male total employment
                = men's concentration in exit-poor high-risk male-dominated sectors
                  (agriculture, construction, manufacturing).
            (b) FEMALE channel = (AGR + domestic work ISIC4_T) / female total
                = women's concentration in isolated/privatized exploitation-exposed
                  work (subsistence agriculture + paid domestic work in households).
          The SCORED s3.2 signal is the MAX of the two channel shares (the larger
          gendered channel on either side -> higher risk; symmetric by construction).
          Direction: larger channel = higher risk.

--------------------------------------------------------------------------------
ANTI-CIRCULARITY (LOAD-BEARING -- see docs/METHODS.md):
  The scored s3.2 signal is the STRUCTURAL sex x sector EMPLOYMENT COMPOSITION
  (ILOSTAT employment shares) -- NEVER the GEMS 2021 prevalence / realized victim
  count. GEMS (ILO, Walk Free & IOM 2022) grounds the *mechanism* (men and women
  are structurally sorted into different exploitation-exposed sectors) ONLY; it is
  NOT pulled, NOT scored, NOT in this table. No trafficking / forced-labour
  prevalence series is mapped anywhere in this module.

S3.3 DE-DUP FLAG (carried, NOT resolved -- pending decision):
  s3.3 (gendered migration channelling into exploitation-exposed corridors) sits on
  the seam with the Constrained Mobility domain. It is NOT independently sourced
  here (no clean publish-safe sex-disaggregated migration-corridor-by-sector panel
  exists at 195-country scale). It is carried as a
  de-dup flag, not scored. D3 rests on s3.2 (the structural sex x sector
  composition) -- a single-signal driver, surfaced low-confidence per rule 9.

SHARED GOVERNANCE / SOCIAL-PROTECTION MODULATOR (Z1) -- scored ONCE, NOT here:
  The shared general-governance dial (Z1; see docs/METHODS.md) is the score-once
  WGI/V-Dem backbone already wired (wb_wgi_rule_of_law / v2x_rule). It is applied
  at domain assembly, NOT in this source table; de-duplicated downstream. This
  module does NOT pull a separate governance index. Z2 (women's legal rights) is
  conditional on the downstream collinearity check and is NOT stacked here.

--------------------------------------------------------------------------------
STANDARDIZE per scoring-rules v1 (docs/scoring-rules.md):
  rule 1  0-1 per-exposure / gendered-gap absolute-anchored scale (anchors below
          are PRIORS -- flagged for review, not locked).
  rule 9  drop-and-re-average with >=50%/>=2 floor; never missing->0; flag
          below-floor. anchor_scale handles missing/floors; D3 single-signal driver
          is surfaced as a coverage caution.
  direction set explicitly per signal.

Reuses (never re-derives): pipeline.iso_utils, pipeline.standardize,
pipeline.register. Live pull by default; never reports done on a failing pull.

Run:  python -m pipeline.sources.gender_structuring
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

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = _REPO_ROOT / "data" / "processed" / "gender_structuring.csv"
FRAGMENT_PATH = (
    _REPO_ROOT / "config" / "data_register.d" / "gender_structuring.csv"
)

WB_SOURCE = "World Bank (Gender Statistics / WDI / Women Business and the Law)"
WB_LICENSE = "CC BY 4.0"
ILO_SOURCE = "ILOSTAT (ILO, rplumber API)"
ILO_LICENSE = "CC BY 4.0 (ILO open data)"
GII_SOURCE = (
    "UNDP Human Development Report -- Gender Inequality Index "
    "(via Our World in Data republication)"
)
GII_LICENSE = (
    "UNDP HDR (publish with attribution); OWID data CC BY 4.0 "
    "-- confirm UNDP HDR re-publication terms before public release"
)

WB_API_BASE = "https://api.worldbank.org/v2"
ILO_API_BASE = "https://rplumber.ilo.org/data/indicator"
GII_CSV_URL = (
    "https://ourworldindata.org/grapher/"
    "gender-inequality-index-from-the-human-development-report.csv?csvType=full"
)
PER_PAGE = 20000
TIMEOUT = 180
RETRIES = 1

# rplumber 403s the default urllib UA -- custom UA REQUIRED (ported pattern).
_ILO_HEADERS = {
    "User-Agent": "FLSRI-pipeline/1.0 (academic research)",
    "Accept": "text/csv,application/octet-stream,*/*",
}
_OWID_HEADERS = {"User-Agent": "FLSRI-pipeline/1.0 (academic research)"}

_PRIOR_ANCHOR_FLAG = (
    "PRIOR-ANCHOR: floor/ceiling a starting point only -- flagged for methods "
    "review, not locked"
)

# ILOSTAT employment-by-sex-by-sector: aggregate sector codes for the channels.
SEC_AGR = "ECO_AGGREGATE_AGR"   # agriculture
SEC_CON = "ECO_AGGREGATE_CON"   # construction
SEC_MAN = "ECO_AGGREGATE_MAN"   # manufacturing
SEC_DOM = "ECO_ISIC4_T"         # households as employers (paid domestic work)
SEC_TOTAL = "ECO_AGGREGATE_TOTAL"


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CTX = _ssl_context()


# --------------------------------------------------------------------------
# Anchors (LOCKED rule 1). Directions set explicitly.
# --------------------------------------------------------------------------

LFP_GAP_SPEC = AnchorSpec(
    indicator="gs_lfp_gender_gap",
    floor=0.0, ceiling=50.0,
    direction="high_risk",
    unit="male-minus-female labour-force-participation gap (pct points, pop 15+)",
    anchor_source=(
        "Gendered LFP GAP = male LFP rate (SL.TLF.CACT.MA.ZS) minus female LFP rate "
        "(SL.TLF.CACT.FE.ZS), modelled ILO estimate. A GENDERED GAP (not a level) per "
        "docs/METHODS.md -- avoids re-scoring the absolute participation level Economic "
        "Precarity reads. Per-exposure by construction (a difference of rates). Floor 0 "
        "= no gendered participation gap (min risk); ceiling 50 pct points ~ the high "
        "end of observed national gaps (the most gender-segregated labour markets reach "
        "~40-55pp male-over-female) so highly-exclusionary regimes saturate at 1.0. "
        "Negative gaps (female>male LFP, a few countries) clamp to 0. Direction "
        "high_risk: larger male-over-female gap = more gendered exclusion. "
        + _PRIOR_ANCHOR_FLAG
    ),
)

GII_SPEC = AnchorSpec(
    indicator="gs_gender_inequality_index",
    floor=0.0, ceiling=1.0,
    direction="high_risk",
    unit="UNDP Gender Inequality Index (0 = equal, 1 = maximally unequal)",
    anchor_source=(
        "UNDP Gender Inequality Index -- a multi-dimensional gendered-GAP composite "
        "(reproductive health: maternal mortality + adolescent births; empowerment: "
        "parliamentary seats + secondary education; labour market: LFP -- all "
        "sex-disaggregated). Native 0-1 by construction -> full-range absolute anchor "
        "(rule 1; NOT a relative fallback). Direction high_risk: higher GII = more "
        "gendered inequality = more risk. Operationalized as the multi-dimensional "
        "gendered-gap signal a raw LFP gap under-captures (see docs/METHODS.md). " + _PRIOR_ANCHOR_FLAG
    ),
)

WBL_MOBILITY_SPEC = AnchorSpec(
    indicator="gs_mobility_constraint",
    floor=0.0, ceiling=100.0,
    direction="low_risk",   # higher legal-mobility score = less constraint = LESS risk
    unit="WBL Legal Framework, Mobility score (0-100)",
    anchor_source=(
        "World Bank Women, Business and the Law -- Legal Framework, Mobility score "
        "(GD_WBL_MOB_LAW_T, scale 0-100): whether a woman may choose where to live, "
        "leave the marital home, travel domestically and internationally on equal legal "
        "terms with a man. A CODIFIED standing constraint signal (s2.2; see docs/METHODS.md). "
        "Native 0-100 -> full-range absolute anchor. Direction LOW_RISK (inverted): a "
        "HIGH legal-mobility score = LOW patriarchal mobility constraint = LOW risk, so "
        "0 (no legal mobility freedom) -> risk 1, 100 -> risk 0. CHO GATE NOT APPLIED: "
        "the mobility-enablement condition c_mob that gates this standing "
        "constraint is a DOWNSTREAM formula step -- this connector delivers the raw "
        "standing-constraint signal only. " + _PRIOR_ANCHOR_FLAG
    ),
)

CHANNEL_SPEC = AnchorSpec(
    indicator="gs_sex_sector_channel_share",
    floor=0.0, ceiling=0.70,
    direction="high_risk",
    unit="max(sex-specific exploitation-exposed-sector employment share), 0-1",
    anchor_source=(
        "STRUCTURAL sex x sector employment composition (ILOSTAT EMP_TEMP_SEX_ECO_NB_A, "
        "employment by sex and aggregate economic activity, thousands). Computes, per "
        "country, two channel shares: MALE = (agriculture + construction + "
        "manufacturing) / male total employment; FEMALE = (agriculture + paid domestic "
        "work ISIC4_T) / female total employment. The scored signal is the MAX of the "
        "two -- the larger gendered exploitation-exposed channel on EITHER side "
        "(sex-symmetric by construction; s3.2, see docs/METHODS.md). A per-exposure share by "
        "construction. Floor 0 = no concentration in the exposed channels; ceiling 0.70 "
        "near the observed high tail (male channel reaches ~0.81, p90 ~0.62; female AGR+"
        "domestic reaches ~0.93, p90 ~0.59) so highly-channelled labour markets saturate "
        "at 1.0. Direction high_risk: larger gendered channel = more structural pickability. "
        "ANTI-CIRCULARITY (LOAD-BEARING): this is the STRUCTURAL employment composition, "
        "NEVER the GEMS 2021 prevalence/victim count -- GEMS grounds the mechanism only "
        "and is NOT pulled/scored. " + _PRIOR_ANCHOR_FLAG
    ),
)


# --------------------------------------------------------------------------
# World Bank pull (auth-free HTTP + pagination; ported pattern).
# --------------------------------------------------------------------------

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


def _wb_pull_series(code: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        url = (f"{WB_API_BASE}/country/all/indicator/{code}"
               f"?format=json&per_page={PER_PAGE}&page={page}")
        body = _http_get_json(url)
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


def _wb_most_recent(rows: list[dict]):
    """{iso3: value} most-recent non-NaN year, normalized to ISO3; + year span."""
    best: dict = {}
    for r in rows:
        val = r.get("value")
        if val in (None, ""):
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


# --------------------------------------------------------------------------
# UNDP GII via OWID republication.
# --------------------------------------------------------------------------

def _gii_pull():
    """{iso3: GII}, most-recent year per country; + year span."""
    last = None
    raw = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(GII_CSV_URL, headers=_OWID_HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
                raw = resp.read().decode("utf-8-sig", "replace")
            break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            if attempt < RETRIES:
                time.sleep(2)
    if raw is None:
        raise RuntimeError(f"GII (OWID) HTTP failed after {RETRIES + 1} attempts: {last}")
    rows = list(csv.DictReader(io.StringIO(raw)))
    if not rows:
        return {}, None, None
    valcol = "Gender Inequality Index"
    best: dict = {}
    for r in rows:
        code = (r.get("Code") or "").strip()
        v = r.get(valcol)
        if not code or v in (None, ""):
            continue
        try:
            v = float(v)
            yr = int(str(r.get("Year")).strip())
        except (TypeError, ValueError):
            continue
        iso3 = iso_utils.normalize_to_iso3(code)  # drops OWID region aggregates
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
# ILOSTAT employment-by-sex-by-sector pull + channel-share computation.
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


def _latest_by_area_sex_sector(rows, sex, sector):
    """{ref_area_raw: value} most-recent year for one (sex, sector) cell."""
    best: dict = {}
    for r in rows:
        if r.get("sex") != sex or r.get("classif1") != sector:
            continue
        v = r.get("obs_value")
        if v in (None, ""):
            continue
        try:
            v = float(v)
            yr = int(float(r.get("time")))
        except (TypeError, ValueError):
            continue
        a = r.get("ref_area")
        if not a:
            continue
        cur = best.get(a)
        if cur is None or yr > cur[0]:
            best[a] = (yr, v)
    return best  # raw-area -> (year, value)


def _channel_shares(rows):
    """Return ({iso3: max-channel-share}, year_min, year_max).

    Male channel  = (AGR+CON+MAN)/male total. Female channel = (AGR+domestic)/female
    total. Scored signal = max of the two present channels (sex-symmetric: the larger
    gendered exploitation-exposed channel on either side). A country needs at least
    ONE complete channel (its total > 0 and its component sectors present); domestic
    work (ISIC4_T) is added to the female channel only where reported (else AGR alone,
    flagged). Most-recent year per cell.
    """
    mtot = _latest_by_area_sex_sector(rows, "SEX_M", SEC_TOTAL)
    magr = _latest_by_area_sex_sector(rows, "SEX_M", SEC_AGR)
    mcon = _latest_by_area_sex_sector(rows, "SEX_M", SEC_CON)
    mman = _latest_by_area_sex_sector(rows, "SEX_M", SEC_MAN)
    ftot = _latest_by_area_sex_sector(rows, "SEX_F", SEC_TOTAL)
    fagr = _latest_by_area_sex_sector(rows, "SEX_F", SEC_AGR)
    fdom = _latest_by_area_sex_sector(rows, "SEX_F", SEC_DOM)

    areas = set(mtot) | set(ftot)
    out: dict = {}
    used_years: list[int] = []
    for a in areas:
        iso3 = iso_utils.normalize_to_iso3(a)
        if not iso3:
            continue
        channels = []
        yrs = []
        # male channel
        if a in mtot and mtot[a][1] > 0 and a in magr and a in mcon and a in mman:
            m = (magr[a][1] + mcon[a][1] + mman[a][1]) / mtot[a][1]
            channels.append(m)
            yrs.extend([mtot[a][0], magr[a][0], mcon[a][0], mman[a][0]])
        # female channel (domestic added where present, else AGR alone)
        if a in ftot and ftot[a][1] > 0 and a in fagr:
            dom = fdom[a][1] if a in fdom else 0.0
            f = (fagr[a][1] + dom) / ftot[a][1]
            channels.append(f)
            yrs.extend([ftot[a][0], fagr[a][0]] + ([fdom[a][0]] if a in fdom else []))
        if not channels:
            continue
        # a share can exceed 1 only via reporting mismatch across cells; clamp defensively
        val = min(1.0, max(channels))
        prev = out.get(iso3)
        if prev is None or val > prev:  # keep the larger if an area maps twice
            out[iso3] = val
        used_years.extend(yrs)
    if not used_years:
        return {}, None, None
    return out, min(used_years), max(used_years)


# --------------------------------------------------------------------------
# Run.
# --------------------------------------------------------------------------

def run():
    countries = iso_utils.load_sample()
    n_total = len(countries)

    # --- D1.s1.1 -- gendered LFP gap (WB male minus female) ---
    try:
        male_lfp = _wb_pull_series("SL.TLF.CACT.MA.ZS")
        female_lfp = _wb_pull_series("SL.TLF.CACT.FE.ZS")
    except Exception as e:
        sys.exit(f"[gender] WB LFP pull failed ({e}) -- ABORT (never report done on a failing pull).")
    male_v, m_ymin, m_ymax = _wb_most_recent(male_lfp)
    female_v, f_ymin, f_ymax = _wb_most_recent(female_lfp)
    gap_vals = {}
    for iso3 in set(male_v) & set(female_v):
        gap_vals[iso3] = male_v[iso3] - female_v[iso3]
    if not gap_vals:
        sys.exit("[gender] WB LFP gap produced no rows -- ABORT.")
    lfp_ymin = min(x for x in [m_ymin, f_ymin] if x is not None)
    lfp_ymax = max(x for x in [m_ymax, f_ymax] if x is not None)
    lfp_res = anchor_scale(gap_vals, LFP_GAP_SPEC, sample=countries)

    # --- D1.s1.2 -- UNDP GII (OWID republication) ---
    try:
        gii_vals, gii_ymin, gii_ymax = _gii_pull()
    except Exception as e:
        sys.exit(f"[gender] GII pull failed ({e}) -- ABORT.")
    if not gii_vals:
        sys.exit("[gender] GII returned no usable rows -- ABORT.")
    gii_res = anchor_scale(gii_vals, GII_SPEC, sample=countries)

    # --- D2.s2.2 -- WBL mobility legal constraint (WB) ---
    try:
        wbl_rows = _wb_pull_series("GD_WBL_MOB_LAW_T")
    except Exception as e:
        sys.exit(f"[gender] WBL mobility pull failed ({e}) -- ABORT.")
    wbl_vals, wbl_ymin, wbl_ymax = _wb_most_recent(wbl_rows)
    if not wbl_vals:
        sys.exit("[gender] WBL mobility returned no usable rows -- ABORT.")
    wbl_res = anchor_scale(wbl_vals, WBL_MOBILITY_SPEC, sample=countries)

    # --- D3.s3.2 -- structural sex x sector channel composition (ILOSTAT) ---
    try:
        ilo_rows = _ilo_pull("EMP_TEMP_SEX_ECO_NB_A")
    except Exception as e:
        sys.exit(f"[gender] ILOSTAT sex x sector pull failed ({e}) -- ABORT.")
    chan_vals, chan_ymin, chan_ymax = _channel_shares(ilo_rows)
    if not chan_vals:
        sys.exit("[gender] ILOSTAT channel-share computation produced no rows -- ABORT.")
    chan_res = anchor_scale(chan_vals, CHANNEL_SPEC, sample=countries)

    # --- write data/processed table ---
    scored = {
        "gs_lfp_gender_gap": lfp_res,
        "gs_gender_inequality_index": gii_res,
        "gs_mobility_constraint": wbl_res,
        "gs_sex_sector_channel_share": chan_res,
    }
    col_order = ["gs_lfp_gender_gap", "gs_gender_inequality_index",
                 "gs_mobility_constraint", "gs_sex_sector_channel_share"]
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
    print(f"\n[gender] wrote {OUT_PATH} ({n_total} rows x {len(col_order)} risk indicators)")

    # --- register fragment ---
    rows = []

    lfp_row = lfp_res.register_row(
        source=WB_SOURCE,
        series_id="SL.TLF.CACT.MA.ZS - SL.TLF.CACT.FE.ZS (male-minus-female LFP)",
        license=WB_LICENSE)
    lfp_row["year_min"], lfp_row["year_max"] = lfp_ymin, lfp_ymax
    lfp_row["flags"] = "; ".join(filter(None, [
        lfp_row["flags"],
        "GAP-SOURCE (Gender Structuring D1.s1.1). GENDERED GAP, not a level "
        "(anti-double-count vs Economic Precarity; see docs/METHODS.md). Modelled ILO LFP "
        "estimates differenced; negative gaps (female>male) clamp to 0. "
        "Screen D1 collinearity vs Economic Precarity (see docs/METHODS.md).",
    ]))
    rows.append(lfp_row)

    gii_row = gii_res.register_row(
        source=GII_SOURCE, series_id="UNDP HDR Gender Inequality Index (OWID full CSV)",
        license=GII_LICENSE)
    gii_row["year_min"], gii_row["year_max"] = gii_ymin, gii_ymax
    gii_row["flags"] = "; ".join(filter(None, [
        gii_row["flags"],
        "GAP-SOURCE (Gender Structuring D1.s1.2). UNDP GII = multi-dimensional "
        "gendered-gap composite, native 0-1 absolute anchor. LICENSE-VERIFY: UNDP HDR "
        "re-publication terms UNCONFIRMED for the public index -- confirm before release "
        "(OWID layer is CC BY 4.0). GII embeds a labour-market LFP component "
        "-- screen collinearity with gs_lfp_gender_gap (partial within-D1 overlap) and "
        "vs Economic Precarity.",
    ]))
    rows.append(gii_row)

    wbl_row = wbl_res.register_row(
        source=WB_SOURCE, series_id="GD_WBL_MOB_LAW_T (WBL Legal Framework, Mobility score)",
        license=WB_LICENSE)
    wbl_row["year_min"], wbl_row["year_max"] = wbl_ymin, wbl_ymax
    wbl_row["flags"] = "; ".join(filter(None, [
        wbl_row["flags"],
        "GAP-SOURCE (Gender Structuring D2.s2.2). Standing codified mobility-constraint "
        "signal; direction LOW_RISK (high legal-mobility score = low constraint, "
        "inverted). CHO-GATE-NOT-APPLIED: the c_mob mobility-enablement condition "
        "(the Cho 2015 non-monotonicity gate) is a DOWNSTREAM formula step "
        "-- this connector delivers the raw standing-constraint signal only; D2 must NOT "
        "enter linearly. Screen D2 vs Constrained Mobility collinearity (see docs/METHODS.md).",
    ]))
    rows.append(wbl_row)

    chan_row = chan_res.register_row(
        source=ILO_SOURCE, series_id="EMP_TEMP_SEX_ECO_NB_A (sex x economic activity)",
        license=ILO_LICENSE)
    chan_row["year_min"], chan_row["year_max"] = chan_ymin, chan_ymax
    chan_row["flags"] = "; ".join(filter(None, [
        chan_row["flags"],
        "GAP-SOURCE (Gender Structuring D3.s3.2, the domain's strongest generating "
        "driver). STRUCTURAL sex x sector EMPLOYMENT composition = max(male (AGR+CON+MAN)/"
        "male-total, female (AGR+domestic ISIC4_T)/female-total); sex-symmetric. "
        "ANTI-CIRCULARITY (LOAD-BEARING): scored value is the STRUCTURAL employment "
        "share, NEVER the GEMS 2021 prevalence/victim count -- GEMS (ILO/WalkFree/IOM "
        "2022) grounds the mechanism ONLY and is NOT pulled/scored; no trafficking/"
        "forced-labour prevalence series is mapped here. FEMALE-DOMESTIC-PARTIAL: paid "
        "domestic work (ISIC4_T) added to the female channel only where reported "
        "(~172/195); elsewhere the female channel = agriculture alone (understates the "
        "isolated-domestic channel) -- surfaced, not imputed. D3 is a SINGLE-SIGNAL "
        "driver here (s3.1 unpaid-care time-use + s3.3 migration-channelling not sourced) "
        "-- low-confidence per rule 9.",
    ]))
    rows.append(chan_row)

    # --- carried NOTE rows: unsourced signals + de-dup + governance (NOT scored) ---
    rows.append({
        "indicator": "gs_s3_3_migration_channel_DEDUP_NOTE", "source": "(carried flag)",
        "series_id": "n/a (note row)", "countries": 0, "year_min": "", "year_max": "",
        "license": "n/a", "direction": "high_risk (intended)", "anchor": "n/a (note row)",
        "coverage_pct": 0.0,
        "flags": "NOTE-ROW / S3.3 DE-DUP CARRIED (pending decision): the "
                 "gendered migration-channelling signal (sex-disaggregated outflow into "
                 "exploitation-exposed corridors) overlaps Constrained Mobility. NOT "
                 "independently scored -- no publish-safe 195-country sex x "
                 "corridor-by-sector panel exists. Carried as a de-dup flag, "
                 "NOT resolved here. Pending decision: gender migration-channel by sector.",
    })
    rows.append({
        "indicator": "gs_s3_1_unpaid_care_NOTE", "source": "(unsourced -- pending decision)",
        "series_id": "n/a (note row)", "countries": 0, "year_min": "", "year_max": "",
        "license": "n/a", "direction": "high_risk (intended)", "anchor": "n/a (note row)",
        "coverage_pct": 0.0,
        "flags": "NOTE-ROW / UN-GROUNDABLE at 195 scale: s3.1 gendered "
                 "unpaid-care burden (female-to-male time-use ratio) has NO publish-safe "
                 "~195-country panel (UN time-use surveys are sparse/intermittent; "
                 "SDG 5.4.1 covers ~90 countries below the floor). NOT substituted "
                 "silently. D3 falls back to s3.2 alone (low-confidence). Pending "
                 "decision: unpaid-care time-use gender source.",
    })
    rows.append({
        "indicator": "gs_governance_modulator_Z1_NOTE",
        "source": "SHARED governance backbone (wb_wgi_rule_of_law / v2x_rule) -- NOT re-pulled",
        "series_id": "n/a (note row -- downstream formula reference)", "countries": 0,
        "year_min": "", "year_max": "", "license": "n/a",
        "direction": "attenuate-only (1 - f_gov)", "anchor": "n/a (note row)",
        "coverage_pct": 0.0,
        "flags": "NOTE-ROW / SHARED-SCORED-ONCE: Z1 is the single shared "
                 "general-governance/social-protection dial, placed ONCE at domain "
                 "assembly (rules 7-8; see docs/scoring-rules.md). This connector does NOT pull a "
                 "separate governance index -- de-duplicated downstream against the existing "
                 "wb_wgi_rule_of_law / v2x_rule columns. Z2 (women's legal rights) is "
                 "CONDITIONAL on the downstream collinearity check and is NOT stacked here.",
    })

    register.ensure_header(path=str(FRAGMENT_PATH))
    register.upsert_rows(rows, path=str(FRAGMENT_PATH))
    print(f"[gender] wrote register fragment {FRAGMENT_PATH} ({len(rows)} rows)")

    for col in col_order:
        m = scored[col].meta
        print(f"   {col:<32} dir={m['direction']:<9} cov={m['coverage_pct']:.1f}% "
              f"({m['n_present']}/{m['n_total']})"
              + ("  [BELOW FLOOR]" if m["below_floor"] else ""))
    return scored, rows


if __name__ == "__main__":
    argparse.ArgumentParser(
        description="Recruitment/Gender-Structuring GAP-signal connector").parse_args()
    run()
