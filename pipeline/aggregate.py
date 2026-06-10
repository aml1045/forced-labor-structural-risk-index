"""Aggregate: signals -> domain -> phase, per LOCKED scoring-rules v1.

Replaces the STUB. Implements:
  rule 2/5 -- equal-weight average of present signals into each domain
              (the registry's per-domain indicator structure is collapsed to a
              flat signal set by crosswalk.py; for v1 every domain is a single
              equal-weight average of its mapped 0-1 risk signals).
  rule 6 -- equal-weight average of present domains into each phase.
  rule 8 -- ONE domain-level attenuate-only governance modulator:
              domain_score = (1 - f_gov) * domain_raw,  f_gov = 1 - wgi_rol.
              Applied ONCE per domain, never per signal/driver. Missing
              governance -> no attenuation (f_gov = 0), recorded.
  rule 9 -- drop-and-re-average with a >=50% / >=2 coverage floor. Below the
              floor a domain is NOT-SCORED for that country (flagged, never 0).
              A domain whose DESIGN is flagged low_confidence/insufficient_data
              in the crosswalk carries that flag regardless of per-country
              coverage. Never missing -> 0.

Inputs are the already-standardized 0-1 RISK columns in data/processed/*.csv.
Reuses iso_utils' country universe via run.py; this module is pure-stdlib over
the processed tables (loaded by load_processed).
"""
import csv
import os

from pipeline import crosswalk

HERE = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(HERE, "..", "data", "processed")

# rule 9 coverage floor: a domain scores only with >= max(2, 50% of mapped
# signals) present; a 1-signal domain (by design) needs that 1 present but is
# carried low-confidence by its crosswalk flag.
COVERAGE_FRACTION_FLOOR = 0.50
COVERAGE_MIN_SIGNALS = 2


def _to_float(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in ("na", "nan", "none"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_processed(processed_dir=PROCESSED_DIR):
    """Load every processed table into {table_slug: {iso3: {col: float}}}."""
    tables = {}
    for fname in os.listdir(processed_dir):
        if not fname.endswith(".csv"):
            continue
        slug = fname[:-4]
        path = os.path.join(processed_dir, fname)
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            cols = [c for c in (reader.fieldnames or []) if c != "iso3"]
            rows = {}
            for r in reader:
                iso3 = (r.get("iso3") or "").strip()
                if not iso3:
                    continue
                rows[iso3] = {c: _to_float(r.get(c)) for c in cols}
            tables[slug] = rows
    return tables


def _signal_value(tables, iso3, table_slug, column):
    return tables.get(table_slug, {}).get(iso3, {}).get(column)


def _coverage_floor_met(n_present, n_total):
    if n_present < COVERAGE_MIN_SIGNALS:
        return False
    return n_present >= max(COVERAGE_MIN_SIGNALS, COVERAGE_FRACTION_FLOOR * n_total)


def _governance_f(tables, iso3):
    """Governance attenuation dial f_gov in [0,1], applied as (1 - f_gov)*raw.

    RESOLVED: the PROTECTIVE reading below is
    adopted. This term exists ONLY in this run.py reproduction build (Rule 8b);
    the published v0.4-SPU site build contains no `(1 - f_gov)` term at all --
    it de-biases governance-correlated signals instead (Rule 8a, see
    docs/METHODS.md §6).

    History (kept as the decision record): the brief's locked formula read
    `f_gov = 1 - wb_wgi_rule_of_law`, written on the assumption that the column
    is a GOODNESS encoding. The PROCESSED column is RISK-aligned (high = weak
    governance; SOM 0.94 worst, NOR 0.10 best -- verified), so the literal
    substitution double-inverts: it attenuates the WORST-governed countries
    hardest (AFG R 0.73 -> 0.08), inverting the index's central thesis and
    collapsing face validity (Kendall's tau vs unattenuated ~0.15). Every
    accepted findings paper describes governance as a PROTECTIVE defeater
    (strong governance attenuates risk), so the adopted reading is:
        good_gov = 1 - risk_aligned_wgi      (high = strong rule of law)
        f_gov    = good_gov                  (strong governance -> more attenuation)
        domain_score = (1 - f_gov) * raw     (weak governance -> little attenuation)
    The literal/as-locked reading remains in the build notes as a sensitivity
    contrast; it is a recorded alternative, not an open question.
    """
    g = _signal_value(tables, iso3, crosswalk.GOVERNANCE_TABLE,
                      crosswalk.GOVERNANCE_COLUMN)
    if g is None:
        return None
    g = max(0.0, min(1.0, g))
    good_gov = 1.0 - g          # invert risk-aligned column to goodness
    return good_gov             # f_gov: strong governance attenuates (protective)


def aggregate_country(iso3, tables, apply_governance=True):
    """Return the full per-country score object for one ISO3.

    {
      domains: {slug: {raw, score, n_present, n_total, scored(bool),
                       coverage_flag, design_confidence, flags,
                       circularity_flag(bool)}},
      phases:  {phase: {score, n_domains_scored, scored(bool)}},
      governance_f: float|None,
      governance_applied: bool,
    }
    """
    f_gov = _governance_f(tables, iso3) if apply_governance else None
    gov_applied = f_gov is not None

    domains = {}
    for slug, spec in crosswalk.CROSSWALK.items():
        sigs = spec["signals"]
        vals = [_signal_value(tables, iso3, t, c) for (t, c) in sigs]
        present = [v for v in vals if v is not None]
        n_present, n_total = len(present), len(sigs)

        floor_met = _coverage_floor_met(n_present, n_total)
        raw = sum(present) / n_present if present else None  # rule 2/5 mean

        # Single-signal-by-DESIGN domains (n_total == 1) can't meet the >=2
        # floor mechanically; they are scored but always low-confidence (their
        # crosswalk flag already says so). Honour that rather than not-scoring.
        single_by_design = (n_total == 1 and n_present == 1)
        scored = (raw is not None) and (floor_met or single_by_design)

        if scored and gov_applied:
            score = (1.0 - f_gov) * raw
        elif scored:
            score = raw
        else:
            score = None

        if not scored:
            coverage_flag = "not_scored_coverage"
        elif spec["confidence"] == "insufficient_data":
            coverage_flag = "insufficient_data"
        elif spec["confidence"] == "low_confidence" or single_by_design or not floor_met:
            coverage_flag = "low_confidence"
        else:
            coverage_flag = "ok"

        domains[slug] = {
            "raw": raw,
            "score": score,
            "n_present": n_present,
            "n_total": n_total,
            "scored": scored,
            "coverage_flag": coverage_flag,
            "design_confidence": spec["confidence"],
            "flags": spec.get("flags", []),
            "circularity_flag": bool(spec.get("circularity_signals")),
            "product2_only": bool(spec.get("product2_only")),
        }

    # rule 6: equal-weight average of present (scored) domains into each phase.
    # Product-1 phases only (recruitment, exploitation). Monetization excluded.
    phases = {}
    for phase, phase_domains in crosswalk.PRODUCT1_PHASES.items():
        dscores = [domains[d]["score"] for d in phase_domains
                   if domains[d]["scored"] and domains[d]["score"] is not None]
        n_scored = len(dscores)
        n_total = len(phase_domains)
        # phase coverage floor (rule 9): >=50% and >=2 domains scored
        phase_floor_met = _coverage_floor_met(n_scored, n_total)
        phase_score = sum(dscores) / n_scored if (dscores and phase_floor_met) else None
        phases[phase] = {
            "score": phase_score,
            "n_domains_scored": n_scored,
            "n_domains_total": n_total,
            "scored": phase_score is not None,
        }

    return {
        "domains": domains,
        "phases": phases,
        "governance_f": f_gov,
        "governance_applied": gov_applied,
    }


def aggregate_all(countries, tables, apply_governance=True):
    return {c: aggregate_country(c, tables, apply_governance=apply_governance)
            for c in countries}
