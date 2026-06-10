"""State Production of Unfreedom (Exploitation Domain 3) — register fragment.

Source REUSE/VERIFY + GAP-RECORD module. This domain's *defensible,
sourceable* signals already live in SHARED, already-wired fragments and are
NOT (re)written here (verified read-only — see docs/data-provenance.md):

  - D4 state-imposed forced labour (SIFL)  -> v2xcl_slave  (vdem.csv)
  - D5 active state capture / complicity    -> v2xnp_client (vdem.csv, reversed
                                               clientelism), trace_bribery_total
                                               (static_indices.csv),
                                               wb_bribery_incidence (worldbank.csv)

  This module does NOT write vdem.csv / static_indices.csv / worldbank.csv /
  the master register — those are produced by other source connectors. This
  module owns ONLY config/data_register.d/state_production.csv.

What this module DOES write: honest **unmapped / low-confidence GAP note-rows**
for the three bespoke legal-coding drivers that have NO single defensible
dataset (flags F, B, and the driver skeleton; see docs/METHODS.md). It does NOT
fabricate coverage, does NOT over-proxy the unsourceable
legal-coding drivers (NORMLEX C105 ratification is near-universal / low-variance
and ratification != compliance -> weak alone), and notes the three
pending source decisions. This mirrors the note-row
convention used by econ_structure_demand.csv (NONE series_id, 0 countries,
0.0 coverage, HARD-GAP / LOW-CONFIDENCE flag string with a pending-decision pointer).

Conforms to scoring-rules v1 (docs/scoring-rules.md):
  Rule 1  — direction set explicitly (all three drivers: higher = MORE risk,
            high_risk — the State-Production double-edge inversion).
  Rule 9  — never missing->0: these are recorded as flagged low-confidence GAP
            rows so the data stage records reduced coverage rather than silently
            scoring (or zeroing) the driver.

No data/processed table is written: the three drivers are UNMAPPED (no data to
standardize). The verified D4/D5 signals already have processed columns in
data/processed/vdem.csv, static_indices.csv, worldbank.csv.

Run:  python -m pipeline.sources.state_production
"""

from pathlib import Path

from pipeline import register

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "state_production.csv"

SOURCE_GAP = "(unmapped -- bespoke legal-coding gap)"

# --- the three bespoke-gap note-rows (D1, D2, D3) --------------------------
# Schema: indicator, source, series_id, countries, year_min, year_max,
#         license, direction, anchor, coverage_pct, flags
ROWS = [
    {
        "indicator": "spu_d1_tied_status_construction",
        "source": SOURCE_GAP,
        "series_id": "NONE",
        "countries": 0,
        "year_min": "",
        "year_max": "",
        "license": "n/a",
        "direction": "high_risk",
        "anchor": "n/a (no single cross-national tied-status legal-design coding)",
        "coverage_pct": 0.0,
        "flags": (
            "HARD-GAP / LOW-CONFIDENCE (rule 9: never missing->0). D1 tied-status "
            "regime CONSTRUCTION (kafala / sponsorship / single-employer work-permit "
            "legal design + bindingness; s1.1 legal-design, s1.2 tied-population "
            "scope). NO single defensible cross-national dataset -- legal coding of "
            "tied-status bindingness is uneven (flag F). Do NOT over-proxy: "
            "ILO NORMLEX C105/C029 ratification is near-universal / low-variance and "
            "ratification != compliance -- weak alone, NOT a "
            "tied-status construction measure. Candidate manual-coding partials only "
            "(ILO labour-migration frameworks; MIPEX immigration policy). UNMAPPED -- "
            "do not fabricate coverage. BOUNDARY: scores the CONSTRUCTION act only; the "
            "worker-facing DEPLOYMENT modulator sits in Foreclosed Exit (flag A) "
            "-- confirm not the same measured variable entered twice, and "
            "test D1-vs-D2 collinearity. Pending decision: "
            "tied-status legal coding."
        ),
    },
    {
        "indicator": "spu_d2_precarity_immigration_architecture",
        "source": SOURCE_GAP,
        "series_id": "NONE",
        "countries": 0,
        "year_min": "",
        "year_max": "",
        "license": "n/a",
        "direction": "high_risk",
        "anchor": "n/a (no single cross-national status-conditionality / deportability legal coding)",
        "coverage_pct": 0.0,
        "flags": (
            "HARD-GAP / LOW-CONFIDENCE (rule 9: never missing->0). D2 precarity-fashioning "
            "immigration architecture (status conditionality / revocability design s2.1; "
            "deportability-as-law legal exposure s2.2). NO single defensible cross-national "
            "dataset -- this is legal-design coding of revocability / standing removal "
            "exposure, not deportation event counts (event counts are detection-biased and "
            "excluded under the structural-conditions premise; D2 reading rule). "
            "Candidate manual-coding partial: MIPEX (immigration policy) -- partial, "
            "manual, NOT a deportability-as-law measure; do NOT over-proxy. UNMAPPED -- "
            "do not fabricate coverage. BOUNDARY: the ABSENCE of relieving enforcement is "
            "the guardian gate (scored once elsewhere), NOT this generating driver; check "
            "D1-vs-D2 collinearity (flag A). Pending decision: "
            "tied-status legal coding (immigration-architecture scope)."
        ),
    },
    {
        "indicator": "spu_d3_protective_floor_deregulation",
        "source": SOURCE_GAP,
        "series_id": "NONE",
        "countries": 0,
        "year_min": "",
        "year_max": "",
        "license": "n/a",
        "direction": "high_risk",
        "anchor": "n/a (no single cross-national labour-protection-floor coverage / deregulation coding)",
        "coverage_pct": 0.0,
        "flags": (
            "HARD-GAP / LOW-CONFIDENCE (rule 9: never missing->0). D3 protective-floor "
            "DEREGULATION = the LEGAL CONTENT of (non-)protection: categorical exclusion "
            "of high-exposure categories (domestic / agricultural / informal / migrant) "
            "from core labour protections (s3.1) + legal room left for coercive "
            "wage/contract structures (s3.2). NO single defensible cross-national dataset "
            "of labour-law COVERAGE/exclusion. Do NOT over-proxy: ILO NORMLEX C105 "
            "ratification is near-universal / low-variance and ratification != compliance "
            "-- it is a de jure RATIFICATION status, NOT a "
            "protective-floor-coverage / deregulation measure; use only as ONE weak input "
            "paired with enforcement, never alone. UNMAPPED -- do not fabricate coverage. "
            "HONESTY CAVEAT (flag B): deregulation -> FORCED-LABOUR-magnitude is a "
            "mechanistic INFERENCE, not a directly-evidenced cross-national dose-response. "
            "BOUNDARY: scores legal CONTENT of the floor, NOT enforcement of an existing "
            "floor (= guardian gate, scored once elsewhere); keep the two "
            "from sharing a measured variable (flag B). s3.2 connecting-flagged to the "
            "cross-phase DEBT theme / Foreclosed-Exit exit-cost (flag C). Pending decision: "
            "reform-implementation verification (+ tied-status legal "
            "coding for labour-law coverage coding)."
        ),
    },
]


def run():
    register.upsert_rows(ROWS, path=str(FRAGMENT_PATH))
    print(f"[state_production] wrote {len(ROWS)} bespoke-gap note-rows -> {FRAGMENT_PATH}")
    print("[state_production] NO data/processed table written (D1/D2/D3 UNMAPPED gaps).")
    print("[state_production] verified-elsewhere (read-only, NOT written here):")
    print("    D4 SIFL          -> v2xcl_slave   (vdem.csv)   [low-confidence SPU-r9; CIRCULARITY FLAG]")
    print("    D5 capture/compl -> v2xnp_client  (vdem.csv), trace_bribery_total (static_indices.csv),")
    print("                        wb_bribery_incidence (worldbank.csv)")
    for r in ROWS:
        print(f"    GAP {r['indicator']:42s} dir={r['direction']} coverage={r['coverage_pct']}%")
    return ROWS


if __name__ == "__main__":
    run()
