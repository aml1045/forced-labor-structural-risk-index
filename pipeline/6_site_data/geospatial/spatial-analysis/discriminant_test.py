#!/usr/bin/env python3
"""
Discriminant-validity test for the FLSRI country-level spatial cluster.

Question: is the High-High country cluster a genuine forced-labor signal, or an
artifact of spatially-autocorrelated weak governance? The FLSRI composite shares
~63% of its variance with WGI rule of law, and governance/conflict cluster in space
by their nature, so a Local Moran's I on the raw composite may simply reproduce the
governance map.

Test (per the adversarial review): residualize the composite on WGI rule of law and
recompute Global Moran's I + LISA on the RESIDUALS. If spatial autocorrelation
survives, the cluster carries forced-labor-specific structure beyond governance; if
it collapses toward zero, the cluster is largely a governance artifact. Also compute
a LISA on WGI alone and report High-High set overlap (Jaccard) with the composite.

Same method as compute_lisa.py: Queen contiguity, row-standardized, 999 permutations,
BH-FDR. READ-ONLY on inputs. Seed fixed at 42.
"""
import sys, os
import numpy as np
import pandas as pd
import geopandas as gpd
from libpysal.weights import Queen
from esda.moran import Moran, Moran_Local

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
sys.path.insert(0, _REPO)
from config import site_data_paths as P  # noqa: E402

SEED, PERMS, ALPHA = 42, 999, 0.05
QUAD = {1: "High-High", 2: "Low-High", 3: "Low-Low", 4: "High-Low"}


def bh_fdr(p):
    p = np.asarray(p, float); n = len(p)
    order = np.argsort(p); ranked = p[order]
    adj = ranked * n / np.arange(1, n + 1)
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    adj = np.clip(adj, 0, 1)
    out = np.empty(n); out[order] = adj
    return out


def layer(gdf, y, name):
    w = Queen.from_dataframe(gdf, use_index=False)
    iso_ids = set(w.islands)
    isomask = np.array([o in iso_ids for o in w.id_order])
    w.transform = "r"
    np.random.seed(SEED); mi = Moran(y, w, permutations=PERMS)
    np.random.seed(SEED); ml = Moran_Local(y, w, permutations=PERMS, seed=SEED)
    p = ml.p_sim.copy(); p[isomask] = np.nan
    valid = ~np.isnan(p)
    pf = np.full_like(p, np.nan); pf[valid] = bh_fdr(p[valid])
    sig = (pf < ALPHA) & valid
    isos = gdf["iso3"].values
    hh = set(isos[i] for i in range(len(gdf)) if sig[i] and ml.q[i] == 1)
    ll = set(isos[i] for i in range(len(gdf)) if sig[i] and ml.q[i] == 3)
    print(f"  [{name}] Global Moran's I = {mi.I:.4f}  z = {mi.z_sim:.2f}  p = {mi.p_sim:.4f}  | HH={len(hh)} LL={len(ll)}")
    return mi.I, mi.p_sim, hh, ll


def main():
    # geometry + composite (identical join to compute_lisa.py country layer)
    cg = gpd.read_file(P.NE_ADMIN0_GEOJSON)
    cg["iso3"] = cg["ADM0_A3"]
    bad = cg["iso3"] == "-99"
    cg.loc[bad, "iso3"] = cg.loc[bad, "ISO_A3_EH"]
    sc = pd.read_csv(P.SCORES_CSV)
    cg = cg.merge(sc[["iso3", "country_name", "composite_score"]], on="iso3", how="left")
    wb = pd.read_csv(os.path.join(_REPO, "data", "processed", "worldbank.csv"))
    cg = cg.merge(wb[["iso3", "wb_wgi_rule_of_law"]], on="iso3", how="left")

    g = cg[cg["composite_score"].notna() & cg["wb_wgi_rule_of_law"].notna()].reset_index(drop=True)
    print(f"countries with composite + WGI rule-of-law: {len(g)}")

    y_comp = g["composite_score"].values.astype(float)
    y_wgi = g["wb_wgi_rule_of_law"].values.astype(float)   # already risk-oriented (high = weak ROL)

    # OLS residualize composite on WGI rule of law
    X = np.column_stack([np.ones(len(g)), y_wgi])
    beta, *_ = np.linalg.lstsq(X, y_comp, rcond=None)
    resid = y_comp - X @ beta
    r2 = 1 - np.var(resid) / np.var(y_comp)
    r = np.corrcoef(y_comp, y_wgi)[0, 1]
    print(f"composite ~ WGI rule-of-law:  r = {r:.3f}  R^2 = {r2:.3f}\n")

    print("Global Moran's I + High-High LISA set (Queen, 999 perms, BH-FDR):")
    I_c, p_c, hh_c, ll_c = layer(g, y_comp, "composite (baseline)")
    I_w, p_w, hh_w, ll_w = layer(g, y_wgi, "WGI rule-of-law alone")
    I_r, p_r, hh_r, ll_r = layer(g, resid, "composite | WGI (residual)")

    def jac(a, b):
        return len(a & b) / len(a | b) if (a | b) else 0.0

    print("\n--- DISCRIMINANT RESULTS ---")
    print(f"Composite HH ({len(hh_c)}): {sorted(hh_c)}")
    print(f"WGI-alone HH ({len(hh_w)}): {sorted(hh_w)}")
    print(f"Residual HH ({len(hh_r)}): {sorted(hh_r)}")
    print(f"\nJaccard(composite HH, WGI HH) = {jac(hh_c, hh_w):.2f}")
    surv = hh_c & hh_r
    print(f"Composite HH countries that SURVIVE in the residual HH: {sorted(surv)}  ({len(surv)}/{len(hh_c)})")
    drop_to_gov = hh_c & hh_w - hh_r
    print(f"Composite HH that are in WGI HH but NOT residual HH (governance-explained): {sorted((hh_c & hh_w) - hh_r)}")
    print(f"\nResidual Global Moran's I = {I_r:.4f} (p={p_r:.4f}) vs composite {I_c:.4f}.")
    frac = I_r / I_c if I_c else float('nan')
    print(f"Residual retains {frac*100:.0f}% of the composite's Global Moran's I after removing the WGI-linear part.")

    # Archive the verdict alongside lisa_summary.json so the residual-clustering
    # numbers quoted on methodology.html are backed by a committed artifact,
    # not just a stdout print (same archive-the-verdict standard as docs/validation/).
    import json
    summary = {
        "seed": SEED, "permutations": PERMS, "alpha_fdr": ALPHA,
        "n_countries": int(len(g)),
        "composite_vs_wgi": {"pearson_r": round(float(r), 4), "R2": round(float(r2), 4)},
        "global_moran_I": {"composite": round(float(I_c), 4), "wgi": round(float(I_w), 4),
                           "residual": round(float(I_r), 4)},
        "pseudo_p": {"composite": round(float(p_c), 4), "wgi": round(float(p_w), 4),
                     "residual": round(float(p_r), 4)},
        "residual_share_of_composite_I": round(float(frac), 4),
        "hh_sets": {"composite": sorted(hh_c), "wgi": sorted(hh_w), "residual": sorted(hh_r)},
        "ll_sets": {"composite": sorted(ll_c), "wgi": sorted(ll_w), "residual": sorted(ll_r)},
        "jaccard_composite_wgi_hh": round(float(jac(hh_c, hh_w)), 4),
        "surviving_hh": sorted(surv),
        "governance_explained_hh": sorted((hh_c & hh_w) - hh_r),
    }
    out_path = os.path.join(_HERE, "discriminant_summary.json")
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
