"""
ISO country normalization utilities for FLSRI indicators.

Convert country names / ISO2 / ISO3 from raw exports -> canonical ISO3,
filter to FLSRI's 195-country sample, and report unmatched rows.

Designed so each connector module (pipeline/sources/*.py) imports from here
instead of re-implementing country matching. Single source of truth for the
FLSRI country universe and name-variant handling.

Adapted from an earlier connector prototype (see docs/METHODS.md).
Changes from the earlier version: the canonical sample is now read from the repo's
`config/iso3_countries.csv` (column `iso3`) rather than the prior
`config/flsri_country_sample.csv` (column `ISO3`). The 195 ISO3 codes were
reconciled and match exactly. A CSV-backed alias loader (`load_alias_map`)
reads `config/iso3_mapping.csv` so numeric/internal codes (e.g. UNCTAD M49)
can be resolved without bloating the in-code override dict.

Usage:
    from pipeline.iso_utils import normalize_to_iso3, filter_to_sample, load_sample

    df['ISO3'] = df['country_name'].map(normalize_to_iso3)
    df_filtered, report = filter_to_sample(df, iso_col='ISO3')

Dependencies: pandas (always), pycountry (preferred). Falls back gracefully
if pycountry is unavailable.
"""

from pathlib import Path
import csv
import difflib

import pandas as pd

try:
    import pycountry
    _HAS_PYCOUNTRY = True
except ImportError:
    _HAS_PYCOUNTRY = False

# --- Repo layout ----------------------------------------------------------
# pipeline/iso_utils.py  ->  repo root is parent of pipeline/
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SAMPLE_PATH = _REPO_ROOT / "config" / "iso3_countries.csv"
_DEFAULT_MAPPING_PATH = _REPO_ROOT / "config" / "iso3_mapping.csv"


def load_sample(path=None):
    """Return the 195 FLSRI ISO3 codes as a list (from config/iso3_countries.csv)."""
    p = Path(path) if path else _DEFAULT_SAMPLE_PATH
    return [r.strip() for r in pd.read_csv(p)["iso3"].astype(str) if r.strip()]


# Cached set for fast membership tests
_SAMPLE_CACHE = None


def _get_sample():
    global _SAMPLE_CACHE
    if _SAMPLE_CACHE is None:
        _SAMPLE_CACHE = set(load_sample())
    return _SAMPLE_CACHE


# --- CSV-backed alias map -------------------------------------------------
# config/iso3_mapping.csv has columns: identifier_type, identifier_value,
# iso3, notes. It covers iso3/iso2/country_name forms for all 195 countries
# and is the place to add numeric/internal codes (e.g. UNCTAD M49) for the
# source connectors, so the in-code override dict stays small.
_ALIAS_CACHE = None


def load_alias_map(path=None):
    """Return {lowercased identifier_value: iso3} from config/iso3_mapping.csv."""
    global _ALIAS_CACHE
    if _ALIAS_CACHE is not None and path is None:
        return _ALIAS_CACHE
    p = Path(path) if path else _DEFAULT_MAPPING_PATH
    aliases = {}
    if p.exists():
        with open(p, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                val = (row.get("identifier_value") or "").strip()
                iso3 = (row.get("iso3") or "").strip()
                if val and iso3:
                    aliases[val.lower()] = iso3
    if path is None:
        _ALIAS_CACHE = aliases
    return aliases


# --- Manual overrides for common name variants ----------------------------
# Map non-canonical names (lowercased, stripped) -> ISO3. Covers political
# short names, World Bank conventions, common misspellings, and recent name
# changes. Non-sample codes (TWN, XKX, PRI, HKG, MAC) are kept here so they
# normalize cleanly and then get dropped by filter_to_sample (rather than
# silently mismatching).
_OVERRIDES = {
    # Major / political short forms
    "russia": "RUS",
    "south korea": "KOR",
    "north korea": "PRK",
    "iran": "IRN",
    "syria": "SYR",
    "vietnam": "VNM",
    "laos": "LAO",
    "moldova": "MDA",
    "tanzania": "TZA",
    "venezuela": "VEN",
    "bolivia": "BOL",
    "taiwan": "TWN",
    "palestine": "PSE",
    "kosovo": "XKX",
    # Recent renames
    "czech republic": "CZE",
    "czechia": "CZE",
    "burma": "MMR",
    "myanmar": "MMR",
    "ivory coast": "CIV",
    "cote d'ivoire": "CIV",
    "côte d'ivoire": "CIV",
    "cabo verde": "CPV",
    "cape verde": "CPV",
    "swaziland": "SWZ",
    "eswatini": "SWZ",
    "macedonia": "MKD",
    "north macedonia": "MKD",
    "east timor": "TLS",
    "timor-leste": "TLS",
    "timor leste": "TLS",
    "turkey": "TUR",
    "türkiye": "TUR",
    "turkiye": "TUR",
    # Congo disambiguation
    "drc": "COD",
    "dr congo": "COD",
    "democratic republic of the congo": "COD",
    "democratic republic of congo": "COD",
    "congo, dem. rep.": "COD",
    "congo, democratic republic of": "COD",
    "republic of congo": "COG",
    "congo": "COG",
    "congo, republic of": "COG",
    # Anglo-political
    "uk": "GBR",
    "united kingdom": "GBR",
    "great britain": "GBR",
    "britain": "GBR",
    "usa": "USA",
    "us": "USA",
    "united states": "USA",
    "united states of america": "USA",
    "uae": "ARE",
    "united arab emirates": "ARE",
    # World Bank-style suffix variants
    "yemen, rep.": "YEM",
    "egypt, arab rep.": "EGY",
    "iran, islamic rep.": "IRN",
    "venezuela, rb": "VEN",
    "gambia, the": "GMB",
    "the gambia": "GMB",
    "bahamas, the": "BHS",
    "the bahamas": "BHS",
    "lao pdr": "LAO",
    "korea, dem. people's rep.": "PRK",
    "korea, rep.": "KOR",
    "kyrgyz republic": "KGZ",
    "slovak republic": "SVK",
    "russian federation": "RUS",
    "syrian arab republic": "SYR",
    "tanzania, united republic of": "TZA",
    "viet nam": "VNM",
    # Other commonly-mismatched
    "central african republic": "CAF",
    "car": "CAF",
    "south sudan": "SSD",
    "papua new guinea": "PNG",
    "solomon islands": "SLB",
    "marshall islands": "MHL",
    "micronesia": "FSM",
    "federated states of micronesia": "FSM",
    "brunei": "BRN",
    "brunei darussalam": "BRN",
    "trinidad and tobago": "TTO",
    "antigua and barbuda": "ATG",
    "saint kitts and nevis": "KNA",
    "saint lucia": "LCA",
    "saint vincent and the grenadines": "VCT",
    "dominican republic": "DOM",
    "el salvador": "SLV",
    "costa rica": "CRI",
    "puerto rico": "PRI",
    "hong kong": "HKG",
    "hong kong sar, china": "HKG",
    "macao": "MAC",
    "macao sar, china": "MAC",
    "egypt": "EGY",
    "yemen": "YEM",
    "gambia": "GMB",
    "bahamas": "BHS",
}


def normalize_to_iso3(value):
    """
    Convert a country identifier (name / ISO2 / ISO3) to canonical ISO3.

    Returns ISO3 string if matched, else None. Resolution order:
      1. ISO3 passthrough (3 uppercase alpha, validated against pycountry if available)
      2. ISO2 lookup via pycountry
      3. Manual override dict (World Bank suffix variants, renames, short forms)
      4. CSV alias map (config/iso3_mapping.csv) — exact name / numeric codes
      5. pycountry exact name / official_name lookup
      6. pycountry fuzzy search
      7. difflib fuzzy match against override + alias keys
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s:
        return None

    # ISO3 passthrough
    if len(s) == 3 and s.isalpha() and s.isupper():
        if _HAS_PYCOUNTRY:
            if pycountry.countries.get(alpha_3=s):
                return s
            return None
        return s  # assume valid if no pycountry to verify

    # ISO2
    if len(s) == 2 and _HAS_PYCOUNTRY:
        match = pycountry.countries.get(alpha_2=s.upper())
        if match:
            return match.alpha_3

    key = s.lower()

    # Override
    if key in _OVERRIDES:
        return _OVERRIDES[key]

    # CSV alias map (exact match on identifier_value, incl. numeric codes)
    aliases = load_alias_map()
    if key in aliases:
        return aliases[key]

    # pycountry exact
    if _HAS_PYCOUNTRY:
        match = pycountry.countries.get(name=s)
        if match:
            return match.alpha_3
        match = pycountry.countries.get(official_name=s)
        if match:
            return match.alpha_3
        # pycountry fuzzy
        try:
            fuzzy = pycountry.countries.search_fuzzy(s)
            if fuzzy:
                return fuzzy[0].alpha_3
        except LookupError:
            pass

    # difflib fuzzy fallback against override + alias keys
    pool = list(_OVERRIDES.keys()) + list(load_alias_map().keys())
    candidates = difflib.get_close_matches(key, pool, n=1, cutoff=0.85)
    if candidates:
        c = candidates[0]
        return _OVERRIDES.get(c) or load_alias_map().get(c)

    return None


def filter_to_sample(df, iso_col="ISO3", sample_path=None):
    """
    Filter dataframe to FLSRI's 195-country sample.

    Args:
        df: input DataFrame.
        iso_col: column containing country identifiers (any form). Will be
            normalized to ISO3 in-place.
        sample_path: override path to the country sample CSV.

    Returns:
        (filtered_df, report) — filtered_df has iso_col rewritten to canonical
        ISO3; report is a dict with keys:
          - unmatched_in_raw: unique raw values that did not normalize
          - normalized_but_out_of_scope: ISO3s in raw data but not in FLSRI sample
          - sample_missing_from_data: FLSRI sample ISO3s with no rows in raw data
          - rows_in_raw, rows_after_filter, unique_countries_after_filter
    """
    sample = set(load_sample(sample_path)) if sample_path else _get_sample()

    df = df.copy()
    df["_iso3_normalized"] = df[iso_col].map(normalize_to_iso3)

    unmatched_in_raw = sorted(
        df.loc[df["_iso3_normalized"].isna(), iso_col]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    normalized = set(df["_iso3_normalized"].dropna().unique())
    out_of_scope = sorted(normalized - sample)
    sample_missing = sorted(sample - normalized)

    df_filtered = df[df["_iso3_normalized"].isin(sample)].copy()
    df_filtered[iso_col] = df_filtered["_iso3_normalized"]
    df_filtered = df_filtered.drop(columns=["_iso3_normalized"])

    report = {
        "unmatched_in_raw": unmatched_in_raw,
        "normalized_but_out_of_scope": out_of_scope,
        "sample_missing_from_data": sample_missing,
        "rows_in_raw": len(df),
        "rows_after_filter": len(df_filtered),
        "unique_countries_after_filter": df_filtered[iso_col].nunique(),
    }
    return df_filtered, report
