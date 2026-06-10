"""Legal Non-Recognition connector -- Layer-2 data map (Recruitment phase).

Domain: recruitment/legal-non-recognition (design decision). Maps the
domain's conceptual signals to real, groundable datasets, standardizes each to a
0-1 per-exposure absolute-anchored risk scale (scoring rule 1; see
docs/scoring-rules.md), records direction + coverage + provenance (rule 9 /
register), and writes data/processed/legal_non_recognition.csv.

The construct carries TWO generating drivers, each one signal (D3 dropped):

  D1 . Uncountability  -- S1.1 Civil-/birth-registration incompleteness
        THE single scored home for birth-registration (ceded by Age/Childhood
        Structuring and Ascriptive Exclusion; see docs/METHODS.md). This is the
        domain backbone.
        -> World Bank WDI  SP.REG.BRTH.ZS
           "Completeness of birth registration (%)" (UNICEF/UNSD CRVS series,
           SDG 16.9.1 base, mirrored on WDI; survey-derived MICS/DHS + admin).
           DIRECTION: completeness is a PROTECTIVE rate, so direction=low_risk
           (HIGH completeness = LOW risk); the standardizer inverts it so the
           RISK signal is INCOMPLETENESS (share NOT registered). Anchored
           absolutely: 100% completeness = 0 risk (universal registration floor),
           0% = 1 risk. Per scoring rule 1 (per-exposure share, absolute anchor).
           Coverage ~178/195; the ~17 missing are mostly high-income / small
           states with de-facto-universal registration but no survey -- a known
           NON-RANDOM missingness. Per rule 9 these are DROPPED, NOT
           imputed to 100% (never silently fabricate "registered"); the domain
           is low-confidence there. Flagged, not papered over.

  D2 . Statelessness & absent legal status -- S2.1 Statelessness prevalence
        -> UNHCR stateless persons, ALREADY PULLED + standardized by the UNHCR
           connector as column `unhcr_stateless_by_country` in
           data/processed/unhcr.csv (per-capita per-100k, anchored 0..5000/100k,
           direction high_risk). This module RE-USES that column rather than
           re-pulling -- the data-stage correlation screen de-duplicates a shared
           series; re-pulling UNHCR here would double-own it. We read the existing
           0-1 column.
        NON-RANDOM-MISSINGNESS (see docs/METHODS.md, Strode et al. 2021): the
           states/groups most excluded are precisely the least counted, so an
           ABSENT statelessness figure must NOT read as "no stateless people".
           Per rule 9 it is DROPPED, never -> 0; opacity can never lower
           the score. The UNHCR connector reports a stateless value (often 0 for
           a genuine no-known-stateless reading) for in-sample countries; where
           the underlying figure is suppressed/absent the value is blank and is
           dropped here, with a low-confidence flag (see SUPPRESSION handling in
           the domain-aggregation step below).

  SHARED GOVERNANCE MODULATOR M1 (grounding-flagged):
        A SINGLE shared general-governance dial, placed ONCE at the domain
        level (see docs/METHODS.md). It is the SAME governance variance scored
        once across the whole index (wb_wgi_rule_of_law / v2x_rule, already wired
        by the worldbank / vdem connectors). Per the score-once rule + data-stage
        de-duplication, this module DOES NOT pull a separate governance index.
        M1's SPECIFIC governance x legal-non-recognition interaction is
        GROUNDING-FLAGGED -- an assumption pending a data-stage source
        (pending decision; see docs/METHODS.md). The final (1 - f_gov)
        attenuation is a downstream aggregation-formula step that references the
        existing governance column; it is NOT applied to the raw signals here,
        and is recorded as a note row, not re-pulled.

DOMAIN SCORE (rule-5 equal-weight average of D1, D2; rule-9 drop-and-re-average):
        LNR_raw = mean(present of {D1=birthreg_incompleteness, D2=statelessness})
        with the >= 50% / min-1-of-2 coverage floor (2-driver domain, so >=1
        present clears the floor but a single-driver domain is surfaced as a
        coverage caution). A SUPPRESSED/absent statelessness figure is DROPPED,
        never zeroed; the row is marked low-confidence wherever D2 is dropped.
        f_gov is NOT applied here (downstream formula; grounding-flagged).

S1.1 is pulled live from the World Bank WDI API (auth-free, CC BY 4.0), reusing
the live-pull HTTP pattern from pipeline/sources/age_childhood.py +
worldbank.py (pagination, certifi SSL, retry, most-recent-year reduction).

Run:  python -m pipeline.sources.legal_non_recognition
      python -m pipeline.sources.legal_non_recognition --cache   # WB cache fallback
"""

from __future__ import annotations

from pathlib import Path
import argparse
import csv
import json
import math
import ssl
import sys
import time
import urllib.error
import urllib.request

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale, drop_and_average

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = _REPO_ROOT / "data" / "processed" / "legal_non_recognition.csv"
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "legal_non_recognition.csv"

# D2 statelessness is RE-USED from the UNHCR connector's processed table
# (data-stage correlation de-dup; not re-pulled here).
_UNHCR_PROCESSED = _REPO_ROOT / "data" / "processed" / "unhcr.csv"
_STATELESS_COL = "unhcr_stateless_by_country"

# Prior fresh WB cache (vintage 2026-05-28) as a network fallback. Schema:
# iso3, country_name, year, series, value. The birth-registration series is not
# in the prior 7-series cache, so --cache typically yields nothing for it; the
# live pull is the primary path.
_CACHE_FALLBACK = _REPO_ROOT / "data" / "aux" / "worldbank_cache.csv"

SOURCE_WB = "World Bank WDI (UNICEF/UNSD CRVS / SDG 16.9.1 birth-registration completeness)"
SOURCE_UNHCR = "UNHCR Population Statistics API (re-used via data/processed/unhcr.csv)"
LICENSE_WB = "CC BY 4.0"
LICENSE_UNHCR = "Open / CC BY (attribution required)"

API_BASE = "https://api.worldbank.org/v2"
PER_PAGE = 20000
TIMEOUT = 40
RETRIES = 1
# Wide window: birth-registration completeness is an infrequent survey stock;
# most-recent non-NaN year per country is taken.
DATE_FROM = 2000
DATE_TO = 2024

# --- D1 signal spec (birth-registration incompleteness) --------------------
# Direction low_risk: completeness is a PROTECTIVE rate (high = low risk); the
# standardizer inverts, so the RISK signal is the un-registered share.
# Absolute anchor: floor=100 (universal registration -> 0 risk),
# ceiling=0 (none registered -> 1 risk). Standards-anchored: near-universal
# birth registration is the explicit target (SDG 16.9.1 "legal identity for all").
D1_SPEC = AnchorSpec(
    indicator="lnr_birth_registration_incompleteness",
    floor=0.0, ceiling=100.0, direction="low_risk",
    unit="% of births registered (completeness)",
    anchor_source=(
        "Birth-registration completeness (WDI SP.REG.BRTH.ZS; UNICEF/UNSD CRVS, "
        "SDG 16.9.1 base). PROTECTIVE rate -> direction=low_risk (standardizer "
        "inverts ONCE): risk signal is INCOMPLETENESS (un-registered share). "
        "Absolute anchor on the raw completeness scale floor=0%/ceiling=100%; after "
        "the low_risk inversion 100% completeness = 0 risk (universal-registration "
        "floor, the SDG 16.9.1 'legal identity for all' target) and 0% = 1 risk. "
        "Per-exposure share, per scoring rule 1 (docs/scoring-rules.md). "
        "ANCHOR-PRIOR: re-examine at data-stage review."
    ),
)
_D1_CODE = "SP.REG.BRTH.ZS"

# --------------------------------------------------------------------------
# Live WDI pull (ported from pipeline/sources/age_childhood.py / worldbank.py).
# --------------------------------------------------------------------------

def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CTX = _ssl_context()


def _api_url(code: str, page: int) -> str:
    return (f"{API_BASE}/country/all/indicator/{code}"
            f"?format=json&per_page={PER_PAGE}&page={page}"
            f"&date={DATE_FROM}:{DATE_TO}")


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


def _pull_series_live(code: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        body = _http_get_json(_api_url(code, page))
        if not isinstance(body, list) or len(body) < 2:
            raise RuntimeError(f"{code}: unexpected response shape from WB API")
        meta, data = body[0], body[1]
        if not isinstance(data, list):
            data = []
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


def _load_d1_from_cache() -> list[dict]:
    if not _CACHE_FALLBACK.exists():
        raise FileNotFoundError(f"cache fallback not found: {_CACHE_FALLBACK}")
    out = []
    with open(_CACHE_FALLBACK, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("series") == _D1_CODE:
                out.append({
                    "iso3": row.get("iso3") or "",
                    "year": row.get("year", ""),
                    "series": _D1_CODE,
                    "value": row.get("value", None),
                })
    return out


def _most_recent_by_iso3(rows: list[dict]):
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


# --------------------------------------------------------------------------
# D2 statelessness -- re-use the UNHCR connector's standardized column.
# --------------------------------------------------------------------------

def _load_d2_from_unhcr(countries) -> dict:
    """Read the already-standardized statelessness 0-1 column from the UNHCR
    processed table (data-stage correlation de-dup; not re-pulled). Returns
    {iso3: 0-1 or None}.

    A BLANK cell is a suppressed/absent figure -> kept as None (dropped by rule 9,
    NEVER coerced to 0). This is the operational form of the Strode et al. 2021
    non-random-missingness handling.
    """
    if not _UNHCR_PROCESSED.exists():
        sys.exit(
            f"[legal_non_recognition] FAILED: statelessness source not found: "
            f"{_UNHCR_PROCESSED}\n  Run `python -m pipeline.sources.unhcr` first "
            f"(D2 re-uses its `{_STATELESS_COL}` column; not re-pulled here)."
        )
    by_iso = {}
    with open(_UNHCR_PROCESSED, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        if _STATELESS_COL not in (rd.fieldnames or []):
            sys.exit(
                f"[legal_non_recognition] FAILED: column `{_STATELESS_COL}` "
                f"absent from {_UNHCR_PROCESSED}."
            )
        for r in rd:
            iso3 = iso_utils.normalize_to_iso3(r.get("iso3"))
            if not iso3:
                continue
            raw = (r.get(_STATELESS_COL) or "").strip()
            if raw == "":
                by_iso[iso3] = None   # suppressed/absent -> dropped, never 0
                continue
            try:
                by_iso[iso3] = float(raw)
            except ValueError:
                by_iso[iso3] = None
    return {iso3: by_iso.get(iso3) for iso3 in countries}


# --------------------------------------------------------------------------
# Run.
# --------------------------------------------------------------------------

def run(use_cache: bool = False):
    countries = iso_utils.load_sample()

    # --- D1: birth-registration incompleteness (pulled + standardized here) --
    d1_rows = None
    if not use_cache:
        try:
            d1_rows = _pull_series_live(_D1_CODE)
            nn = sum(1 for r in d1_rows if r["value"] not in (None, ""))
            print(f"[legal_non_recognition] live pull {_D1_CODE}: "
                  f"{len(d1_rows)} rows ({nn} non-null)")
        except Exception as e:
            print(f"[legal_non_recognition] live pull failed ({e}); trying cache",
                  file=sys.stderr)
    pulled_live = d1_rows is not None
    if d1_rows is None:
        d1_rows = _load_d1_from_cache()
        print(f"[legal_non_recognition] D1 loaded from cache: {_CACHE_FALLBACK}")

    d1_raw, d1_ymin, d1_ymax = _most_recent_by_iso3(d1_rows)
    if not d1_raw:
        sys.exit("[legal_non_recognition] FAILED: no birth-registration rows "
                 "aggregated for D1 -- aborting (never report done on a failing pull).")

    # write-back AFTER validation so a degenerate pull can never replace the
    # cached D1 series (raw_cache additionally refuses all-null series)
    if pulled_live:
        from pipeline import raw_cache
        raw_cache.upsert_series(_CACHE_FALLBACK, {_D1_CODE: d1_rows},
                                source_label="legal_non_recognition (D1 birth registration)")
    d1 = anchor_scale(d1_raw, D1_SPEC, sample=countries)
    m1 = d1.meta
    print(f"[legal_non_recognition] D1 {_D1_CODE} (S1.1 birth-reg incompleteness) "
          f"dir={m1['direction']} coverage {m1['coverage_pct']:.1f}% "
          f"({m1['n_present']}/{m1['n_total']}), years {d1_ymin}-{d1_ymax}, "
          f"below_floor={m1['below_floor']}")

    # --- D2: statelessness (re-used from UNHCR processed table; de-dup) -------
    d2 = _load_d2_from_unhcr(countries)
    d2_present = sum(1 for v in d2.values() if v is not None)
    d2_cov = 100.0 * d2_present / len(countries)
    print(f"[legal_non_recognition] D2 (S2.1 statelessness) RE-USED from "
          f"{_STATELESS_COL}: coverage {d2_cov:.1f}% ({d2_present}/{len(countries)}) "
          f"-- suppressed/absent kept as blank (dropped, never -> 0)")

    # --- domain score: rule-5 equal-weight avg, rule-9 drop-and-re-average ----
    # f_gov (M1) is NOT applied here -- downstream formula step, grounding-flagged.
    domain_scores = {}
    domain_lowconf = {}      # iso3 -> True where below floor OR single-driver fallback
    n_domain_present = 0
    n_single_driver_lowconf = 0
    n_d2_dropped_lowconf = 0
    for iso3 in countries:
        v_d1 = d1.get(iso3)
        v_d2 = d2.get(iso3)
        mean, cov_pct, below = drop_and_average([v_d1, v_d2],
                                                coverage_floor=0.5, min_present=1)
        domain_scores[iso3] = mean
        # Low-confidence wherever the domain rests on a SINGLE driver (either D1 or
        # D2 dropped) -- the documented coverage caution -- or below floor (both
        # missing -> mean is None). Opacity must SURFACE, never lower the score.
        d2_dropped = (v_d2 is None) and (v_d1 is not None)   # statelessness suppressed/absent
        single_driver = (v_d1 is None) != (v_d2 is None)     # exactly one present
        lc = below or single_driver
        domain_lowconf[iso3] = lc
        if mean is not None:
            n_domain_present += 1
        if single_driver:
            n_single_driver_lowconf += 1
        if d2_dropped:
            n_d2_dropped_lowconf += 1
    domain_cov = 100.0 * n_domain_present / len(countries)
    print(f"[legal_non_recognition] DOMAIN LNR_raw (mean D1,D2; rule-9): "
          f"coverage {domain_cov:.1f}% ({n_domain_present}/{len(countries)}); "
          f"{n_single_driver_lowconf} single-driver rows low-confidence "
          f"(of which {n_d2_dropped_lowconf} from a DROPPED/suppressed statelessness "
          f"figure, resting on D1 alone, never zeroed)")

    # --- write data/processed/legal_non_recognition.csv ----------------------
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    cols = ["lnr_birth_registration_incompleteness",
            "lnr_statelessness_prevalence",
            "lnr_domain_raw",
            "lnr_domain_low_confidence"]
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3"] + cols)
        for iso3 in countries:
            v_d1 = d1.get(iso3)
            v_d2 = d2.get(iso3)
            v_dom = domain_scores.get(iso3)
            lc = domain_lowconf.get(iso3, False)
            w.writerow([
                iso3,
                "" if v_d1 is None else round(v_d1, 4),
                "" if v_d2 is None else round(v_d2, 4),
                "" if v_dom is None else round(v_dom, 4),
                "1" if lc else "0",
            ])
    print(f"[legal_non_recognition] wrote {OUT_PATH} "
          f"({len(countries)} rows x {len(cols)} cols)")

    # --- register fragment ----------------------------------------------------
    register_rows = []

    # D1 row (owned + scored here)
    d1_row = d1.register_row(
        source=SOURCE_WB, series_id=_D1_CODE, license=LICENSE_WB,
        extra_flags=[
            "SINGLE-HOME: this is the SOLE scored home for birth-registration "
            "completeness (ceded by Age/Childhood Structuring + Ascriptive "
            "Exclusion; docs/METHODS.md). Do NOT score it in a sibling domain.",
            "NON-RANDOM-MISSING: ~17 mostly high-income/small states lack a survey "
            "value (de-facto-universal registration). Per rule 9 they are DROPPED, "
            "NOT imputed to 100% -- never fabricate 'registered'; low-confidence there.",
            "ANCHOR-PRIOR: raw scale floor=0%/ceiling=100%, direction low_risk "
            "inverts ONCE so 100%=0 risk and 0%=1 risk; risk signal is un-registered "
            "share. Re-examine at data-stage review.",
        ],
    )
    d1_row["year_min"] = d1_ymin if d1_ymin is not None else ""
    d1_row["year_max"] = d1_ymax if d1_ymax is not None else ""
    register_rows.append(d1_row)

    # D2 row (re-used from UNHCR; recorded for provenance, NOT re-owned)
    d2_below = (d2_present / len(countries) < 0.5) or (d2_present < 2)
    register_rows.append({
        "indicator": "lnr_statelessness_prevalence",
        "source": SOURCE_UNHCR,
        "series_id": _STATELESS_COL,
        "countries": d2_present,
        "year_min": 2010,
        "year_max": 2025,
        "license": LICENSE_UNHCR,
        "direction": "high_risk",
        "anchor": "[0.0, 5000.0] persons per 100k population (re-used from UNHCR connector)",
        "coverage_pct": round(d2_cov, 1),
        "flags": (
            "RE-USED-NOT-REPULLED: D2 statelessness reads the already-standardized "
            f"`{_STATELESS_COL}` column from data/processed/unhcr.csv (data-stage "
            "correlation de-dup -- UNHCR connector owns the pull). "
            "SUPPRESSION/NON-RANDOM-MISSING (Strode et al. 2021; docs/METHODS.md): "
            "an absent/suppressed figure is DROPPED, NEVER -> 0; opacity can never "
            "lower the score; rows resting on D1 alone are marked low-confidence."
            + (" BELOW-COVERAGE-FLOOR -- low-confidence." if d2_below else "")
        ),
    })

    # Domain note row
    register_rows.append({
        "indicator": "lnr_domain_raw",
        "source": "FLSRI domain aggregate (this connector)",
        "series_id": "mean(D1,D2) rule-9 drop-and-re-average",
        "countries": n_domain_present,
        "year_min": d1_ymin if d1_ymin is not None else 2010,
        "year_max": 2025,
        "license": "derived",
        "direction": "high_risk",
        "anchor": "[0,1] equal-weight mean of D1 (birth-reg incompleteness) + D2 (statelessness)",
        "coverage_pct": round(domain_cov, 1),
        "flags": (
            "DOMAIN-RAW (pre-governance): equal-weight average of D1, D2 (scoring "
            "rule 5), with rule-9 drop-and-re-average (>=50%/min-1-of-2 floor; a "
            "single-driver domain is surfaced as a coverage caution). f_gov (M1 "
            "governance) is NOT applied here -- it is a downstream aggregation step. "
            f"{n_single_driver_lowconf} single-driver rows are low-confidence (of which "
            f"{n_d2_dropped_lowconf} from a dropped/suppressed statelessness figure, "
            "resting on D1 alone, never zeroed). See lnr_domain_low_confidence column "
            "for the per-row flag."
        ),
    })

    # M1 governance modulator -- NOTE ROW (not pulled; grounding-flagged)
    register_rows.append({
        "indicator": "lnr_governance_modulator_M1",
        "source": "SHARED governance backbone (wb_wgi_rule_of_law / v2x_rule) -- NOT re-pulled",
        "series_id": "n/a (note row -- downstream formula reference)",
        "countries": 0,
        "year_min": "",
        "year_max": "",
        "license": "n/a",
        "direction": "attenuate-only (1 - f_gov)",
        "anchor": "n/a (note row)",
        "coverage_pct": 0.0,
        "flags": (
            "NOTE-ROW / SHARED-SCORED-ONCE: M1 is the single shared general-governance "
            "dial, placed ONCE at the domain level (docs/METHODS.md; scoring rules 7-8). "
            "The data-stage correlation screen de-dups it -- this connector does NOT "
            "pull a separate governance index; the (1 - f_gov) attenuation is a "
            "downstream aggregation step that references the existing "
            "wb_wgi_rule_of_law / v2x_rule columns. "
            "GROUNDING-FLAGGED: the SPECIFIC governance x legal-non-recognition -> "
            "trafficking moderation is NOT in the verified pool -- carried as an "
            "assumption pending a data-stage source (pending decision; docs/METHODS.md). "
            "f_gov magnitude unsettled + flagged for the data-stage correlation/"
            "collinearity + sensitivity check (scoring rule 8; flagged for review)."
        ),
    })

    # Un-verified foundational-ID signal -- NOTE ROW (not wired)
    register_rows.append({
        "indicator": "lnr_foundational_id_coverage",
        "source": "NOT WIRED -- unverified; request filed",
        "series_id": "n/a (note row)",
        "countries": 0,
        "year_min": "",
        "year_max": "",
        "license": "n/a",
        "direction": "high_risk (intended)",
        "anchor": "n/a (note row)",
        "coverage_pct": 0.0,
        "flags": (
            "NOTE-ROW / UNVERIFIED: the design contemplates a SECOND D1 signal "
            "(foundational-ID / legal-identity coverage, e.g. WB ID4D) but its "
            "country coverage is flagged 'asserted-not-cited' (docs/METHODS.md). "
            "NOT wired -- not relied on; D1 rests on the single S1.1 backbone "
            "(one-signal driver, coverage caution). Must be a DISTINCT measurement "
            "base from birth-registration completeness to avoid double-counting the "
            "same latent signal. Pending decision (see docs/METHODS.md)."
        ),
    })

    register.ensure_header(path=str(FRAGMENT_PATH))
    register.upsert_rows(register_rows, path=str(FRAGMENT_PATH))
    print(f"[legal_non_recognition] wrote register fragment {FRAGMENT_PATH} "
          f"({len(register_rows)} rows, incl. 3 note rows)")
    return {"d1": d1, "d2": d2, "domain": domain_scores}, register_rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Legal Non-Recognition connector")
    ap.add_argument("--cache", action="store_true",
                    help="Skip the live pull; load D1 from the prior WB cache.")
    args = ap.parse_args()
    run(use_cache=args.cache)
