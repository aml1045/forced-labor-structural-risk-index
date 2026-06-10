"""AUX source -- UCDP conflict-intensity (Structural Disruption D3, s3.1).

Source for the Recruitment domain *Structural Disruption* (see
docs/scoring-rules.md and docs/METHODS.md), supplying the D3 "conflict
intensity (direct)" signal. UCDP GED is used rather than ACLED, which is
licence-restricted (registration, no redistribution) and therefore EXCLUDED
from the published index. UCDP GED is the open equivalent: this connector
pulls GED v24.1 from the UCDP open download centre and stages a slim
country-year cache to data/aux/ucdp_ged_country_year_2019_2023.csv. It REUSES
the shared plumbing exactly like every other source:
  - iso_utils.normalize_to_iso3 / load_sample  (195-country ISO3 stack)
  - data/aux/worldbank_population.csv           (shared per-capita denominator)
  - standardize.AnchorSpec / anchor_scale       (0-1 absolute anchoring, rule 1)
  - register.upsert_rows                          (provenance + coverage fragment)

WHAT IT MEASURES (see docs/METHODS.md, recruitment/structural-disruption):
  D3 signal s3.1 "conflict intensity" -- event-based organized-violence deaths
  per 100k over a recent window. Sum the UCDP GED `best` fatality estimate per
  country over 2019-2023 (state-based + non-state + one-sided violence), divide
  by World Bank population to get a per-exposure rate, anchor to 0-1 absolutely.
  This is the direct D3 intensity signal; the D3 displacement hinge s3.2
  (IDPs + originating refugees) is the already-wired UNHCR series and is NOT
  re-counted here (displacement double-count guard, see docs/METHODS.md).

DIRECTION
  higher = more risk (more conflict mortality = more structural disruption /
  more population knocked loose into recruitment pathways). No inversion.

ANCHOR (PRIOR, pending decision)
  floor 0, ceiling 100 conflict deaths per 100k over the 5-yr window.
  Justification: 100/100k ~= 0.1% of the population killed by organized
  violence over 5 years = a catastrophic conflict-mortality burden. This
  MIRRORS the EM-DAT D1 mortality anchor (aux_emdat.py) so the two acute-shock
  signals in the SAME domain (D1 disaster, D3 conflict) sit on a comparable
  per-exposure scale. Not saturated: only the most extreme conflicts clamp
  (UKR ~434, ETH ~244, ISR ~240, AFG ~210 per 100k 2019-2023); the rest spread
  below the ceiling.

WINDOW
  2019-2023 (five COMPLETE calendar years; GED v24.1 ends at 2023). Mirrors the
  EM-DAT 5-year window length. PRIOR, pending decision -- not locked.

MISSING DATA (MNAR true-zero -- mirrors EM-DAT, PRIOR, pending decision)
  UCDP GED is an event-stock source: a country with NO event in the window
  recorded no organized-violence deaths. In-sample countries absent from GED
  are treated as TRUE ZERO (no conflict -> 0 deaths -> score 0) -- the same
  accepted MNAR assumption as EM-DAT (aux_emdat.py), NOT drop-and-re-average.
  Countries with NO usable population denominator stay MISSING (None), not 0.

LICENSE -- OPEN (no blocker)
  UCDP GED is CC BY 4.0, fully open / publish-safe with attribution. Unlike the
  EM-DAT D1 sibling, this is NOT a pre-publication requirement. (ACLED, the alternative,
  WOULD be a blocker -- which is exactly why UCDP was chosen.)

DEPENDENCY
  Needs raw population from data/aux/worldbank_population.csv (the shared
  denominator built by pipeline/build_population.py). Same dependency as the
  EM-DAT and UNHCR per-capita connectors.

Run:  python -m pipeline.sources.aux_ucdp
"""

from pathlib import Path
import csv

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = _REPO_ROOT / "config" / "api-config" / "ucdp.yaml"
OUT_PATH = _REPO_ROOT / "data" / "processed" / "aux_ucdp.csv"
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "aux_ucdp.csv"
CACHE_DIR = _REPO_ROOT / "data" / "aux"
POP_PATH = _REPO_ROOT / "data" / "aux" / "worldbank_population.csv"

# UCDP GED uses Correlates-of-War-style statenames with historical parentheticals
# the shared normalizer cannot resolve. These are UCDP-specific aliases, kept
# HERE (not edited into shared iso_utils) so this connector owns only its own
# files. All 7 verified to land in the 195 sample (ingest check 2026-06).
_UCDP_NAME_ALIASES = {
    "dr congo (zaire)": "COD",
    "kingdom of eswatini (swaziland)": "SWZ",
    "madagascar (malagasy)": "MDG",
    "myanmar (burma)": "MMR",
    "russia (soviet union)": "RUS",
    "yemen (north yemen)": "YEM",
    "zimbabwe (rhodesia)": "ZWE",
}


def _load_config():
    if not _HAS_YAML:
        raise RuntimeError(
            "PyYAML is required to read config/api-config/ucdp.yaml "
            "(pip install pyyaml)."
        )
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _ucdp_iso3(statename):
    key = (statename or "").strip().lower()
    if key in _UCDP_NAME_ALIASES:
        return _UCDP_NAME_ALIASES[key]
    return iso_utils.normalize_to_iso3(statename)


def _load_population():
    if not POP_PATH.exists():
        raise FileNotFoundError(
            f"Shared population denominator not found: {POP_PATH}\n"
            "Run `python -m pipeline.build_population` first."
        )
    pop = {}
    with open(POP_PATH, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                pop[r["iso3"]] = float(r["population"])
            except (ValueError, KeyError):
                continue
    return pop


def _aggregate_deaths(cfg):
    """Return ({iso3: summed best-deaths over window}, report).

    Sums GED `best` fatality estimates per country over the configured window
    and violence types. Unmatched statenames are reported (dropped, never
    silently mis-joined). Returns deaths only for countries that HAD events;
    true-zero fill for the rest happens in run().
    """
    cache_path = CACHE_DIR / cfg["cache_file"]
    if not cache_path.exists():
        raise FileNotFoundError(
            f"UCDP GED cache not found: {cache_path}\n"
            "Stage the slim country-year cache from GED v24.1 first "
            "(downloaded from ucdp.uu.se/downloads/ged/ged241-csv.zip)."
        )
    ymin, ymax = int(cfg["year_min"]), int(cfg["year_max"])
    include_types = {int(t) for t in cfg["include_violence_types"]}
    ccol, dcol, ycol = cfg["country_col"], cfg["deaths_col"], cfg["year_col"]

    deaths = {}
    seen = set()
    unmatched = set()
    n_events_rows = 0
    with open(cache_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                y = int(row[ycol])
            except (ValueError, KeyError):
                continue
            if not (ymin <= y <= ymax):
                continue
            try:
                tov = int(row.get("type_of_violence", 0))
            except ValueError:
                tov = 0
            if include_types and tov not in include_types:
                continue
            name = row.get(ccol, "")
            seen.add(name)
            iso3 = _ucdp_iso3(name)
            if iso3 is None:
                unmatched.add(name)
                continue
            try:
                d = float(row.get(dcol) or 0.0)
            except ValueError:
                d = 0.0
            deaths[iso3] = deaths.get(iso3, 0.0) + d
            n_events_rows += 1
    report = {
        "n_countries_in_panel": len(seen),
        "n_iso3_with_deaths": len(deaths),
        "unmatched_names": sorted(unmatched),
        "n_rows_summed": n_events_rows,
    }
    return deaths, report


def run():
    cfg = _load_config()
    sample = iso_utils.load_sample()
    sample_set = set(sample)

    deaths, report = _aggregate_deaths(cfg)
    if report["unmatched_names"]:
        print(f"[aux_ucdp] WARN unmatched UCDP statenames (dropped): "
              f"{report['unmatched_names']}")

    pop = _load_population()

    # per-exposure: deaths per 100k. In-sample countries absent from GED ->
    # TRUE ZERO (MNAR event-stock assumption). No population -> missing (None).
    per100k = {}
    for iso3 in sample:
        d = deaths.get(iso3, 0.0)   # absent from GED -> 0 events (true zero)
        p = pop.get(iso3)
        if p is None or p <= 0:
            per100k[iso3] = None     # no denominator -> missing (NOT zero)
        else:
            per100k[iso3] = d / p * 1e5

    idx = cfg["indicator"]
    spec = AnchorSpec(
        indicator=idx["output_col"],
        floor=float(idx["floor"]),
        ceiling=float(idx["ceiling"]),
        direction=idx["direction"],
        unit=idx["unit"],
        anchor_source=idx["anchor_source"],
    )
    result = anchor_scale(per100k, spec, sample=sample)

    flags = [
        "SOURCE-CHOICE (Structural Disruption D3 conflict intensity, s3.1). UCDP "
        "GED chosen over ACLED, which is licence-restricted (registration, no "
        "redistribution) and EXCLUDED from the published index -- 'use UCDP, NOT "
        "ACLED'",
        "MNAR-TRUE-ZERO: in-sample countries absent from UCDP GED treated as 0 "
        "events (accepted event-stock assumption, mirrors EM-DAT, NOT "
        "drop-and-re-average); countries with no population denominator stay "
        "missing",
        "WINDOW-PRIOR: 5 complete years 2019-2023 (GED v24.1 ends 2023; mirrors "
        "the EM-DAT 5-yr window length) -- pending decision, not locked",
        "ANCHOR-PRIOR: ceiling 100 deaths/100k over the window -- absolute "
        "per-exposure anchor mirroring EM-DAT D1 for cross-signal comparability; "
        "UKR/ETH/ISR/AFG clamp at ceiling -- pending decision, not locked",
        "DISPLACEMENT DOUBLE-COUNT GUARD: this is the DIRECT intensity signal "
        "s3.1 ONLY; the D3 displacement hinge s3.2 (IDPs + originating refugees) "
        "is the already-wired UNHCR series and is NOT re-counted here",
        "UNIT-CAUTION: D3 degrades most under country-level aggregation (conflict "
        "is natively subnational); subnational disaggregation = future work via "
        "GED geocodes, not a reason to change the unit now",
        "LICENSE OPEN: UCDP GED is CC BY 4.0 publish-safe -- NOT a "
        "pre-publication requirement (unlike the EM-DAT D1 sibling and the rejected ACLED)",
    ]

    register_rows = [
        result.register_row(
            source=cfg["source_title"],
            series_id=f"UCDP GED v24.1 best-deaths, types "
                      f"{cfg['include_violence_types']}, window "
                      f"{cfg['year_min']}-{cfg['year_max']}",
            license=cfg["license"],
            extra_flags=flags,
        )
    ]
    register_rows[0]["year_min"] = cfg["year_min"]
    register_rows[0]["year_max"] = cfg["year_max"]

    print(
        f"[aux_ucdp] {idx['output_col']}: dir={result.meta['direction']} "
        f"anchor={result.meta['anchor']} "
        f"coverage={result.meta['coverage_pct']:.1f}% "
        f"({result.meta['n_present']}/{result.meta['n_total']}) "
        f"below_floor={result.meta['below_floor']}"
    )
    print(f"[aux_ucdp] {report['n_iso3_with_deaths']} in-sample countries with "
          f"conflict events ({report['n_rows_summed']} country-year-type rows summed)")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3", idx["output_col"]])
        for iso3 in sample:
            v = result.get(iso3)
            w.writerow([iso3, "" if v is None else round(v, 4)])

    register.upsert_rows(register_rows, path=str(FRAGMENT_PATH))

    print(f"[aux_ucdp] wrote {OUT_PATH}")
    print(f"[aux_ucdp] wrote fragment {FRAGMENT_PATH} ({len(register_rows)} indicator row)")
    return result, register_rows


if __name__ == "__main__":
    run()
