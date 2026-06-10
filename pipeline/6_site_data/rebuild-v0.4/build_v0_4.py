#!/usr/bin/env python3
"""BUILD v0.4 -- FLSRI: the governance down-weight lever.

STATUS (2026-06-09): PROMOTED TO PUBLISHED BUILD. The SPU-only w=0.5 arm of
this script (the v0_4_spu_w05_* columns) is the build the site displays; it was
re-checked against the pre-registered validation suite and passed (see
docs/validation/validation_results_v2_v04spu_w05.json and docs/METHODS.md §6).
The remaining arms (broad sweep, other weights, IPUMS replacement) are reported
sensitivity diagnostics. The EXPLORATORY framing below is retained as the
original design rationale.

Read-only on the locked pipeline. Reuses the locked modules (crosswalk, aggregate,
composite) ONLY to read processed signals + the crosswalk map and to reproduce the
LOCKED reference. ALL experimental aggregation logic lives in THIS file. It re-implements
the v0.3 aggregation EXACTLY (corr 1.0 vs persisted v0.3) and adds ONE new lever on top,
so every v0.4 delta is attributable to that lever alone.

----------------------------------------------------------------------------------
THE LEVER (named by rebuild-structure-report.md s6 as the ONE remaining untested move):
  DOWN-WEIGHT (and, where a defensible FL-specific replacement exists on disk, REPLACE)
  the governance-correlated LEVEL signals -- not just re-shape them.

The structure report showed the conditional/transform restoration de-correlates signals
but WASHES OUT at the composite because equal-weight domain averaging + the geometric
R x E spine re-homogenise toward the governance axis. The proposed cure: stop averaging
the governance-correlated levels in at full weight.

OPERATIONALIZATION (transparent + tunable):
  1. Measure each generating signal's governance R^2 once (per-signal, as actually entered
     in v0.3, incl. the two temporal overrides), n>=10.
  2. Flag any signal with R^2_gov >= GOV_THRESHOLD (default 0.55) as a "governance-correlated
     LEVEL." Temporal/transformed signals are measured AS-USED, so a feature whose temporal
     re-derivation already broke the correlation is NOT flagged (correct: we only down-weight
     what is still governance-laden after the cheaper fixes).
  3. At the WITHIN-DOMAIN averaging step, weight a flagged signal by W_GOV (sweep:
     1.0 = v0.3 reproduction, 0.5, 0.25, 0.0 = drop). All other signals keep weight 1.0.
     The coverage-floor denominator still COUNTS a flagged signal as present (so down-
     weighting does not silently fail the floor) UNLESS W_GOV == 0.0 (drop = truly absent,
     which is the honest accounting for a dropped signal).

REPLACEMENT (tested, reported honestly):
  An FL-specific REPLACEMENT (IPUMS vulnerable employment, R^2_gov 0.29, 71% independent
  variance) exists for the informal-employment level -- BUT it covers only 82 of 195 scored
  countries. Swapping a 143-country level for an 82-country signal pushes >60 countries
  below the coverage floor. We therefore test replacement as a flagged SENSITIVITY only and
  report the coverage cost; the main v0.4 lever is down-weighting, which preserves coverage.

WHY THIS IS THE LAST LEVER, NOT A NEW ONE: v0.1/v0.2 removed the governance MODULATOR
(the (1-f_gov) dial). That left the governance-correlated LEVEL SIGNALS still being averaged
in. This build is the only remaining move that acts on those levels directly rather than
re-shaping them (transforms, v0.3) or re-deriving them temporally (also v0.3).

Confidence: EXPLORATORY throughout; effects labelled; nothing fabricated; nothing locked.
Outputs under .../experiments/rebuild-v0.4/.
"""
import sys, os, warnings
import numpy as np, pandas as pd
from scipy import stats
warnings.filterwarnings("ignore")

# ---- paths (all paths now come from config/site_data_paths.py;
#      analysis logic below is byte-for-byte the prior-effort generator) ----
HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, _REPO_ROOT)
from config import site_data_paths as P  # noqa: E402
EXP   = P.EXP
REPO  = P.REPO
V01   = P.V01
V02   = P.V02
V03   = P.V03
TEMP  = P.TEMP
KAF   = P.KAF
IPUMS = P.IPUMS
OUT   = HERE
sys.path.insert(0, REPO)
from pipeline import crosswalk
from pipeline import aggregate as agg

PROC = os.path.join(REPO, "data", "processed")
scores = pd.read_csv(os.path.join(REPO, "outputs", "scores.csv"))
tables = agg.load_processed(PROC)
countries = sorted(scores.iso3.dropna().tolist())
cname = scores.set_index("iso3")["country_name"].to_dict()

GOV_T, GOV_C = crosswalk.GOVERNANCE_TABLE, crosswalk.GOVERNANCE_COLUMN
# Governance reference used ONLY to flag governance-correlated levels (R2gov).
# wgi (default, = original v0.4) | vdem (V-Dem v2x_rule) | blend (50/50, risk-aligned).
# This affects WHICH signals are down-weighted, not the scoring arithmetic itself.
_GOV_REF = os.environ.get("FLSRI_GOV_REF", "wgi").lower()
_wgi_g = {iso: tables.get(GOV_T, {}).get(iso, {}).get(GOV_C) for iso in countries}
_vdem_g = {iso: tables.get("vdem", {}).get(iso, {}).get("v2x_rule") for iso in countries}
def _gov_ref_value(iso):
    a, b = _wgi_g.get(iso), _vdem_g.get(iso)
    if _GOV_REF == "vdem":
        return b
    if _GOV_REF == "blend":
        if a is None:
            return b
        if b is None:
            return a
        return 0.5 * a + 0.5 * b
    return a  # wgi (default)
GOV = pd.Series({iso: _gov_ref_value(iso) for iso in countries}, name="gov")

# ---- carried experimental signals (identical to v0.3) ----
fl = pd.read_csv(os.path.join(EXP, "fl-signals", "fl_candidate_signals.csv"))
GLOTIP = pd.Series({r.iso3: (r.glotip_fl_share_of_detected
                             if pd.notna(r.glotip_fl_share_of_detected) else None)
                    for r in fl.itertuples()}, name="glotip")
dem = pd.read_csv(os.path.join(V02, "demscore_signal.csv"))
DEM = pd.Series({r.iso3: float(r.demscore_tied_status_signal) for r in dem.itertuples()},
                name="demscore")
KAFALA_ADOPT = {"ARE", "QAT", "SAU", "KWT", "BHR", "OMN", "JOR", "LBN"}
kaf = pd.read_csv(os.path.join(KAF, "kafala_signal.csv"))
KAFALA = pd.Series({r.iso3: float(r.kafala_tied_status_signal)
                    for r in kaf.itertuples() if r.iso3 in KAFALA_ADOPT}, name="kafala")
tf = pd.read_csv(os.path.join(TEMP, "temporal_features.csv")).set_index("iso3")
def _col(c):
    return {iso: (float(tf.at[iso, c]) if iso in tf.index and pd.notna(tf.at[iso, c]) else None)
            for iso in countries}
TEMPORAL_OVERRIDE = {
    ("ndgain", "ndgain_climate_vulnerability"):        _col("ndgain_vuln_temporal"),
    ("aux_unctad", "aux_unctad_export_concentration"): _col("unctad_export_concentration_temporal"),
}
# IPUMS replacement candidate (sensitivity only). The IPUMS-derived aggregate
# table is a gated local input (IPUMS conditions of use; not redistributed --
# see docs/REPRODUCING.md). When absent, the sensitivity arm is skipped; the
# shipped configuration does not depend on it.
_IPUMS_FILE = os.path.join(IPUMS, "candidate_signals_country.csv")
HAS_IPUMS = os.path.exists(_IPUMS_FILE)
if HAS_IPUMS:
    ipums = pd.read_csv(_IPUMS_FILE)
    IP_VULN = pd.Series({r.iso3: (float(r.vulnerable_employment)
                                  if pd.notna(r.vulnerable_employment) else None)
                         for r in ipums.itertuples()}, name="ip_vuln")
else:
    IP_VULN = pd.Series(dtype=float, name="ip_vuln")
    print("NOTE: IPUMS-derived gated input absent; IPUMS-replacement "
          "sensitivity arm skipped (shipped scores unaffected).")

# ---------------- fenced modeled-estimate overlay (data-staging layer) ----------------
# Owner design decision §8/§12: two accepted modeled estimates (NZL/SGP
# ep_informal_employment_share) enter from data/staging/modeled_estimates.csv, NOT
# from the data/processed connector outputs. A modeled value fills a MISSING observed
# signal ONLY (never overwrites an observed value); it is marked so the domain and
# indicator exporters fence it (badge + interval) rather than present it as observed.
_MODELED_PATH = os.path.join(REPO, "data", "staging", "modeled_estimates.csv")
MODELED = {}          # (table, col, iso3) -> {"value","lo","hi","status","provenance"}
MODELED_DOMAINS = {}  # iso3 -> {domain_slug: (table, col)} fed by a modeled estimate
if os.path.exists(_MODELED_PATH):
    _md = pd.read_csv(_MODELED_PATH)
    _sig2dom = {(t, c): slug for slug, spec in crosswalk.CROSSWALK.items()
                for (t, c) in spec["signals"]}
    for _r in _md.itertuples():
        MODELED[(_r.table, _r.signal, _r.iso3)] = {
            "value": float(_r.value), "lo": float(_r.pi80_lo), "hi": float(_r.pi80_hi),
            "status": str(_r.status), "provenance": str(_r.provenance)}
        _slug = _sig2dom.get((_r.table, _r.signal))
        if _slug:
            MODELED_DOMAINS.setdefault(_r.iso3, {})[_slug] = (_r.table, _r.signal)

# ---------------- stats helpers ----------------
def spearman(a, b):
    m = a.notna() & b.notna()
    if m.sum() < 10: return (np.nan, np.nan, int(m.sum()))
    r, p = stats.spearmanr(a[m], b[m]); return (round(float(r), 3), float(p), int(m.sum()))
def pearson_r2(a, b):
    m = a.notna() & b.notna()
    if m.sum() < 10: return np.nan
    return round(float(np.corrcoef(a[m], b[m])[0, 1] ** 2), 3)
def kendall(a, b):
    m = a.notna() & b.notna()
    if m.sum() < 10: return np.nan
    return round(float(stats.kendalltau(a[m], b[m])[0]), 3)
def resid_sd(cs):
    m = cs.notna() & GOV.notna()
    if m.sum() < 10: return np.nan
    x, y = GOV[m].values, cs[m].values
    b = np.polyfit(x, y, 1)
    return round(float(np.std(y - np.polyval(b, x))), 4)

def sig(iso, t, c, use_temporal):
    if use_temporal and (t, c) in TEMPORAL_OVERRIDE:
        v = TEMPORAL_OVERRIDE[(t, c)].get(iso)
        if v is not None:
            return v
    v = tables.get(t, {}).get(iso, {}).get(c)
    if v is None:
        # fenced modeled estimate fills a MISSING observed value only (never overwrites)
        m = MODELED.get((t, c, iso))
        if m is not None:
            return m["value"]
    return v if v is not None else None

# ---------------- transform / gate library (identical to v0.3) ----------------
def hump_gate(x, peak=0.7, drop=0.5):
    if x is None: return None
    # design decision: the non-monotonic hump (which scored the highest-poverty /
    # highest-mobility-constraint countries DOWN, undocumented) is REMOVED by default ->
    # identity (monotone). Set FLSRI_KEEP_HUMP=1 to reproduce the old build for comparison.
    if os.environ.get("FLSRI_KEEP_HUMP") != "1": return x
    if x <= peak: return x
    frac = (x - peak) / (1.0 - peak)
    return peak - frac * (drop * peak)
def materiality_ramp(x, cut=0.20):
    if x is None: return None
    if x >= cut: return x
    return x * (x / cut)
def cho_gate(x):     return hump_gate(x, peak=0.7, drop=0.5)
def poverty_hump(x): return hump_gate(x, peak=0.7, drop=0.5)

DEM_DOMAINS = {"state-production-of-unfreedom", "constrained-mobility"}
KAF_DOMAINS = {"state-production-of-unfreedom", "constrained-mobility"}
DROP_SIGNALS = {("vdem", "v2xcl_slave")}

# =====================================================================
# STEP 1+2: measure per-signal governance R^2 AS ENTERED, flag the levels
# =====================================================================
GOV_THRESHOLD = 0.55  # >= this = "governance-correlated LEVEL", a down-weight target

def signal_series_as_used(t, c):
    """The signal vector exactly as v0.3 enters it (incl. temporal override + transform)."""
    out = {}
    for iso in countries:
        v = sig(iso, t, c, use_temporal=True)
        if v is None:
            out[iso] = None; continue
        if (t, c) == ("gender_structuring", "gs_mobility_constraint"):
            v = cho_gate(v)
        if (t, c) == ("recruitment_econprecarity", "ep_poverty_headcount_685"):
            v = poverty_hump(v)
        if (t, c) == ("econ_structure_demand", "esd_d1_hazardous_sector_share"):
            v = materiality_ramp(v)
        out[iso] = v
    return pd.Series(out)

# enumerate every generating (table,col) actually entered, measure R^2_gov
SIGNAL_GOV_R2 = {}
for slug, spec in crosswalk.CROSSWALK.items():
    if slug in crosswalk.MONETIZATION_DOMAINS:
        continue
    for (t, c) in spec["signals"]:
        if (t, c) in DROP_SIGNALS:
            continue
        if (t, c) not in SIGNAL_GOV_R2:
            SIGNAL_GOV_R2[(t, c)] = pearson_r2(signal_series_as_used(t, c), GOV)

GOV_LEVELS = {tc for tc, r in SIGNAL_GOV_R2.items()
              if (r is not None and not np.isnan(r) and r >= GOV_THRESHOLD)}

print("=== STEP 1-2: per-signal governance R^2 (as entered in v0.3) ===")
for (t, c), r in sorted(SIGNAL_GOV_R2.items(), key=lambda kv: -(kv[1] if kv[1] else 0)):
    flag = "  <== DOWN-WEIGHT TARGET" if (t, c) in GOV_LEVELS else ""
    print(f"  {t:<26} {c:<40} R2gov={r}{flag}")
print(f"\nFlagged governance-correlated LEVELS (R2gov >= {GOV_THRESHOLD}): "
      f"{sorted(c for _, c in GOV_LEVELS)}")

# =====================================================================
# STEP 3: weighted within-domain averaging with the down-weight lever
# =====================================================================
def domain_raw(iso, slug, spec, use_demscore=True, use_kafala=True,
               structure=True, use_temporal=True, w_gov=1.0, ip_replace=False,
               spu_only=False):
    # spu_only: apply the down-weight ONLY to flagged signals inside the
    # State-Production-of-Unfreedom domain (the conceptually clean de-biasing sub-move).
    # All other domains keep full weight (w_gov_here = 1.0).
    w_gov_here = w_gov
    if spu_only and slug != "state-production-of-unfreedom":
        w_gov_here = 1.0
    sigs = [(t, c) for (t, c) in spec["signals"] if (t, c) not in DROP_SIGNALS]

    # weighted accumulation: (value, weight, counts_for_floor)
    items = []  # list of (value, weight)
    n_present_floor = 0  # signals counting toward the coverage-floor numerator
    n_total = 0          # signals counting toward the coverage-floor denominator

    for (t, c) in sigs:
        n_total += 1
        # ----- optional IPUMS REPLACEMENT (sensitivity) for the informal-employment level
        if (ip_replace and (t, c) in (("recruitment_econprecarity", "ep_informal_employment_share"),
                                       ("econ_structure_demand", "esd_d2_informal_employment_share"))):
            v = IP_VULN.get(iso)
            if v is None or not pd.notna(v):
                continue  # replacement has no value here -> signal absent (coverage cost)
            items.append((float(v), 1.0)); n_present_floor += 1
            continue
        v = sig(iso, t, c, use_temporal)
        if v is None:
            continue
        if (t, c) == ("gender_structuring", "gs_mobility_constraint"):
            v = cho_gate(v)
        if structure:
            if (t, c) == ("recruitment_econprecarity", "ep_poverty_headcount_685"):
                v = poverty_hump(v)
            if (t, c) == ("econ_structure_demand", "esd_d1_hazardous_sector_share"):
                v = materiality_ramp(v)
        # ----- THE LEVER: down-weight a governance-correlated level
        if (t, c) in GOV_LEVELS and w_gov_here != 1.0:
            if w_gov_here == 0.0:
                # drop entirely: not present, not counted in floor numerator
                continue
            items.append((float(v), w_gov_here)); n_present_floor += 1
        else:
            items.append((float(v), 1.0)); n_present_floor += 1

    # ----- extra slots (glotip / demscore / kafala): weight 1.0, count for floor
    if slug == "state-production-of-unfreedom":
        g = GLOTIP.get(iso)
        if g is not None and pd.notna(g):
            items.append((float(g), 1.0)); n_present_floor += 1; n_total += 1
    if use_demscore and slug in DEM_DOMAINS:
        dv = DEM.get(iso)
        if dv is not None and pd.notna(dv):
            items.append((float(dv), 1.0)); n_present_floor += 1; n_total += 1
    if use_kafala and slug in KAF_DOMAINS:
        kv = KAFALA.get(iso)
        if kv is not None and pd.notna(kv):
            items.append((float(kv), 1.0)); n_present_floor += 1; n_total += 1

    floor_met = (n_present_floor >= agg.COVERAGE_MIN_SIGNALS and
                 n_present_floor >= max(agg.COVERAGE_MIN_SIGNALS,
                                        agg.COVERAGE_FRACTION_FLOOR * n_total)) if n_total else False
    single_by_design = (n_total == 1 and n_present_floor == 1)
    scored = bool(items) and (floor_met or single_by_design)
    if not scored:
        return None, n_present_floor, n_total, False
    wsum = sum(w for _, w in items)
    raw = sum(v * w for v, w in items) / wsum if wsum > 0 else None
    return raw, n_present_floor, n_total, (raw is not None)

def build_country(iso, use_demscore=True, use_kafala=True, structure=True,
                  use_temporal=True, w_gov=1.0, ip_replace=False, spu_only=False):
    dom = {}
    for slug, spec in crosswalk.CROSSWALK.items():
        if slug in crosswalk.MONETIZATION_DOMAINS:
            continue
        raw, npz, ntot, scored = domain_raw(iso, slug, spec, use_demscore, use_kafala,
                                            structure, use_temporal, w_gov, ip_replace,
                                            spu_only)
        dom[slug] = raw if scored else None
    phases = {}
    for phase, dlist in crosswalk.PRODUCT1_PHASES.items():
        ds = [dom[d] for d in dlist if dom.get(d) is not None]
        n, ntot = len(ds), len(dlist)
        floor = (n >= agg.COVERAGE_MIN_SIGNALS and
                 n >= max(agg.COVERAGE_MIN_SIGNALS, agg.COVERAGE_FRACTION_FLOOR * ntot))
        phases[phase] = (sum(ds) / n) if (ds and floor) else None
    R, E = phases["recruitment"], phases["exploitation"]
    composite = None if (R is None or E is None) else (max(0.0, R) * max(0.0, E)) ** 0.5
    return composite, R, E, dom

def run(use_demscore, use_kafala, structure, use_temporal, w_gov=1.0, ip_replace=False,
        spu_only=False):
    c, R, E, dom = {}, {}, {}, {}
    for iso in countries:
        cc, rr, ee, dd = build_country(iso, use_demscore, use_kafala, structure,
                                       use_temporal, w_gov, ip_replace, spu_only)
        c[iso], R[iso], E[iso], dom[iso] = cc, rr, ee, dd
    return (pd.Series(c).reindex(countries), pd.Series(R).reindex(countries),
            pd.Series(E).reindex(countries), dom)

# ---------------- LOCKED reference ----------------
sc = scores.set_index("iso3")
locked_comp = sc.composite_score.reindex(countries)
locked_R = sc.R_score.reindex(countries)
locked_E = sc.E_score.reindex(countries)

# ---------------- nested ladder + the v0.4 sweep ----------------
v3_c, v3_R, v3_E, v3dom = run(True, True, True, True, w_gov=1.0)            # v0.3 reproduction (w=1.0)
# v0.4 BROAD down-weight sweep (all seven flagged governance-correlated levels)
sweep = {}
for w in (0.5, 0.25, 0.0):
    sweep[w] = run(True, True, True, True, w_gov=w)
# v0.4-SPU: SPU-ONLY down-weight sweep (down-weight the flagged governance proxies ONLY
# inside State-Production-of-Unfreedom -- the conceptually clean de-biasing sub-move that
# the consolidated recommendation actually recommends). All other domains keep full weight.
spu_sweep = {}
for w in (0.75, 0.5, 0.25, 0.0):
    spu_sweep[w] = run(True, True, True, True, w_gov=w, spu_only=True)
# replacement sensitivity (IPUMS vuln-emp swaps informal-employment level), full weight
if HAS_IPUMS:
    rep_c, rep_R, rep_E, repdom = run(True, True, True, True, w_gov=1.0, ip_replace=True)
else:
    _nan = pd.Series([float("nan")] * len(countries), index=countries)
    rep_c, rep_R, rep_E, repdom = _nan, _nan, _nan, {}

# headline BROAD v0.4 arm = w_gov 0.25 (strong down-weight, coverage preserved).
# NOTE: the broad arms are reported sweep diagnostics, not the shipped build.
W_HEAD = 0.25
v4_c, v4_R, v4_E, v4dom = sweep[W_HEAD]
# SHIPPED configuration = SPU-only w = 0.5: the site's scores.json reads the
# v0_4_spu_w05_* columns (see to_public_json.py and the provenance verification
# block at the end of this script), and docs/METHODS.md §6 documents w = 0.5.
# An earlier draft recommendation referenced w = 0.25; that label was stale.
# W_SPU below only selects which arm feeds the legacy convenience column
# (v0_4_spu_w025_composite) and the w=0.25 diagnostic blocks; every weight is
# persisted per-arm so the cost curve stays visible.
W_SPU = 0.25
spu_c, spu_R, spu_E, spudom = spu_sweep[W_SPU]
W_SHIPPED = 0.5  # SPU-only w=0.5 -- the build the site displays

# ---------------- reproduction check (v0.3 exact) ----------------
def repro(name, mine, path, col):
    try:
        ref = pd.read_csv(path).set_index("iso3")[col]
        chk = pd.concat([mine.rename("mine"), ref], axis=1).dropna()
        rc = np.corrcoef(chk.iloc[:, 0], chk.iloc[:, 1])[0, 1]
        md = float((chk.iloc[:, 0] - chk.iloc[:, 1]).abs().max())
        print(f"\n=== {name} REPRO: corr={rc:.5f} maxdiff={md:.6f} (n={len(chk)}) ===")
        return rc, md
    except Exception as e:
        print(f"{name} repro skipped:", e); return None, None
repro("v0.3 (w_gov=1.0 base)", v3_c, os.path.join(V03, "scores_v0_3.csv"), "v0_3_composite")

# ---------------- headline metrics ----------------
def block(name, cs, R, E, ref):
    return {"build": name, "n_scored": int(cs.notna().sum()),
            "R2_vs_gov": pearson_r2(cs, GOV), "spearman_vs_gov": spearman(cs, GOV)[0],
            "spearman_R_E": spearman(R, E)[0], "resid_sd_given_gov": resid_sd(cs),
            "kendall_tau_vs_locked": (kendall(cs, ref) if ref is not None else 1.0)}
rows = [
    block("LOCKED", locked_comp, locked_R, locked_E, None),
    block("v0.3 (=base)", v3_c, v3_R, v3_E, locked_comp),
    block("v0.4 w=0.5", sweep[0.5][0], sweep[0.5][1], sweep[0.5][2], locked_comp),
    block("v0.4 w=0.25 [HEADLINE]", v4_c, v4_R, v4_E, locked_comp),
    block("v0.4 w=0.0 (drop)", sweep[0.0][0], sweep[0.0][1], sweep[0.0][2], locked_comp),
    *([block("v0.4 IPUMS-replace (sens)", rep_c, rep_R, rep_E, locked_comp)]
      if HAS_IPUMS else []),
    # --- SPU-only down-weight sweep (the RECOMMENDED sub-move) ---
    block("v0.4-SPU w=0.75", spu_sweep[0.75][0], spu_sweep[0.75][1], spu_sweep[0.75][2], locked_comp),
    block("v0.4-SPU w=0.5 [SHIPPED]", spu_sweep[0.5][0], spu_sweep[0.5][1], spu_sweep[0.5][2], locked_comp),
    block("v0.4-SPU w=0.25 (sweep arm)", spu_c, spu_R, spu_E, locked_comp),
    block("v0.4-SPU w=0.0 (drop)", spu_sweep[0.0][0], spu_sweep[0.0][1], spu_sweep[0.0][2], locked_comp),
]
metrics = pd.DataFrame(rows)
print("\n=== HEADLINE METRICS (v0.4 down-weight sweep) ===")
print(metrics.to_string(index=False))

# ---------------- SPU-only sweep summary (the RECOMMENDED sub-move) ----------------
print("\n=== SPU-ONLY DOWN-WEIGHT SWEEP (recommended configuration) ===")
print(f"{'w_gov':>6}  {'n':>4}  {'R2gov':>6}  {'R<->E':>6}  {'residSD':>7}  {'tau_locked':>10}")
for w in (1.0, 0.75, 0.5, 0.25, 0.0):
    if w == 1.0:
        cs, R, E = v3_c, v3_R, v3_E
    else:
        cs, R, E, _ = spu_sweep[w]
    tag = "  <== SHIPPED" if w == W_SHIPPED else ("  (=v0.3 base)" if w == 1.0 else "")
    print(f"{w:>6.2f}  {int(cs.notna().sum()):>4}  {pearson_r2(cs,GOV):>6}  "
          f"{spearman(R,E)[0]:>6}  {resid_sd(cs):>7}  {kendall(cs,locked_comp):>10}{tag}")

# ---------------- domain-level gov R^2 movement (v0.3 -> v0.4 headline) ----------------
def dser(dm, slug): return pd.Series({iso: dm[iso].get(slug) for iso in countries})
print("\n=== DOMAIN gov R^2 + coverage (v0.3 -> v0.4 w=0.25) ===")
for slug in ["state-production-of-unfreedom", "constrained-mobility", "gender-structuring",
             "economic-precarity", "economic-structure-demand", "debt-financialized-dependency"]:
    sb = dser(v3dom, slug); st = dser(v4dom, slug)
    print(f"  {slug:<32} R2gov {pearson_r2(sb,GOV)} -> {pearson_r2(st,GOV)}   "
          f"cov {int(sb.notna().sum())} -> {int(st.notna().sum())}")

# ---------------- face validity ----------------
def topbot(cs, k=10):
    s = cs.dropna().sort_values(ascending=False)
    return ([cname.get(i, i) for i in s.head(k).index], [cname.get(i, i) for i in s.tail(k).index])
print("\n=== FACE VALIDITY ===")
for nm, cs in [("v0.3", v3_c), ("v0.4 w=0.25", v4_c), ("v0.4 w=0.0 drop", sweep[0.0][0])]:
    t, b = topbot(cs); print(f"[{nm}]\n  TOP10: {', '.join(t)}\n  BOT10: {', '.join(b)}")

# ---------------- biggest movers v0.4 vs v0.3 ----------------
r3r = v3_c.rank(ascending=False, method="first")
r4r = v4_c.rank(ascending=False, method="first")
shift = (r3r - r4r).dropna().sort_values()
print("\n=== BIGGEST RANK MOVERS v0.4(w=0.25) vs v0.3 ===")
for iso in pd.concat([shift.head(8), shift.tail(8)]).index:
    print(f"  {cname.get(iso,iso):<26} {int(r3r[iso]):>4} -> {int(r4r[iso]):>4}  ({int(shift[iso]):+d})")

# ---------------- SPU-only face validity + movers (w=0.25 sweep arm) ----------------
print("\n=== FACE VALIDITY -- SPU-only w=0.25 (sweep arm; shipped build is w=0.5) ===")
t, b = topbot(spu_c)
print(f"  TOP10: {', '.join(t)}\n  BOT10: {', '.join(b)}")
rSr = spu_c.rank(ascending=False, method="first")
shiftS = (r3r - rSr).dropna().sort_values()
print("=== BIGGEST RANK MOVERS SPU-only(w=0.25) vs v0.3 ===")
for iso in pd.concat([shiftS.head(6), shiftS.tail(6)]).index:
    print(f"  {cname.get(iso,iso):<26} {int(r3r[iso]):>4} -> {int(rSr[iso]):>4}  ({int(shiftS[iso]):+d})")
# domain-level SPU movement (should match broad v0.4 at the SPU domain only)
sbS = dser(v3dom, "state-production-of-unfreedom"); stS = dser(spudom, "state-production-of-unfreedom")
print(f"  [SPU domain] R2gov {pearson_r2(sbS,GOV)} -> {pearson_r2(stS,GOV)}   "
      f"cov {int(sbS.notna().sum())} -> {int(stS.notna().sum())}")

# =====================================================================
# DIRECT per-domain variance-added-vs-removed decomposition
# (the load-bearing de-bias-vs-hollow judgement, made direct rather than indirect).
# For each domain we decompose its contribution to composite spread into the part
# aligned with governance (the "bias" axis) and the part orthogonal to governance
# (the "information" axis), then ask what the down-weight removes from EACH.
#   gov-aligned variance  = (corr(domain, gov)^2) * Var(domain)   [bias content]
#   orthogonal variance   = (1 - corr^2)          * Var(domain)   [FL-relevant content]
# A move that "de-biases" removes mostly gov-aligned variance; a move that "hollows"
# removes orthogonal (FL-relevant) variance too. We report this for v0.3 -> broad v0.4
# and v0.3 -> SPU-only, per down-weighted domain.
# =====================================================================
def var_decomp(series):
    m = series.notna() & GOV.notna()
    if m.sum() < 10:
        return (np.nan, np.nan, np.nan)
    v = float(np.var(series[m].values))
    r = float(np.corrcoef(series[m].values, GOV[m].values)[0, 1])
    gov_var = (r ** 2) * v
    orth_var = (1 - r ** 2) * v
    return (v, gov_var, orth_var)

print("\n=== VARIANCE DECOMPOSITION per down-weighted domain (gov-aligned vs orthogonal) ===")
print(f"{'domain':<32} {'config':<10} {'tot':>8} {'gov':>8} {'orth':>8}  {'d_gov':>8} {'d_orth':>8} {'removed_is':>10}")
DW_DOMAINS = ["state-production-of-unfreedom", "constrained-mobility", "gender-structuring",
              "economic-precarity", "economic-structure-demand", "debt-financialized-dependency"]
for slug in DW_DOMAINS:
    base = dser(v3dom, slug)
    bt, bg, bo = var_decomp(base)
    for cfgname, dm in [("broad.25", v4dom), ("SPU.25", spudom)]:
        st = dser(dm, slug)
        tt, tg, to = var_decomp(st)
        if any(np.isnan(x) for x in (bt, bg, bo, tt, tg, to)):
            continue
        d_gov, d_orth = bg - tg, bo - to   # positive = variance REMOVED by the down-weight
        # Classify the move by what it did to EACH axis (positive d = removed, negative = added):
        #   removed gov AND added/kept orthogonal      -> clean DE-BIAS (the goal)
        #   removed gov AND removed >=2x less orth      -> de-bias
        #   removed orthogonal >=2x more than gov       -> HOLLOW (the failure mode)
        if abs(d_gov) < 1e-9 and abs(d_orth) < 1e-9:
            verdict = "no-op"
        elif d_gov > 0 and d_orth <= 0:
            verdict = "DE-BIAS+"      # removed gov-aligned, ADDED orthogonal (best case)
        elif d_gov <= 0 and d_orth <= 0:
            verdict = "added"
        elif d_gov >= 2 * abs(d_orth):
            verdict = "de-bias"
        elif d_orth > 0 and d_orth >= 2 * abs(d_gov):
            verdict = "HOLLOW"
        else:
            verdict = "mixed"
        print(f"{slug:<32} {cfgname:<10} {tt*1e3:>8.2f} {tg*1e3:>8.2f} {to*1e3:>8.2f}  "
              f"{d_gov*1e3:>8.2f} {d_orth*1e3:>8.2f} {verdict:>10}")
print("  (variances x1e3 for readability; d_gov/d_orth = variance REMOVED vs v0.3;"
      " 'de-bias' = removed mostly gov-aligned, 'HOLLOW' = removed mostly orthogonal.)")

# ---------------- WRITE outputs ----------------
out = pd.DataFrame({
    "iso3": countries,
    "country_name": [cname.get(i, i) for i in countries],
    "v0_4_composite": v4_c.values, "v0_4_R": v4_R.values, "v0_4_E": v4_E.values,
    "v0_3_composite": v3_c.values,
    "v0_4_w05_composite": sweep[0.5][0].values,
    "v0_4_w00_composite": sweep[0.0][0].values,
    "v0_4_iprepl_composite": rep_c.values,
    # SPU-only (recommended sub-move) sweep
    "v0_4_spu_w075_composite": spu_sweep[0.75][0].values,
    "v0_4_spu_w05_composite": spu_sweep[0.5][0].values,
    # PROVENANCE: persist the RECOMMENDED structure's PHASE-LEVEL R and E
    # scores (SPU-only @ w=0.5) so the headline R<->E coherence (0.694) reproduces directly
    # from disk. Previously only the SPU composites were persisted; a reader recomputing
    # R<->E off the broad v0_4_R/v0_4_E columns got the WRONG variant (0.585, broad v0.4).
    "v0_4_spu_w05_R": spu_sweep[0.5][1].values,
    "v0_4_spu_w05_E": spu_sweep[0.5][2].values,
    "v0_4_spu_w025_composite": spu_c.values,
    "v0_4_spu_w00_composite": spu_sweep[0.0][0].values,
    "v0_4_spu_rank": rSr.reindex(countries).values,
    "locked_composite": locked_comp.values,
    "v0_4_rank": r4r.reindex(countries).values, "v0_3_rank": r3r.reindex(countries).values,
    "gov_risk_wgi": GOV.reindex(countries).values,
}).sort_values("v0_4_composite", ascending=False, na_position="last")
out.to_csv(os.path.join(OUT, "scores_v0_4.csv"), index=False)
metrics.to_csv(os.path.join(OUT, "comparison_metrics.csv"), index=False)
print("\nWROTE:", os.path.join(OUT, "scores_v0_4.csv"))
print("WROTE:", os.path.join(OUT, "comparison_metrics.csv"))

# =====================================================================
# PROVENANCE VERIFICATION: reproduce the RECOMMENDED structure's headline
# metrics FROM the persisted CSV columns (round-trip through disk), so the
# recommendation's R<->E = 0.694 is demonstrably reproducible from file.
# =====================================================================
_disk = pd.read_csv(os.path.join(OUT, "scores_v0_4.csv")).set_index("iso3")
_R_disk = _disk["v0_4_spu_w05_R"].reindex(countries)
_E_disk = _disk["v0_4_spu_w05_E"].reindex(countries)
_C_disk = _disk["v0_4_spu_w05_composite"].reindex(countries)
re_from_disk = spearman(_R_disk, _E_disk)[0]
r2gov_from_disk = pearson_r2(_C_disk, GOV)
cov_from_disk = int(_C_disk.notna().sum())
# the WRONG variant a reader would get off the broad v0_4_R / v0_4_E columns:
re_broad = spearman(_disk["v0_4_R"].reindex(countries), _disk["v0_4_E"].reindex(countries))[0]
print("\n=== PROVENANCE VERIFICATION (recomputed FROM persisted CSV columns) ===")
print(f"  RECOMMENDED (SPU-only w=0.5): R<->E (Spearman, from v0_4_spu_w05_R/E) = {re_from_disk}"
      f"   [recommendation headline: 0.694]")
print(f"  RECOMMENDED composite R2_vs_gov (from v0_4_spu_w05_composite) = {r2gov_from_disk}"
      f"   coverage = {cov_from_disk}   [headline: 0.630 / 184]")
print(f"  (broad v0.4 R<->E off v0_4_R/v0_4_E = {re_broad}  <- the WRONG variant before this fix)")
assert abs(re_from_disk - 0.694) <= 0.01, f"R<->E from disk {re_from_disk} not within 0.01 of 0.694"
# Hump removed (design decision): composite R2gov moved 0.630 -> ~0.628 (negligible); tolerant check.
assert abs(r2gov_from_disk - 0.628) <= 0.01, f"composite R2gov from disk {r2gov_from_disk} not within 0.01 of 0.628 (hump-removed build)"
assert cov_from_disk == 184, f"coverage from disk {cov_from_disk} != 184 (UNCHANGED expected)"
print("  CONFIRMED: R<->E within +/-0.01 of 0.694; composite R2gov ~0.628 (hump-removed); coverage 184.")
