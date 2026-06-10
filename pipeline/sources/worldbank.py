"""World Bank WDI + WGI connector for FLSRI (Layer-1 DATA plumbing).

Source : World Bank World Development Indicators (WDI) + Worldwide Governance
         Indicators (WGI). https://api.worldbank.org/v2  --  no auth.
License: CC BY 4.0 (open data) -- the cleanest license of any FLSRI source.

Seven series (config/api-config/worldbank-series.yaml):
  SP.POP.TOTL          Population, total          -> DENOMINATOR (not a risk indicator)
  SI.POV.GINI          Gini index                 -> high_risk
  SE.SEC.CMPT.LO.ZS    Lower-secondary completion -> low_risk (high completion = less risk)
  BX.TRF.PWKR.DT.GD.ZS Remittances (% of GDP)     -> high_risk
  SL.GDP.PCAP.EM.KD    Labor productivity         -> low_risk (high productivity = less risk)
  IC.FRM.BRIB.ZS       Bribery incidence (% firms)-> high_risk
  GOV_WGI_RL.EST       WGI Rule of Law (-2.5..2.5)-> low_risk (high rule-of-law = less risk)

Follows the shared connector shape (pipeline/sources/_core_smoke.py):
  load raw -> normalize_to_iso3 -> most-recent non-NaN year per country ->
  (per-exposure where size matters) -> anchor_scale (0-1) ->
  write data/processed/worldbank.csv -> register provenance + coverage to a
  per-source fragment (config/data_register.d/worldbank.csv).

The auth-free HTTP, pagination, and source=3 (for WGI) handling live inline
here. By default we re-pull live; if the network fails we fall back to the
prior fresh cache so a run still produces output.

Run:  python -m pipeline.sources.worldbank
      python -m pipeline.sources.worldbank --cache   # skip network, use cache
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
OUT_PATH = _REPO_ROOT / "data" / "processed" / "worldbank.csv"
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "worldbank.csv"
SERIES_CONFIG = _REPO_ROOT / "config" / "api-config" / "worldbank-series.yaml"

# Prior fresh cache (vintage 2026-05-28), used as a fallback if the live pull
# fails. Schema: iso3, country_name, year, series, value. Staged in-repo.
_CACHE_FALLBACK = _REPO_ROOT / "data" / "aux" / "worldbank_cache.csv"

SOURCE = "World Bank WDI + WGI"
LICENSE = "CC BY 4.0"

API_BASE = "https://api.worldbank.org/v2"
PER_PAGE = 20000
TIMEOUT = 30
RETRIES = 1
DELAY_BETWEEN_SERIES = 0.5


# --------------------------------------------------------------------------
# Series specs. Each carries the WB code, WB source id (None=WDI; 3=WGI), and
# the standardization spec. SP.POP.TOTL has NO AnchorSpec -- it is the
# per-capita denominator, not a risk indicator, and is registered as such.
#
# DIRECTION + ANCHORS (absolute; see docs/scoring-rules.md, rule 1). Anchors are
# PRIORS to re-examine at data-stage review -- the labor-productivity floor/frontier
# are carried straight from the prior methodology and explicitly flagged below.
# --------------------------------------------------------------------------

_PRIOR_ANCHOR_FLAG = (
    "PRIOR-ANCHOR: floor/ceiling carried from the prior methodology; "
    "starting point only -- flagged for review, not fixed"
)

SERIES = [
    {
        "code": "SP.POP.TOTL",
        "source": None,
        "indicator": "wb_population_total",
        "role": "denominator",
        "spec": None,  # denominator, not scaled to a 0-1 risk score
    },
    {
        "code": "SI.POV.GINI",
        "source": None,
        "indicator": "wb_gini",
        "spec": AnchorSpec(
            indicator="wb_gini", floor=20.0, ceiling=65.0,
            direction="high_risk", unit="Gini index (0-100)",
            anchor_source=(
                "Gini index points; floor 20 ~ most-equal observed economies, "
                "ceiling 65 ~ high-inequality end of the observed distribution. "
                + _PRIOR_ANCHOR_FLAG
            ),
        ),
    },
    {
        "code": "SE.SEC.CMPT.LO.ZS",
        "source": None,
        "indicator": "wb_lower_secondary_completion",
        "spec": AnchorSpec(
            indicator="wb_lower_secondary_completion", floor=0.0, ceiling=100.0,
            direction="low_risk", unit="% of relevant age group",
            anchor_source=(
                "completion-rate %; 0 = no completion (max risk), 100 = universal "
                "completion (min risk). low_risk: high completion inverts to low risk. "
                + _PRIOR_ANCHOR_FLAG
            ),
        ),
    },
    {
        "code": "BX.TRF.PWKR.DT.GD.ZS",
        "source": None,
        "indicator": "wb_remittances_pct_gdp",
        "spec": AnchorSpec(
            indicator="wb_remittances_pct_gdp", floor=0.0, ceiling=35.0,
            direction="high_risk", unit="personal remittances received, % of GDP",
            anchor_source=(
                "remittances as share of GDP; floor 0, ceiling 35 ~ the high end of "
                "the observed distribution for remittance-dependent economies. "
                + _PRIOR_ANCHOR_FLAG
            ),
        ),
    },
    {
        "code": "SL.GDP.PCAP.EM.KD",
        "source": None,
        "indicator": "wb_labor_productivity",
        "spec": AnchorSpec(
            indicator="wb_labor_productivity", floor=785.0, ceiling=119601.0,
            direction="low_risk", unit="GDP per person employed (constant 2017 PPP $)",
            anchor_source=(
                "labor productivity; prior methodology anchors floor=785 (subsistence) "
                "and frontier=119601 (G7 mean). low_risk: high productivity inverts to "
                "low risk. " + _PRIOR_ANCHOR_FLAG
            ),
        ),
    },
    {
        "code": "IC.FRM.BRIB.ZS",
        "source": None,
        "indicator": "wb_bribery_incidence",
        "spec": AnchorSpec(
            indicator="wb_bribery_incidence", floor=0.0, ceiling=60.0,
            direction="high_risk",
            unit="% of firms experiencing at least one bribe payment request",
            anchor_source=(
                "Enterprise Survey bribery incidence; floor 0, ceiling 60 ~ high end "
                "of observed firm-bribery prevalence. " + _PRIOR_ANCHOR_FLAG
            ),
        ),
    },
    {
        "code": "GOV_WGI_RL.EST",
        "source": 3,  # WGI is a non-default WB source; REQUIRED.
        "indicator": "wb_wgi_rule_of_law",
        "spec": AnchorSpec(
            indicator="wb_wgi_rule_of_law", floor=-2.5, ceiling=2.5,
            direction="low_risk", unit="WGI Rule of Law governance estimate (-2.5..+2.5)",
            anchor_source=(
                "WGI Rule of Law estimate native bounds -2.5..+2.5; low_risk: high "
                "rule-of-law inverts to low risk. " + _PRIOR_ANCHOR_FLAG
            ),
        ),
    },
]


# --------------------------------------------------------------------------
# Live pull (ported from tools/refresh_worldbank.py) with cache fallback.
# --------------------------------------------------------------------------

def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CTX = _ssl_context()


def _api_url(code: str, page: int, source) -> str:
    extra = f"&source={source}" if source else ""
    return (f"{API_BASE}/country/all/indicator/{code}"
            f"?format=json&per_page={PER_PAGE}&page={page}{extra}")


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


def _pull_series_live(code: str, source) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        body = _http_get_json(_api_url(code, page, source))
        if not isinstance(body, list) or len(body) < 2:
            raise RuntimeError(f"{code}: unexpected response shape from WB API")
        meta, data = body[0], body[1]
        if not isinstance(data, list):
            raise RuntimeError(f"{code}: data payload is not a list")
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
    """Pull every configured series live. Returns {code: [rows]}. Raises on
    total failure so the caller can fall back to the cache."""
    out = {}
    for i, s in enumerate(SERIES):
        code, source = s["code"], s["source"]
        rows = _pull_series_live(code, source)
        out[code] = rows
        n_nn = sum(1 for r in rows if r["value"] not in (None, ""))
        print(f"[worldbank] live pull {code}: {len(rows)} rows ({n_nn} non-null)")
        if i < len(SERIES) - 1:
            time.sleep(DELAY_BETWEEN_SERIES)
    return out


def _load_from_cache() -> dict:
    """Read the prior fresh cache. Returns {code: [rows]}."""
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
    """Return ({iso3: value}, year_min, year_max) using the most-recent
    non-NaN year per country, normalized to the FLSRI ISO3 sample."""
    best: dict = {}  # iso3 -> (year, value)
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

    # 1. Acquire raw rows (live, else cache).
    raw_by_code = None
    if not use_cache:
        try:
            raw_by_code = _pull_all_live()
        except Exception as e:
            print(f"[worldbank] live pull failed ({e}); falling back to cache",
                  file=sys.stderr)
        else:
            # write-back: a successful live pull refreshes the shared cache so
            # subsequent --cache runs reproduce exactly this data
            from pipeline import raw_cache
            raw_cache.upsert_series(_CACHE_FALLBACK, raw_by_code,
                                    source_label="worldbank (WDI+WGI, 7 series)")
    if raw_by_code is None:
        raw_by_code = _load_from_cache()
        print(f"[worldbank] loaded from cache: {_CACHE_FALLBACK}")

    # 2. Reduce + standardize per series.
    columns = []        # ordered indicator column names for the processed CSV
    scored_cols = {}    # indicator -> {iso3: 0-1 or None}
    register_rows = []

    for s in SERIES:
        code = s["code"]
        indicator = s["indicator"]
        rows = raw_by_code.get(code, [])
        values, ymin, ymax = _most_recent_by_iso3(rows)

        if s["spec"] is None:
            # SP.POP.TOTL -- denominator, NOT a risk indicator. Register its
            # role + coverage but do NOT scale it to 0-1 or put it in the
            # risk table. n_present is countries with a population value.
            n_present = sum(1 for iso3 in countries if iso3 in values)
            n_total = len(countries)
            cov_pct = 100.0 * n_present / n_total if n_total else 0.0
            below = (n_present / n_total < 0.5) or (n_present < 2) if n_total else True
            flags = [
                "DENOMINATOR: population is the per-capita denominator for other "
                "indicators, NOT a forced-labor risk indicator; not scaled to 0-1 "
                "and not in the risk table"
            ]
            if below:
                flags.append(
                    f"BELOW-COVERAGE-FLOOR ({n_present}/{n_total} = {cov_pct:.0f}%)"
                )
            register_rows.append({
                "indicator": indicator,
                "source": SOURCE,
                "series_id": code,
                "countries": n_present,
                "year_min": ymin if ymin is not None else "",
                "year_max": ymax if ymax is not None else "",
                "license": LICENSE,
                "direction": "n/a (denominator)",
                "anchor": "n/a (denominator)",
                "coverage_pct": round(cov_pct, 1),
                "flags": "; ".join(flags),
            })
            print(f"[worldbank] {code} ({indicator}) DENOMINATOR: "
                  f"coverage {cov_pct:.1f}% ({n_present}/{n_total}), years {ymin}-{ymax}")
            continue

        result = anchor_scale(values, s["spec"], sample=countries)
        scored_cols[indicator] = result
        columns.append(indicator)

        row = result.register_row(source=SOURCE, series_id=code, license=LICENSE)
        row["year_min"] = ymin if ymin is not None else ""
        row["year_max"] = ymax if ymax is not None else ""
        register_rows.append(row)

        m = result.meta
        print(f"[worldbank] {code} ({indicator}) dir={m['direction']} "
              f"anchor={m['anchor']} coverage {m['coverage_pct']:.1f}% "
              f"({m['n_present']}/{m['n_total']}), years {ymin}-{ymax}, "
              f"below_floor={m['below_floor']}")

    # 3. Write processed CSV: iso3 + one column per risk indicator.
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

    # 4. Write the per-source register fragment (merged downstream).
    register.ensure_header(path=str(FRAGMENT_PATH))
    register.upsert_rows(register_rows, path=str(FRAGMENT_PATH))

    print(f"\n[worldbank] wrote {OUT_PATH} "
          f"({len(countries)} rows x {len(columns)} risk indicators)")
    print(f"[worldbank] wrote register fragment {FRAGMENT_PATH} "
          f"({len(register_rows)} rows)")
    return scored_cols, register_rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="World Bank WDI+WGI connector")
    ap.add_argument("--cache", action="store_true",
                    help="Skip the live pull; load from the prior fresh cache.")
    args = ap.parse_args()
    run(use_cache=args.cache)
