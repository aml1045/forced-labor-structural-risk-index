"""AUX source -- UNCTAD merchandise EXPORT concentration index.

Provides the UNCTAD merchandise export concentration indicator (see
docs/METHODS.md and docs/data-provenance.md). Self-contained: it depends only
on the repo's iso_utils / standardize / register, plus
config/api-config/UNCTADSTAT.yaml.

WHAT IT DOES
  Pulls the UNCTAD merchandise PRODUCT CONCENTRATION index (Herfindahl-
  Hirschman-style, range ~0-1) for exports (Flow/Code '01') from the
  authenticated UNCTADSTAT OData API, takes the most-recent year per economy,
  maps UNCTAD numeric economy codes (== UN M49 numeric) to ISO3, and anchor-
  scales to 0-1.

THE M49 -> ISO3 STEP
  UNCTAD economy codes are NOT ISO3 -- they are UN M49 numeric codes
  (verified: France=250, Bangladesh=050, China=156, Germany=276, Angola=024).
  This module maps them via pycountry.countries.get(numeric=...). The 5xxx
  codes are regional/world aggregates and are dropped. ~191 of the 195 FLSRI
  countries resolve. Label-based fuzzy matching is deliberately NOT used
  (it mis-maps "Africa" -> ZAF); only the exact numeric map is trusted.

DIRECTION
  higher = more risk. A higher concentration index = a narrower export
  basket = greater structural dependence on a few products = more exposure to
  trade/labour-demand shocks. direction="high_risk", no inversion.

ANCHOR
  The index is already a normalized 0-1 concentration measure. floor 0.0
  (fully diversified), ceiling 1.0 (fully concentrated in one product). This
  is the measure's own natural absolute range -- an absolute anchor, not
  min-max.

CREDENTIALS
  ClientId + ClientSecret read from (1) env vars UNCTAD_CLIENT_ID /
  UNCTAD_API_KEY, else (2) the repo .env.txt ("Client ID:" / "API key:"
  lines). Credentials are never logged or written to output. If absent, the
  module reports BLOCKED-on-credentials and writes no table.

FLAGS carried to the register
  - DIRECTION-FIXED: the series was switched from IMPORT concentration
    (Flow/Code '02') to EXPORT concentration (Flow/Code '01') so it matches
    indicator 2.3.4 (EXPORT commodity concentration). The earlier
    DIRECTION-VERIFY ambiguity is resolved; the series is pulled on the export
    flow (2024) and data/processed/aux_unctad.csv carries the export column.
  - M49->ISO3 mapping is implemented here.

Run:  python -m pipeline.sources.aux_unctad
"""

from __future__ import annotations

from pathlib import Path
import csv
import gzip
import io
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd
import yaml

try:
    import pycountry
    _HAS_PYCOUNTRY = True
except ImportError:
    _HAS_PYCOUNTRY = False

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = _REPO_ROOT / "data" / "processed" / "aux_unctad.csv"
REGISTER_FRAGMENT = _REPO_ROOT / "config" / "data_register.d" / "aux_unctad.csv"
_CONFIG_PATH = _REPO_ROOT / "config" / "api-config" / "UNCTADSTAT.yaml"
_ENV_PATH = _REPO_ROOT / ".env.txt"

API_BASE = "https://unctadstat-user-api.unctad.org"
TIMEOUT = 90

SOURCE = "UNCTADSTAT (authenticated OData API)"
LICENSE = "UNCTAD data publicly available; cite UNCTADSTAT"

_DIRECTION_FLAG = (
    "DIRECTION-FIXED: switched from IMPORT (Flow/Code '02') to EXPORT "
    "concentration (Flow/Code '01') to match indicator 2.3.4; DIRECTION-VERIFY "
    "flag cleared. Series pulled on the export flow (Flow/Code '01', 2024) -- "
    "data/processed/aux_unctad.csv carries aux_unctad_export_concentration "
    "(any earlier stale import column is overwritten)"
)
_M49_FLAG = (
    "M49->ISO3 mapping implemented here via pycountry numeric; 5xxx regional "
    "aggregates dropped"
)


# --- credentials -----------------------------------------------------------

def _load_credentials() -> tuple[str | None, str | None]:
    cid = os.environ.get("UNCTAD_CLIENT_ID")
    key = os.environ.get("UNCTAD_API_KEY")
    if cid and key:
        return cid, key
    if _ENV_PATH.exists():
        text = _ENV_PATH.read_text(encoding="utf-8")
        cid_m = re.search(r"Client\s*ID\s*:\s*(\S+)", text, re.IGNORECASE)
        key_m = re.search(r"API\s*key\s*:\s*(\S.*?)\s*$", text, re.IGNORECASE | re.MULTILINE)
        if cid_m and key_m:
            return cid_m.group(1).strip(), key_m.group(1).strip()
    return None, None


# --- config ----------------------------------------------------------------

def _load_series_spec() -> dict:
    with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data["series"][0]


# --- M49 numeric -> ISO3 ----------------------------------------------------

def m49_to_iso3(code) -> str | None:
    """Map a UNCTAD economy code (== UN M49 numeric) to ISO3 via pycountry.

    Returns None for regional/world aggregates (no numeric country match).
    """
    if not _HAS_PYCOUNTRY:
        return None
    s = str(code).strip()
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    try:
        c = pycountry.countries.get(numeric=str(int(digits)).zfill(3))
    except (ValueError, KeyError):
        return None
    return c.alpha_3 if c else None


# --- HTTP pull -------------------------------------------------------------

def _pull(spec: dict, cid: str, key: str) -> pd.DataFrame:
    url = f"{API_BASE}/{spec['endpoint']}"
    form = {"$format": "csv", "compress": "gz"}
    for yk, fk in (("select", "$select"), ("filter", "$filter"),
                   ("orderby", "$orderby"), ("compute", "$compute")):
        if spec.get(yk):
            form[fk] = spec[yk]
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(form).encode("utf-8"), method="POST",
        headers={
            "ClientId": cid, "ClientSecret": key,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/csv,application/gzip,*/*",
            "User-Agent": "FLSRI-pipeline/1.0 (academic research)",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
            return pd.read_csv(gz, low_memory=False)
    return pd.read_csv(io.BytesIO(raw), low_memory=False)


# --- run -------------------------------------------------------------------

def run():
    cid, key = _load_credentials()
    if not cid or not key:
        print("[aux_unctad] BLOCKED-on-credentials: no UNCTAD_CLIENT_ID/"
              "UNCTAD_API_KEY env vars and no Client ID / API key in .env.txt. "
              "Connector + config are ported and ready; no table written.")
        return None

    spec = _load_series_spec()
    iso_col = spec.get("iso_col", "Economy_Code")
    year_col = spec.get("year_col", "Year")
    value_col = spec.get("value_col", "Concentration_Index_Value")

    df = _pull(spec, cid, key)
    for col in (iso_col, year_col, value_col):
        if col not in df.columns:
            raise RuntimeError(f"UNCTAD response missing column {col!r}; got {list(df.columns)}")

    df = df[df[value_col].notna()].copy()
    df["iso3"] = df[iso_col].map(m49_to_iso3)
    sample = iso_utils.load_sample()
    sample_set = set(sample)
    df = df[df["iso3"].isin(sample_set)]

    # most-recent year per country
    df = df.sort_values(year_col).groupby("iso3", as_index=False).last()
    raw = dict(zip(df["iso3"], pd.to_numeric(df[value_col], errors="coerce")))
    raw = {k: (None if pd.isna(v) else float(v)) for k, v in raw.items()}
    year_min = int(df[year_col].min()) if len(df) else None
    year_max = int(df[year_col].max()) if len(df) else None

    spec_anchor = AnchorSpec(
        indicator="unctad_export_concentration",
        floor=0.0, ceiling=1.0,
        direction="high_risk",
        unit="UNCTAD merchandise export concentration index (HHI-style, 0-1)",
        anchor_source="measure's own natural range: 0 = fully diversified "
                      "export basket, 1 = fully concentrated in one product",
    )
    res = anchor_scale(raw, spec_anchor, sample=sample)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3", "aux_unctad_export_concentration"])
        for iso3 in sample:
            v = res.get(iso3)
            w.writerow([iso3, "" if v is None else round(v, 4)])

    row = res.register_row(
        source=SOURCE,
        series_id=spec.get("code", "unctad_concent_div_exports"),
        license=LICENSE,
        extra_flags=[_DIRECTION_FLAG, _M49_FLAG],
    )
    row["year_min"], row["year_max"] = year_min, year_max
    register.upsert_rows([row], path=str(REGISTER_FRAGMENT))

    print(f"[aux_unctad] pulled {len(df)} in-sample economies "
          f"(years {year_min}-{year_max}, most-recent per country)")
    print(f"[aux_unctad] wrote {OUT_PATH}")
    print(f"[aux_unctad] coverage {res.meta['coverage_pct']:.1f}% "
          f"({res.meta['n_present']}/{res.meta['n_total']}) "
          f"below_floor={res.meta['below_floor']}")
    print(f"[aux_unctad] register fragment: {REGISTER_FRAGMENT}")
    print(f"[aux_unctad] FLAGS: {res.meta['flags'] + [_DIRECTION_FLAG, _M49_FLAG]}")
    return res


if __name__ == "__main__":
    run()
