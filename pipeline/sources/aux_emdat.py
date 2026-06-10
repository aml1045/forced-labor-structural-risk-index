"""AUX source -- EM-DAT disaster-shock intensity (FLSRI indicator 1.1.1).

Operationalizes FLSRI indicator 1.1.1 "Disaster Shock Intensity" (see
docs/METHODS.md and docs/data-provenance.md).

WHAT IT DOES
  Sum EM-DAT natural-disaster Total Deaths and Total Affected per country over
  a recent window, divide by World Bank population (SP.POP.TOTL, most-recent
  non-NaN year) to get two per-exposure components.
  Heat-wave entries (Disaster Subtype "Heat wave") are excluded at the subtype
  level, ALL years (documented design decision, §13): their excess-mortality
  accounting exists only where statistical systems run attribution studies, so
  the entries measure mortality-statistics capacity rather than
  displacement-producing shock. Cold-wave entries stay (flagged for watch).
  Components:
      mortality_intensity  = deaths over window / population * 100,000
      affected_intensity   = affected over window / population   (share of pop)
  Each is anchor-scaled to 0-1 (scoring rule 1), then the two component
  signals are combined into one indicator score by drop-and-re-average
  (rule 9, equal weight -- two readings of one construct).

DIRECTION
  higher = more risk for BOTH components (more disaster mortality / more
  population affected = more structural disruption). No inversion.

ANCHORS (PRIORS, pending decision)
  mortality_intensity: floor 0, ceiling 100 deaths per 100k over the window.
    Justification: 100/100k ~= 0.1% of the population killed by natural
    disasters over a 5-year window is a defensible "catastrophic mortality
    burden" upper anchor; it also sits at ~the 99th percentile of the observed
    2020-2024 cross-section, so the scale is not saturated by a single outlier.
  affected_intensity: floor 0, ceiling 1.0 (= the entire population affected
    at least once over the window). Values can exceed 1.0 from repeat events /
    multi-counting across years; clamped to 1.0.
  Both anchors are ABSOLUTE per-exposure anchors (not min-max), per rule 1.

WINDOW
  2020-2024 (five COMPLETE calendar years). An earlier window choice used
  2020-2025; 2025 is partial in this export (fewer events) so it is excluded
  to avoid a truncation bias against the most-recent year. The window itself
  is a PRIOR, pending decision -- not a locked choice.

MISSING DATA (MNAR true-zero -- PRIOR, pending decision)
  EM-DAT is an event-stock source: a country absent from the export recorded
  no qualifying disaster in the window. Absent in-sample countries are treated
  as TRUE ZERO (no events -> 0 deaths, 0 affected -> score 0). That is an
  accepted MNAR assumption, NOT a missing value -- it is the only place in this
  pipeline where absence is read as a real low value rather than dropped.
  Countries that ARE in EM-DAT but have no usable population denominator stay
  missing (None), not zero.

LICENSE -- BLOCKER (caveat, not resolved here)
  EM-DAT (CRED / UCLouvain) is NON-COMMERCIAL / academic-use, registration
  required. The standardized table may be built for internal scoring, but
  publication eligibility MUST be confirmed before any public release.
  Surfaced on every register row.

DEPENDENCY
  Needs population. The canonical source is data/processed/worldbank.csv
  (SP.POP.TOTL), which is NOT yet present in the repo (sibling WB source not
  yet landed). This module reads it if present; otherwise it falls back to a
  World Bank API cache (read-only legacy file) and FLAGS the dependency so it
  can be rewired to the repo WB table once that lands.

Run:  python -m pipeline.sources.aux_emdat
"""

from __future__ import annotations

from pathlib import Path
import csv
import glob

import pandas as pd

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale, drop_and_average

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = _REPO_ROOT / "data" / "processed" / "aux_emdat.csv"
REGISTER_FRAGMENT = _REPO_ROOT / "config" / "data_register.d" / "aux_emdat.csv"

# Project root: raw inputs resolve repo-relative under data/raw/ (no escape).
_PROJECT_ROOT = _REPO_ROOT

# --- source files (read-only legacy inputs, now under data/raw/) -----------
_EMDAT_GLOB = str(
    _PROJECT_ROOT
    / "data" / "raw" / "*emdat*.xlsx"
)
_EMDAT_SHEET = "EM-DAT Data"

# Canonical per-capita denominator: the SHARED population file the UCDP and
# UNHCR per-capita connectors also use (data/aux/worldbank_population.csv, built
# by pipeline/build_population.py). Preferring it here means the two sibling
# acute-shock signals in THIS domain -- D1 disaster mortality (here) and D3
# conflict mortality (aux_ucdp) -- divide by the IDENTICAL denominator, which is
# exactly what their mirrored per-100k absolute anchors (ceiling 100/100k)
# require for cross-signal comparability.
_SHARED_POP = _REPO_ROOT / "data" / "aux" / "worldbank_population.csv"
# Secondary: a repo data/processed/worldbank.csv carrying SP.POP.TOTL, if present.
_REPO_WB = _REPO_ROOT / "data" / "processed" / "worldbank.csv"
# Fallback: legacy World Bank API cache (long format: iso3,year,series,value;
# now under data/raw/).
_LEGACY_WB = (
    _PROJECT_ROOT
    / "data" / "raw" / "worldbank_API_2026-05-28.csv"
)

SOURCE = "EM-DAT (CRED/UCLouvain), public custom request 2026-05-28"
LICENSE = "Non-commercial / academic; registration required (CRED/UCLouvain) -- PUBLICATION-ELIGIBILITY PENDING: confirm publication eligibility before public release"
YEAR_MIN, YEAR_MAX = 2020, 2024

_LICENSE_FLAG = (
    "PUBLICATION-ELIGIBILITY PENDING (design decision 3.1): EM-DAT non-commercial/academic only -- rows KEPT. "
    "Confirm CRED/UCLouvain publication eligibility (or line up a publishable "
    "disaster proxy) before public release; Structural Disruption D1 depends "
    "on these"
)
_MNAR_FLAG = (
    "MNAR-TRUE-ZERO: in-sample countries absent from EM-DAT treated as 0 events "
    "(accepted event-stock assumption, NOT drop-and-re-average); countries with "
    "no population denominator stay missing"
)
_WINDOW_FLAG = (
    "WINDOW-PRIOR: 5 complete years 2020-2024 (prior effort used 2020-2025; "
    "2025 partial excluded) -- re-examine, pending decision"
)
_ANCHOR_FLAG = (
    "ANCHOR-PRIOR: mortality ceiling 100/100k, affected ceiling 1.0 share -- "
    "absolute per-exposure anchors, re-examine"
)
_HEAT_FLAG = (
    "HEAT-WAVE-EXCLUDED (documented design decision): Disaster Subtype 'Heat wave' "
    "entries excluded, all years -- excess-mortality accounting tracks "
    "measurement capacity, not displacement-producing shock; cold-wave entries "
    "stay (flagged for watch)"
)


# --- population ------------------------------------------------------------

def _load_population() -> tuple[dict, str]:
    """Return ({iso3: population}, source_note).

    Preference order, so D1 shares the D3/UNHCR denominator:
      1. data/aux/worldbank_population.csv (the SHARED denominator) -- canonical.
      2. data/processed/worldbank.csv carrying SP.POP.TOTL, if present.
      3. legacy WB API cache (fallback; most-recent non-NaN year).
    """
    if _SHARED_POP.exists():
        pop = {}
        with open(_SHARED_POP, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    pop[r["iso3"]] = float(r["population"])
                except (ValueError, KeyError):
                    continue
        if pop:
            return pop, "shared data/aux/worldbank_population.csv (same denominator as UCDP/UNHCR)"
    if _REPO_WB.exists():
        df = pd.read_csv(_REPO_WB)
        # repo WB table shape is not yet finalized; accept a few likely layouts.
        if "SP.POP.TOTL" in df.columns:  # wide
            d = df[["iso3", "SP.POP.TOTL"]].dropna()
            return dict(zip(d["iso3"], d["SP.POP.TOTL"])), "repo data/processed/worldbank.csv (wide)"
        if {"series", "value", "year", "iso3"}.issubset(df.columns):  # long
            pop = df[(df["series"] == "SP.POP.TOTL") & df["value"].notna()]
            pop = pop.sort_values("year").groupby("iso3")["value"].last()
            return pop.to_dict(), "repo data/processed/worldbank.csv (long)"
        # unknown layout -> fall through to legacy
    if _LEGACY_WB.exists():
        df = pd.read_csv(_LEGACY_WB)
        pop = df[(df["series"] == "SP.POP.TOTL") & df["value"].notna()]
        pop = pop.sort_values("year").groupby("iso3")["value"].last()
        return pop.to_dict(), "FALLBACK legacy WB API cache (worldbank_API_2026-05-28.csv)"
    raise FileNotFoundError(
        "No population source found: neither data/processed/worldbank.csv nor "
        f"the legacy WB cache at {_LEGACY_WB}"
    )


# --- load + aggregate ------------------------------------------------------

def load() -> dict:
    """Return per-country raw components plus context.

    {iso3: {"deaths": float, "affected": float, "pop": float|None}} for every
    in-sample ISO3 (true-zero filled), plus a `_meta` dict.
    """
    files = glob.glob(_EMDAT_GLOB)
    if not files:
        raise FileNotFoundError(f"EM-DAT export not found: {_EMDAT_GLOB}")
    em = pd.read_excel(files[0], sheet_name=_EMDAT_SHEET)

    # The custom request is already filtered to Natural disasters; re-assert.
    if "Disaster Group" in em.columns:
        em = em[em["Disaster Group"] == "Natural"]
    # Owner design decision (§13): heat-wave entries are excluded at the
    # subtype level, ALL years; cold-wave entries stay.
    em = em[~em["Disaster Subtype"].str.contains("Heat", case=False, na=False)]
    em = em[(em["Start Year"] >= YEAR_MIN) & (em["Start Year"] <= YEAR_MAX)].copy()

    em["iso3"] = em["ISO"].map(iso_utils.normalize_to_iso3)
    sample = iso_utils.load_sample()
    sample_set = set(sample)
    em = em[em["iso3"].isin(sample_set)]

    agg = em.groupby("iso3").agg(
        deaths=("Total Deaths", "sum"),
        affected=("Total Affected", "sum"),
    )
    deaths = agg["deaths"].fillna(0.0).to_dict()
    affected = agg["affected"].fillna(0.0).to_dict()

    pop, pop_note = _load_population()

    out = {}
    for iso3 in sample:
        out[iso3] = {
            "deaths": float(deaths.get(iso3, 0.0)),       # absent -> true zero (MNAR)
            "affected": float(affected.get(iso3, 0.0)),   # absent -> true zero (MNAR)
            "pop": pop.get(iso3),                          # missing pop stays None
        }
    out["_meta"] = {
        "n_events": int(len(em)),
        "n_countries_with_events": int(em["iso3"].nunique()),
        "pop_note": pop_note,
        "emdat_file": Path(files[0]).name,
    }
    return out


def run():
    sample = iso_utils.load_sample()
    raw = load()
    meta = raw.pop("_meta")
    pop_fallback = meta["pop_note"].startswith("FALLBACK")

    # per-exposure components -----------------------------------------------
    mortality = {}   # deaths per 100k over window
    affected = {}    # affected as share of population
    for iso3 in sample:
        rec = raw[iso3]
        p = rec["pop"]
        if p is None or p <= 0:
            mortality[iso3] = None      # no denominator -> missing (NOT zero)
            affected[iso3] = None
        else:
            mortality[iso3] = rec["deaths"] / p * 1e5
            affected[iso3] = rec["affected"] / p

    dep_flag = []
    if pop_fallback:
        dep_flag = [
            "POP-DEPENDENCY: population from FALLBACK legacy WB cache "
            "(repo data/processed/worldbank.csv not yet present) -- rewire to "
            "the repo WB table once it lands"
        ]

    mort_spec = AnchorSpec(
        indicator="disaster_mortality_intensity",
        floor=0.0, ceiling=100.0,
        direction="high_risk",
        unit="disaster deaths per 100k (5-yr window 2020-2024)",
        anchor_source="absolute anchor: 100/100k ~= 0.1% pop killed over 5 yrs "
                      "= catastrophic mortality burden (~p99 of 2020-2024 obs)",
    )
    aff_spec = AnchorSpec(
        indicator="disaster_affected_intensity",
        floor=0.0, ceiling=1.0,
        direction="high_risk",
        unit="persons affected as share of population (5-yr window 2020-2024)",
        anchor_source="absolute anchor: 1.0 = entire population affected once "
                      "over the window; repeat-counting >1 clamped to 1.0",
    )

    mort_res = anchor_scale(mortality, mort_spec, sample=sample)
    aff_res = anchor_scale(affected, aff_spec, sample=sample)

    # combine the two component signals -> one indicator score (rule 9) ------
    combined = {}
    n_combined = 0
    for iso3 in sample:
        mean, _cov, below = drop_and_average(
            [mort_res.get(iso3), aff_res.get(iso3)],
            coverage_floor=0.5, min_present=1,  # 1 component suffices per country
        )
        combined[iso3] = mean
        if mean is not None:
            n_combined += 1

    # write data/processed/aux_emdat.csv ------------------------------------
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "iso3",
            "aux_emdat_disaster_shock",        # combined indicator 0-1
            "disaster_mortality_intensity",    # component 0-1
            "disaster_affected_intensity",     # component 0-1
        ])
        for iso3 in sample:
            c = combined.get(iso3)
            m = mort_res.get(iso3)
            a = aff_res.get(iso3)
            w.writerow([
                iso3,
                "" if c is None else round(c, 4),
                "" if m is None else round(m, 4),
                "" if a is None else round(a, 4),
            ])

    # register provenance + coverage (PER-SOURCE FRAGMENT) ------------------
    flags = [_LICENSE_FLAG, _MNAR_FLAG, _WINDOW_FLAG, _ANCHOR_FLAG, _HEAT_FLAG] + dep_flag
    rows = []
    for res in (mort_res, aff_res):
        row = res.register_row(
            source=SOURCE,
            series_id="EM-DAT Total Deaths / Total Affected (Natural)",
            license=LICENSE,
            extra_flags=flags,
        )
        row["year_min"], row["year_max"] = YEAR_MIN, YEAR_MAX
        rows.append(row)
    register.upsert_rows(rows, path=str(REGISTER_FRAGMENT))

    print(f"[aux_emdat] EM-DAT file: {meta['emdat_file']}")
    print(f"[aux_emdat] {meta['n_events']} events, "
          f"{meta['n_countries_with_events']} countries with events (window {YEAR_MIN}-{YEAR_MAX})")
    print(f"[aux_emdat] population: {meta['pop_note']}")
    print(f"[aux_emdat] wrote {OUT_PATH}")
    print(f"[aux_emdat] mortality coverage  {mort_res.meta['coverage_pct']:.1f}% "
          f"({mort_res.meta['n_present']}/{mort_res.meta['n_total']}) "
          f"below_floor={mort_res.meta['below_floor']}")
    print(f"[aux_emdat] affected  coverage  {aff_res.meta['coverage_pct']:.1f}% "
          f"({aff_res.meta['n_present']}/{aff_res.meta['n_total']}) "
          f"below_floor={aff_res.meta['below_floor']}")
    print(f"[aux_emdat] combined indicator present for {n_combined}/{len(sample)} countries")
    print(f"[aux_emdat] register fragment: {REGISTER_FRAGMENT}")
    print(f"[aux_emdat] FLAGS: {flags}")
    return {"mortality": mort_res, "affected": aff_res, "combined": combined}


if __name__ == "__main__":
    run()
