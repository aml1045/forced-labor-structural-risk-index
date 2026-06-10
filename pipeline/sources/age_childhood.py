"""Age/Childhood Structuring connector -- Layer-2 data map (Recruitment phase).

Domain: recruitment/age-childhood-structuring (design decision).
Maps the domain's conceptual signals to real, groundable datasets, standardizes
each to a 0-1 per-exposure absolute-anchored risk scale (scoring rule 1; see
docs/scoring-rules.md), records direction + coverage + provenance (rule 9 /
register), and writes data/processed/age_childhood.csv.

SIGNAL -> DATASET MAP (only signals with a verified dataset at the findings'
mechanism bar are wired; un-groundable signals are flagged, never fabricated):

  S1.1 Child-labor prevalence (standing pickability)
        -> World Bank WDI  SL.TLF.0714.ZS
           "Children in employment, total (% of children ages 7-14)".
           Read as STANDING pool size, NOT realized worst-forms (anti-circularity
           gate; see docs/METHODS.md). The condition-B gate is conceptual; this
           series is already a standing-prevalence stock, not an exploitation
           outcome.
           COVERAGE IS THIN (~71/195) -> BELOW-FLOOR, low-confidence (kept, flagged).

  S2.1 Out-of-school rate
        -> World Bank WDI  SE.SEC.UNER.LO.ZS
           "Adolescents out of school (% of lower secondary school age)".
           Education-as-protective-institution; absence raises exposure.

  S2.2 Youth-dependency / child-cohort share
        -> World Bank WDI  SP.POP.0014.TO.ZS
           "Population ages 0-14 (% of total population)".
           Structural weight of the dependent-child cohort. Note: WDI does not
           carry the 15-24 school-to-work adolescent band as a clean share; the
           0-14 share is the available structural proxy for cohort weight. The
           Condition-A soft youth-boundary ramp is a downstream formula gate,
           not applied to the raw anchor here.

  S3.2 Child-marriage rate
        -> World Bank WDI  SP.M18.2024.FE.ZS
           "Women who were first married by age 18 (% of women ages 20-24)"
           (SDG 5.3.1; UNICEF/MICS-DHS, mirrored on WDI). Premature foreclosure
           of childhood. NOTE: a MECHANISM source-gap is flagged on S3.2
           (child-marriage -> forced-labor pathway at the mechanism bar); the
           PREVALENCE data exists and is wired here, but whether S3.2 is RETAINED
           in scoring is a pending decision flagged for review. Flagged.

SIGNAL NOT WIRED (un-groundable at the mechanism bar -- flag, don't fabricate):

  S3.1 Orphan / caregiver-loss prevalence (all-cause)
        -> NO publish-safe 195-country panel. WDI carries no orphanhood series;
           UNICEF's machine-accessible orphanhood data is AIDS-RELATED ONLY
           (HIV-specific subset, regionally concentrated, mild circularity), not
           the all-cause caregiver-loss signal the construct defines. All-cause
           orphanhood exists only as modeled study estimates, not a turnkey table.
           DISPOSITION: NOT wired; pending decision (see docs/METHODS.md); D3
           falls back to S3.2 alone (low-confidence single signal). DO NOT
           substitute AIDS-orphanhood silently.

SIGNAL CEDED: Birth-registration / legal-identity -> Legal Non-Recognition
  (see docs/METHODS.md). NOT mapped or scored here.

MODULATOR Z (social-protection / child-protection floor): a SHARED
general-governance signal, placed ONCE at the domain level and reconciled at the
data-stage collinearity check against the governance backbone (wb_wgi_rule_of_law
/ v2x_rule, already wired by the worldbank / vdem connectors). NOT re-pulled here
-- doing so would double-enter the governance variance the scoring rules score
once. The domain aggregation step references the existing governance column.

All four wired signals come from the World Bank WDI API (auth-free, CC BY 4.0) --
the cleanest license in the source pool -- reusing the live-pull HTTP pattern
from pipeline/sources/worldbank.py (pagination, certifi SSL, retry).

Run:  python -m pipeline.sources.age_childhood
      python -m pipeline.sources.age_childhood --cache   # WB cache fallback
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
from pipeline.standardize import AnchorSpec, anchor_scale

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = _REPO_ROOT / "data" / "processed" / "age_childhood.csv"
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "age_childhood.csv"

# Prior fresh WB cache (vintage 2026-05-28) as a network fallback. Schema:
# iso3, country_name, year, series, value. NOTE: the four age/childhood series
# below are NOT in the prior 7-series cache, so --cache will only succeed for
# any series that happen to be present; the live pull is the primary path.
_CACHE_FALLBACK = _REPO_ROOT / "data" / "aux" / "worldbank_cache.csv"

SOURCE = "World Bank WDI (mirrors ILO/UNICEF SDG series)"
LICENSE = "CC BY 4.0"

API_BASE = "https://api.worldbank.org/v2"
PER_PAGE = 20000
TIMEOUT = 30
RETRIES = 1
DELAY_BETWEEN_SERIES = 0.5
# Most-recent-year window. Childhood prevalences are infrequent survey stocks;
# a wide window maximizes coverage. Most-recent non-NaN year per country is taken.
DATE_FROM = 2005
DATE_TO = 2024


# --------------------------------------------------------------------------
# Series specs -- each carries the WDI code and its standardization anchor.
# Anchors are ABSOLUTE per-exposure reference points (scoring rule 1; see
# docs/scoring-rules.md), set from the observed cross-country distribution's
# defensible extremes; flagged as data-stage priors to re-examine.
# --------------------------------------------------------------------------

_ANCHOR_FLAG = (
    "ANCHOR-PRIOR: floor/ceiling set from the observed cross-country distribution "
    "extremes; absolute per-exposure anchor, re-examine at data-stage review"
)

SERIES = [
    {
        "code": "SL.TLF.0714.ZS",
        "indicator": "age_child_labor_prevalence",
        "signal": "S1.1",
        "spec": AnchorSpec(
            indicator="age_child_labor_prevalence",
            floor=0.0, ceiling=40.0, direction="high_risk",
            unit="% of children ages 7-14 in employment",
            anchor_source=(
                "Children-in-employment share (WDI SL.TLF.0714.ZS); floor 0 = none, "
                "ceiling 40 ~ high end of observed national child-work prevalence. "
                "Read as STANDING pickability / reachable-pool size, NOT realized "
                "worst-forms (anti-circularity; docs/METHODS.md). " + _ANCHOR_FLAG
            ),
        ),
        "flags": [
            "STANDING-NOT-REALIZED: read as child-work pool size, not realized "
            "trafficking/worst-forms (anti-circularity gate; docs/METHODS.md)",
        ],
    },
    {
        "code": "SE.SEC.UNER.LO.ZS",
        "indicator": "age_out_of_school_rate",
        "signal": "S2.1",
        "spec": AnchorSpec(
            indicator="age_out_of_school_rate",
            floor=0.0, ceiling=60.0, direction="high_risk",
            unit="% of lower-secondary-age adolescents out of school",
            anchor_source=(
                "Adolescents out of school (WDI SE.SEC.UNER.LO.ZS); floor 0 = "
                "universal enrolment (min risk), ceiling 60 ~ high end of observed "
                "out-of-school rates. Education = protective institution. " + _ANCHOR_FLAG
            ),
        ),
        "flags": [],
    },
    {
        "code": "SP.POP.0014.TO.ZS",
        "indicator": "age_child_cohort_share",
        "signal": "S2.2",
        "spec": AnchorSpec(
            indicator="age_child_cohort_share",
            floor=10.0, ceiling=50.0, direction="high_risk",
            unit="% of total population ages 0-14",
            anchor_source=(
                "Population ages 0-14 share (WDI SP.POP.0014.TO.ZS); floor 10 ~ "
                "lowest observed (aged societies), ceiling 50 ~ highest observed "
                "(youngest). Structural weight of the dependent-child cohort = "
                "pickability weight. " + _ANCHOR_FLAG
            ),
        ),
        "flags": [
            "PROXY: WDI carries no clean 15-24 school-to-work band share; the 0-14 "
            "share is the available structural cohort-weight proxy. Condition-A "
            "youth-boundary ramp (docs/METHODS.md) is a downstream formula gate.",
        ],
    },
    {
        "code": "SP.M18.2024.FE.ZS",
        "indicator": "age_child_marriage_rate",
        "signal": "S3.2",
        "spec": AnchorSpec(
            indicator="age_child_marriage_rate",
            floor=0.0, ceiling=60.0, direction="high_risk",
            unit="% of women ages 20-24 first married by age 18",
            anchor_source=(
                "Child-marriage prevalence (WDI SP.M18.2024.FE.ZS; SDG 5.3.1, "
                "UNICEF/MICS-DHS); floor 0, ceiling 60 ~ high end of observed "
                "prevalence. Premature foreclosure of childhood. " + _ANCHOR_FLAG
            ),
        ),
        "flags": [
            "MECHANISM-SOURCE-GAP (docs/METHODS.md): the prevalence DATA exists "
            "and is wired, but the child-marriage -> forced-labor MECHANISM "
            "source at the mechanism bar is still a pending decision; whether "
            "S3.2 is RETAINED in D3 scoring depends on that mechanism review -- "
            "flagged for review.",
        ],
    },
]


# --------------------------------------------------------------------------
# Live WDI pull (ported pattern from pipeline/sources/worldbank.py).
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


def _pull_all_live() -> dict:
    out = {}
    for i, s in enumerate(SERIES):
        code = s["code"]
        rows = _pull_series_live(code)
        out[code] = rows
        n_nn = sum(1 for r in rows if r["value"] not in (None, ""))
        print(f"[age_childhood] live pull {code}: {len(rows)} rows ({n_nn} non-null)")
        if i < len(SERIES) - 1:
            time.sleep(DELAY_BETWEEN_SERIES)
    return out


def _load_from_cache() -> dict:
    if not _CACHE_FALLBACK.exists():
        raise FileNotFoundError(f"cache fallback not found: {_CACHE_FALLBACK}")
    by_code: dict = {s["code"]: [] for s in SERIES}
    with open(_CACHE_FALLBACK, newline="", encoding="utf-8") as fh:
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


# --------------------------------------------------------------------------
# Reduce to most-recent non-NaN year per country, normalize ISO3.
# --------------------------------------------------------------------------

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
# Run.
# --------------------------------------------------------------------------

def run(use_cache: bool = False):
    countries = iso_utils.load_sample()

    raw_by_code = None
    if not use_cache:
        try:
            raw_by_code = _pull_all_live()
        except Exception as e:
            print(f"[age_childhood] live pull failed ({e}); falling back to cache",
                  file=sys.stderr)
        else:
            # write-back so the shared cache carries this connector's series
            # (without it, --cache finds no rows and would emit empty output)
            from pipeline import raw_cache
            raw_cache.upsert_series(_CACHE_FALLBACK, raw_by_code,
                                    source_label="age_childhood (WDI series)")
    if raw_by_code is None:
        raw_by_code = _load_from_cache()
        print(f"[age_childhood] loaded from cache: {_CACHE_FALLBACK}")

    columns = []
    scored_cols = {}
    register_rows = []

    for s in SERIES:
        code = s["code"]
        indicator = s["indicator"]
        rows = raw_by_code.get(code, [])
        values, ymin, ymax = _most_recent_by_iso3(rows)

        result = anchor_scale(values, s["spec"], sample=countries)
        scored_cols[indicator] = result
        columns.append(indicator)

        row = result.register_row(
            source=SOURCE, series_id=code, license=LICENSE,
            extra_flags=s.get("flags"),
        )
        row["year_min"] = ymin if ymin is not None else ""
        row["year_max"] = ymax if ymax is not None else ""
        register_rows.append(row)

        m = result.meta
        print(f"[age_childhood] {code} ({indicator}, {s['signal']}) "
              f"dir={m['direction']} coverage {m['coverage_pct']:.1f}% "
              f"({m['n_present']}/{m['n_total']}), years {ymin}-{ymax}, "
              f"below_floor={m['below_floor']}")

    # Note-row for the un-wired orphanhood signal S3.1 (un-groundable).
    register_rows.append({
        "indicator": "age_orphanhood_prevalence",
        "source": "NOT WIRED -- pending decision",
        "series_id": "n/a (note row)",
        "countries": 0,
        "year_min": "",
        "year_max": "",
        "license": "n/a",
        "direction": "high_risk (intended)",
        "anchor": "n/a (note row)",
        "coverage_pct": 0.0,
        "flags": (
            "NOTE-ROW / UN-GROUNDABLE: S3.1 all-cause orphan/caregiver-loss "
            "prevalence has NO publish-safe 195-country panel. WDI carries no "
            "orphanhood series; UNICEF machine-accessible orphanhood is AIDS-related "
            "ONLY (HIV-specific subset, mild circularity) -- NOT substituted silently. "
            "Pending decision (see docs/METHODS.md). D3 falls back to S3.2 "
            "(child-marriage) alone, low-confidence single signal."
        ),
    })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3"] + columns)
        for iso3 in countries:
            line = [iso3]
            for col in columns:
                v = scored_cols[col].get(iso3)
                line.append("" if v is None else round(v, 4))
            w.writerow(line)

    register.ensure_header(path=str(FRAGMENT_PATH))
    register.upsert_rows(register_rows, path=str(FRAGMENT_PATH))

    print(f"\n[age_childhood] wrote {OUT_PATH} "
          f"({len(countries)} rows x {len(columns)} risk indicators)")
    print(f"[age_childhood] wrote register fragment {FRAGMENT_PATH} "
          f"({len(register_rows)} rows, incl. 1 un-wired note row)")
    return scored_cols, register_rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Age/Childhood Structuring connector")
    ap.add_argument("--cache", action="store_true",
                    help="Skip the live pull; load from the prior WB cache.")
    args = ap.parse_args()
    run(use_cache=args.cache)
