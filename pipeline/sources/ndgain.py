"""ND-GAIN connector -- Structural Disruption domain, D2 chronic climate.

Source for the Recruitment domain *Structural Disruption* (see
docs/scoring-rules.md and docs/METHODS.md), supplying the D2 "chronic climate
vulnerability" signal that previously had no loaded indicator. The raw ND-GAIN
zip's vulnerability.csv is staged to data/aux/ndgain_vulnerability_raw.csv.
This module REUSES the shared plumbing exactly like every other source:
  - iso_utils.normalize_to_iso3 / load_sample  (195-country ISO3 stack)
  - standardize.AnchorSpec / anchor_scale       (0-1 absolute anchoring, rule 1)
  - register.upsert_rows                          (provenance + coverage fragment)

WHAT IT MEASURES (see docs/METHODS.md, recruitment/structural-disruption):
  D2 signal s2.1 "climate exposure/sensitivity" -- a standing 0-1 composite of
  a country's vulnerability to climate disruption across six life-supporting
  sectors (food, water, health, ecosystems, habitat, infrastructure), netting
  exposure + sensitivity against adaptive capacity. ND-GAIN VULNERABILITY is
  already a 0-1 index on an absolute conceptual scale, so it is anchored at its
  native bounds (floor 0, ceiling 1), direction high_risk -- NOT a relative
  winsorized min-max. This is the chronic D2 driver, the slow-onset counterpart
  to the EM-DAT acute D1 disaster shock (aux_emdat.py).

SCOPE / CAVEATS:
  * D2 PARTIAL OPERATIONALIZATION. The methodology names three D2 signals:
    s2.1 climate exposure/sensitivity (THIS), s2.2 agrarian-livelihood
    dependence, s2.3 climate-driven outmigration pressure (the D2 displacement
    hinge). ND-GAIN supplies s2.1 only. s2.2 (agric-employment share) and s2.3
    (climate outmigration) are NOT supplied here -- D2 currently rests on this
    one signal. Caveat: within-driver average collapses to s2.1 until s2.2/s2.3
    land. ND-GAIN's vulnerability composite DOES embed food/water sensitivity,
    partially proxying s2.2, but not as a separable signal.
  * COVERAGE / structural-absence (rule 9). ND-GAIN covers ~185-192 countries;
    a handful of FLSRI-sample microstates are absent. Absent countries stay
    MISSING (drop-and-re-average), NOT 0 -- a country off the ND-GAIN panel is
    unmeasured, not "least vulnerable". Caveat noted.
  * GOVERNANCE OVERLAP NOTE. ND-GAIN's vulnerability score is constructed to be
    largely INDEPENDENT of governance/readiness (ND-GAIN keeps readiness, which
    carries governance, in a SEPARATE axis it does NOT mix into vulnerability).
    So this signal does not double-count the shared governance defeater Z
    (z.1 = wb_wgi_rule_of_law / v2x_rule), applied once at domain assembly.
    Note for the cross-domain de-dup pass; not blocking.
  * LICENSE: ND-GAIN is Creative-Commons free/open (publish-safe with
    attribution) -- no pre-publication requirement, unlike the EM-DAT D1 sibling.

Config: config/api-config/ndgain.yaml
Run:    python -m pipeline.sources.ndgain
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
CONFIG_PATH = _REPO_ROOT / "config" / "api-config" / "ndgain.yaml"
OUT_PATH = _REPO_ROOT / "data" / "processed" / "ndgain.csv"
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "ndgain.csv"
CACHE_DIR = _REPO_ROOT / "data" / "aux"


def _load_config():
    if not _HAS_YAML:
        raise RuntimeError(
            "PyYAML is required to read config/api-config/ndgain.yaml "
            "(pip install pyyaml)."
        )
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_vulnerability(cache_path, iso_col, year_col):
    """Return {iso3: vulnerability_value} for the snapshot year.

    The ND-GAIN vulnerability.csv is a wide panel: ISO3, Name, 1995..2023.
    Rows with a blank/non-numeric snapshot value are dropped (stay missing,
    never -> 0). ISO3 is normalized through the shared stack and filtered to
    the FLSRI sample downstream by anchor_scale's `sample` universe.
    """
    out = {}
    n_absent_year = 0
    with open(cache_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if year_col not in reader.fieldnames:
            raise KeyError(
                f"Snapshot-year column {year_col!r} not in {cache_path.name}; "
                f"available year columns end at {reader.fieldnames[-1]!r}"
            )
        for row in reader:
            iso3 = iso_utils.normalize_to_iso3(row.get(iso_col))
            if not iso3:
                continue
            raw = (row.get(year_col) or "").strip()
            if raw == "":
                n_absent_year += 1
                continue
            try:
                out[iso3] = float(raw)
            except ValueError:
                continue
    return out, n_absent_year


def run():
    cfg = _load_config()
    countries = iso_utils.load_sample()
    cache_path = CACHE_DIR / cfg["cache_file"]
    if not cache_path.exists():
        raise FileNotFoundError(
            f"ND-GAIN vulnerability cache not found: {cache_path}\n"
            "Stage resources/vulnerability/vulnerability.csv from "
            "ndgain_countryindex_2026.zip into data/aux/ first."
        )

    snapshot_year = str(cfg["snapshot_year"])
    raw_by_iso3, n_absent_year = _load_vulnerability(
        cache_path, cfg["iso_col"], snapshot_year
    )

    idx = cfg["indicator"]
    spec = AnchorSpec(
        indicator=idx["output_col"],
        floor=float(idx["floor"]),
        ceiling=float(idx["ceiling"]),
        direction=idx["direction"],
        unit=idx["unit"],
        anchor_source=idx["anchor_source"],
    )
    result = anchor_scale(raw_by_iso3, spec, sample=countries)

    flags = [
        "SINGLE-SIGNAL DRIVER (Structural Disruption D2 chronic climate, s2.1). "
        "ND-GAIN vulnerability supplies s2.1 climate exposure/sensitivity ONLY; "
        "D2 signals s2.2 agrarian-livelihood dependence and s2.3 climate-driven "
        "outmigration (the D2 displacement hinge) are NOT supplied here -- D2 "
        "rests on this one signal until those land (within-driver average)",
        "STRUCTURAL-ABSENCE vs UNMEASURED (rule 9): FLSRI-sample countries "
        "absent from the ND-GAIN panel (~185-192 coverage) stay MISSING "
        "(drop-and-re-average), NOT 0 -- off-panel = unmeasured, not "
        "'least vulnerable'",
        "GOVERNANCE-DEDUP NOTE: ND-GAIN holds governance in its SEPARATE "
        "readiness axis (NOT mixed into the vulnerability score used here), so "
        "this signal does not double-count the shared governance defeater Z "
        "(z.1 = wb_wgi_rule_of_law / v2x_rule, applied once at domain assembly)",
        "ABSOLUTE-ANCHOR (rule 1): scored at native 0-1 conceptual bounds, NOT "
        "a winsorized-relative fallback -- comparable across refreshes",
        f"SNAPSHOT-YEAR {snapshot_year}: most-recent fully-populated column in "
        "the 2026 edition; chronic standing condition (Condition B cB,2 ~= 1, "
        "continuously in-window)",
    ]

    register_rows = [
        result.register_row(
            source=cfg["source_title"],
            series_id=f"ND-GAIN vulnerability, snapshot {snapshot_year}",
            license=cfg["license"],
            extra_flags=flags,
        )
    ]
    register_rows[0]["year_min"] = snapshot_year
    register_rows[0]["year_max"] = snapshot_year

    print(
        f"[ndgain] {idx['output_col']}: dir={result.meta['direction']} "
        f"anchor={result.meta['anchor']} "
        f"coverage={result.meta['coverage_pct']:.1f}% "
        f"({result.meta['n_present']}/{result.meta['n_total']}) "
        f"below_floor={result.meta['below_floor']}"
    )
    if n_absent_year:
        print(f"[ndgain] {n_absent_year} panel rows had no {snapshot_year} value (dropped)")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3", idx["output_col"]])
        for iso3 in countries:
            v = result.get(iso3)
            w.writerow([iso3, "" if v is None else round(v, 4)])

    register.upsert_rows(register_rows, path=str(FRAGMENT_PATH))

    print(f"[ndgain] wrote {OUT_PATH}")
    print(f"[ndgain] wrote fragment {FRAGMENT_PATH} ({len(register_rows)} indicator row)")
    return result, register_rows


if __name__ == "__main__":
    run()
