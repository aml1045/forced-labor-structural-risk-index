#!/usr/bin/env python3
"""Export per-country per-domain scores for the FLSRI public-site country profiles.

Reuses build_v0_4's EXACT domain-scoring logic and config. The displayed build is the
RECOMMENDED structure = v0.3 + SPU-only down-weight @ w_gov=0.5 (scores.json columns
v0_4_spu_w05_composite / _R / _E). We therefore call build_country with the SAME knobs:
    use_demscore=True, use_kafala=True, structure=True, use_temporal=True,
    w_gov=0.5, ip_replace=False, spu_only=True
so the exported domain scores reconcile to the displayed R/E by construction.

For each scored country and ALL 13 domains we record:
  slug, readable label, 0-1 score, phase (Recruitment/Exploitation/Monetization),
  low_conf (from the displayed build's per-domain confidence flags), scored (bool).
Never impute 0: a domain that fails its coverage floor is marked scored:false with score:null.

Monetization domains are computed for the lens display but are NOT in the composite
(locked rule 7); they are flagged phase="Monetization" so the site can label them
"lens -- not in the score".

Writes:  4-final deliverable/working/public-site/data/domains.json
Prints:  reconciliation of Recruitment domains -> R and Exploitation domains -> E.
"""
import sys, os, json
import numpy as np, pandas as pd

# Import build_v0_4's machinery by executing it in this namespace would re-run the whole
# analysis; instead we replicate the small setup and reuse its functions via import.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# build_v0_4 does its heavy printing/analysis at import; we want only its functions +
# loaded tables. It is import-safe (all top-level work is data loading + a long analysis
# that also prints). To avoid re-running the full sweep we instead import the pieces we
# need by reading the same modules build_v0_4 reads. The domain_raw/build_country logic
# is duplicated minimally here ONLY by delegating to build_v0_4's own functions.
import build_v0_4 as B

countries = B.countries
cname = B.cname
crosswalk = B.crosswalk

# Readable labels (canonical wording from the site's framework.html domain tables).
LABELS = {
    "economic-precarity": "Economic precarity",
    "debt-financialized-dependency": "Debt & financialized dependency",
    "constrained-mobility": "Constrained mobility",
    "ascriptive-exclusion": "Ascriptive exclusion",
    "legal-non-recognition": "Legal non-recognition",
    "gender-structuring": "Gendered labor",
    "age-childhood-structuring": "Age & childhood structuring",
    "structural-disruption": "Structural disruption",
    "foreclosed-exit-structural": "Foreclosed exit (structural)",
    "economic-structure-demand": "Economic structure & demand",
    "state-production-of-unfreedom": "State production of unfreedom",
    "domain-a-transnational-concealment": "Transnational concealment",
    "domain-b-cash-informal-retention": "Cash & informal retention",
}
PHASE_LABEL = {"recruitment": "Recruitment", "exploitation": "Exploitation",
               "monetization": "Monetization"}

# slug -> phase
SLUG_PHASE = {}
for ph, dlist in crosswalk.PRODUCT1_PHASES.items():
    for d in dlist:
        SLUG_PHASE[d] = ph
for d in crosswalk.MONETIZATION_DOMAINS:
    SLUG_PHASE[d] = "monetization"

# Per-country per-domain low-confidence / not-scored flags from the DISPLAYED build's
# persisted output (outputs/scores.csv). These structural data-quality flags are what
# scores.json already surfaces; we reuse them verbatim so the profile matches the rest
# of the site exactly rather than re-deriving a possibly-divergent flag.
sc_csv = pd.read_csv(os.path.join(B.REPO, "outputs", "scores.csv"))
def _split(x):
    return set(str(x).split(",")) if isinstance(x, str) and x.strip() else set()
LOWCONF = {r.iso3: _split(r.low_confidence_flags) for r in sc_csv.itertuples()}
NOTSCORED_FLAG = {r.iso3: _split(r.not_scored_flags) for r in sc_csv.itertuples()}

# ---- DISPLAYED-build knobs (RECOMMENDED structure: SPU-only down-weight @ w=0.5) ----
KNOBS = dict(use_demscore=True, use_kafala=True, structure=True, use_temporal=True,
             w_gov=0.5, ip_replace=False, spu_only=True)

# Monetization domains are computed with the same base aggregation (no down-weight relevance:
# spu_only only touches state-production-of-unfreedom). domain_raw works for any slug+spec.
def monet_raw(iso, slug, spec):
    return B.domain_raw(iso, slug, spec, **KNOBS)

out = {}
recon_R, recon_E = [], []
n_scored_countries = 0

for iso in countries:
    comp, R, E, dom = B.build_country(iso, **KNOBS)
    if comp is None and R is None and E is None and all(v is None for v in dom.values()):
        # entirely unscored country -- still emit an entry so the profile can say so,
        # but the site only opens profiles for scored countries (scores.json scored flag).
        pass
    country_scored = comp is not None
    if country_scored:
        n_scored_countries += 1

    drec = {}
    # Product-1 domains (recruitment + exploitation), from build_country's dom dict
    for slug, spec in crosswalk.CROSSWALK.items():
        if slug in crosswalk.MONETIZATION_DOMAINS:
            score = None
            raw, npz, ntot, ok = monet_raw(iso, slug, spec)
            score = float(raw) if (ok and raw is not None) else None
        else:
            v = dom.get(slug)
            score = float(v) if v is not None else None
        ph = SLUG_PHASE[slug]
        scored_dom = score is not None
        low_conf = (slug in LOWCONF.get(iso, set())) and scored_dom
        drec[slug] = {
            "score": (round(score, 4) if score is not None else None),
            "phase": PHASE_LABEL[ph],
            "label": LABELS.get(slug, slug),
            "low_conf": bool(low_conf),
            "scored": bool(scored_dom),
        }
        # ---- fenced modeled-estimate domain (design decisions) ----
        # A domain fed by a modeled estimate is never shown as an ordinary scored
        # domain: it carries modeled_input + the 80% interval and is forced low_conf.
        # Interval = the domain mean with the modeled signal swapped to its PI80
        # bounds (analytic: score +/- bound-delta / n_present_signals).
        mdoms = B.MODELED_DOMAINS.get(iso, {})
        if slug in mdoms and scored_dom:
            t, c = mdoms[slug]
            m = B.MODELED[(t, c, iso)]
            _raw, npz, _ntot, _ok = B.domain_raw(iso, slug, spec, **KNOBS)
            n = npz if npz else 1
            drec[slug]["modeled_input"] = True
            drec[slug]["modeled_n"] = 1
            drec[slug]["n_signals"] = int(npz)
            drec[slug]["interval"] = [round(score - (m["value"] - m["lo"]) / n, 4),
                                      round(score + (m["hi"] - m["value"]) / n, 4)]
            drec[slug]["low_conf"] = True
    out[iso] = drec

    # ---- reconciliation: equal-weight mean of scored Recruitment / Exploitation domains ----
    if country_scored:
        rec_doms = [drec[d]["score"] for d in crosswalk.PRODUCT1_PHASES["recruitment"]
                    if drec[d]["scored"]]
        exp_doms = [drec[d]["score"] for d in crosswalk.PRODUCT1_PHASES["exploitation"]
                    if drec[d]["scored"]]
        if R is not None and rec_doms:
            recon_R.append((iso, float(np.mean(rec_doms)), float(R)))
        if E is not None and exp_doms:
            recon_E.append((iso, float(np.mean(exp_doms)), float(E)))

# ---- write (staging, never public/) ----
OUT_JSON = os.path.join(B.P.STAGING, "domains.json")
os.makedirs(B.P.STAGING, exist_ok=True)
with open(OUT_JSON, "w") as f:
    json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
print("WROTE:", OUT_JSON, f"({len(out)} countries)")

# ---- reconciliation report ----
def report(name, pairs):
    if not pairs:
        print(f"  {name}: no pairs"); return
    diffs = np.array([abs(m - p) for _, m, p in pairs])
    mx = diffs.max(); md = diffs.mean()
    worst = max(pairs, key=lambda t: abs(t[1] - t[2]))
    print(f"  {name}: n={len(pairs)}  max|diff|={mx:.6f}  mean|diff|={md:.6f}  "
          f"worst={worst[0]} (domains_mean={worst[1]:.4f} vs phase={worst[2]:.4f})")
    return mx

print("\n=== RECONCILIATION: equal-weight domain mean vs displayed phase score ===")
mxR = report("Recruitment domains -> R", recon_R)
mxE = report("Exploitation domains -> E", recon_E)
TOL = 1e-4
ok = (mxR is not None and mxR <= TOL) and (mxE is not None and mxE <= TOL)
print(f"\nRECONCILES within tol={TOL}: {ok}")
print(f"Scored countries: {n_scored_countries}")
