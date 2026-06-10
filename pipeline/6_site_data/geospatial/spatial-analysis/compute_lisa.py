#!/usr/bin/env python3
"""
FLSRI spatial-autocorrelation analysis — Global Moran's I + LISA (Local Moran's I)
at admin-1 (priority) and country level.

READ-ONLY on all inputs. Real data only. Outputs written to this folder.

Method:
  - Queen contiguity spatial weights from polygon geometry, row-standardized.
  - Global Moran's I (I, z, pseudo-p via 999 conditional permutations).
  - Local Moran's I_i per unit: stat, conditional-permutation pseudo-p (999 perms),
    cluster classification HH / LL / HL / LH / NS.
  - Benjamini-Hochberg FDR correction on the LISA pseudo-p values; report raw + fdr.
  - Getis-Ord Gi* hotspots as a secondary layer.

Reproducibility: numpy seed fixed at 42.
"""
import json, sys
import numpy as np
import pandas as pd
import geopandas as gpd
from libpysal.weights import Queen
from esda.moran import Moran, Moran_Local
from esda.getisord import G_Local

SEED = 42
PERMS = 999
ALPHA = 0.05

# ---- paths (from config/site_data_paths.py; logic unchanged) ----
import os as _os, sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_REPO_ROOT = _os.path.abspath(_os.path.join(_HERE, "..", "..", "..", ".."))
_sys.path.insert(0, _REPO_ROOT)
from config import site_data_paths as P  # noqa: E402
ADMIN1_GEOJSON = _os.path.join(P.PORT_SUBNAT, "admin1_risk_simplified.geojson")
COUNTRY_GEOJSON = P.NE_ADMIN0_GEOJSON
SCORES_CSV = P.SCORES_CSV
OUTDIR = P.PORT_SPATIAL


def bh_fdr(pvals):
    """Benjamini-Hochberg FDR-adjusted p-values."""
    p = np.asarray(pvals, dtype=float)
    n = len(p)
    order = np.argsort(p)
    ranked = p[order]
    adj = ranked * n / (np.arange(1, n + 1))
    # enforce monotonicity from the largest p downward
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    adj = np.clip(adj, 0, 1)
    out = np.empty(n)
    out[order] = adj
    return out


# esda Moran_Local quadrant codes: 1=HH, 2=LH, 3=LL, 4=HL
QUAD = {1: "High-High", 2: "Low-High", 3: "Low-Low", 4: "High-Low"}


def classify(local, sig_mask, isolate_mask):
    """Classify each unit. Isolates (no neighbors) get an explicit 'Isolate'
    label and are NEVER assigned a cluster: a local spatial association is
    undefined without neighbors. esda fills their lag with 0 and returns a
    degenerate pseudo-p — we override that here so isolates are not reported
    as spurious clusters."""
    labels = []
    for q, sig, iso in zip(local.q, sig_mask, isolate_mask):
        if iso:
            labels.append("Isolate (no neighbours)")
        else:
            labels.append(QUAD[q] if sig else "Not-Significant")
    return labels


def run_layer(gdf, value_col, id_cols, label):
    print(f"\n=== {label} ===")
    gdf = gdf.copy()
    # drop missing-value units (never missing->0)
    n_total = len(gdf)
    gdf = gdf[gdf[value_col].notna()].reset_index(drop=True)
    n_used = len(gdf)
    print(f"units with non-missing {value_col}: {n_used} / {n_total}")

    # Queen contiguity weights
    w = Queen.from_dataframe(gdf, use_index=False)
    n_isolates = len(w.islands)
    print(f"Queen weights built. isolates (no neighbors): {n_isolates}")
    # boolean mask of isolates aligned to gdf row order
    iso_ids = set(w.islands)
    isolate_mask = np.array([oid in iso_ids for oid in w.id_order])
    w.transform = "r"  # row-standardize

    y = gdf[value_col].values.astype(float)

    # Global Moran's I
    np.random.seed(SEED)
    mi = Moran(y, w, permutations=PERMS)
    print(f"Global Moran's I = {mi.I:.4f}  z = {mi.z_sim:.3f}  pseudo-p = {mi.p_sim:.4f}")

    # Local Moran's I (LISA), conditional permutation
    np.random.seed(SEED)
    lisa = Moran_Local(y, w, permutations=PERMS, seed=SEED)
    p_raw = lisa.p_sim.copy()
    # isolates have a degenerate (zero-variance) reference distribution; their
    # pseudo-p is meaningless. Null them out and exclude from the FDR pool.
    p_raw[isolate_mask] = np.nan
    p_fdr = np.full_like(p_raw, np.nan, dtype=float)
    valid = ~np.isnan(p_raw)
    p_fdr[valid] = bh_fdr(p_raw[valid])
    sig_raw = (p_raw < ALPHA) & valid
    sig_fdr = (p_fdr < ALPHA) & valid

    cluster_raw = classify(lisa, sig_raw, isolate_mask)
    cluster_fdr = classify(lisa, sig_fdr, isolate_mask)

    # Getis-Ord Gi* (star = include self)
    np.random.seed(SEED)
    gistar = G_Local(y, w, star=True, permutations=PERMS, seed=SEED)
    gi_p = gistar.p_sim.copy()
    gi_p[isolate_mask] = np.nan
    gi_p_fdr = np.full_like(gi_p, np.nan, dtype=float)
    gvalid = ~np.isnan(gi_p)
    gi_p_fdr[gvalid] = bh_fdr(gi_p[gvalid])

    def gi_class(z, p, iso):
        if iso or np.isnan(p):
            return "Isolate (no neighbours)"
        if p >= ALPHA:
            return "Not-Significant"
        return "Hot-Spot" if z > 0 else "Cold-Spot"

    gi_cluster = [gi_class(z, p, iso) for z, p, iso in zip(gistar.Zs, gi_p_fdr, isolate_mask)]

    out = gdf[id_cols].copy()
    out["value"] = y
    out["local_I"] = lisa.Is
    out["z_sim"] = lisa.z_sim
    out["p_raw"] = p_raw
    out["p_fdr"] = p_fdr
    out["quadrant"] = [QUAD[q] for q in lisa.q]
    out["cluster_raw"] = cluster_raw      # significant at raw alpha
    out["cluster_fdr"] = cluster_fdr      # significant after BH-FDR (headline)
    out["gistar_z"] = gistar.Zs
    out["gistar_p_fdr"] = gi_p_fdr
    out["gistar_cluster"] = gi_cluster

    stats = {
        "label": label,
        "n_total": int(n_total),
        "n_used": int(n_used),
        "n_isolates": int(n_isolates),
        "global_moran_I": float(mi.I),
        "global_moran_z": float(mi.z_sim),
        "global_moran_p_sim": float(mi.p_sim),
        "permutations": PERMS,
        "alpha": ALPHA,
        "n_valid_for_lisa": int(valid.sum()),
        "n_sig_raw": int(sig_raw.sum()),
        "n_sig_fdr": int(sig_fdr.sum()),
        "cluster_counts_fdr": pd.Series(cluster_fdr).value_counts().to_dict(),
        "cluster_counts_raw": pd.Series(cluster_raw).value_counts().to_dict(),
        "gistar_hot_fdr": int(sum(1 for c in gi_cluster if c == "Hot-Spot")),
        "gistar_cold_fdr": int(sum(1 for c in gi_cluster if c == "Cold-Spot")),
    }
    print("  n sig raw:", stats["n_sig_raw"], " n sig fdr:", stats["n_sig_fdr"])
    print("  FDR cluster counts:", stats["cluster_counts_fdr"])
    return out, stats, gdf, lisa


def main():
    summary = {}

    # ---------- ADMIN-1 (priority) ----------
    a1 = gpd.read_file(ADMIN1_GEOJSON)
    # property names from inspection: id, iso3, cntry, name, ..., risk
    a1 = a1.rename(columns={"risk": "risk_surface"})
    out_a1, stats_a1, gdf_a1, lisa_a1 = run_layer(
        a1, "risk_surface",
        id_cols=["id", "iso3", "cntry", "name"],
        label="ADMIN-1 (IPUMS, ~97 countries)",
    )
    out_a1.to_csv(f"{OUTDIR}/lisa_admin1.csv", index=False)
    summary["admin1"] = stats_a1

    # ---------- COUNTRY ----------
    cgdf = gpd.read_file(COUNTRY_GEOJSON)
    scores = pd.read_csv(SCORES_CSV)
    # join composite score onto polygons via ISO A3
    cgdf["iso3"] = cgdf["ADM0_A3"]
    # repair a few NE codes where ADM0_A3 == '-99'
    bad = cgdf["iso3"] == "-99"
    cgdf.loc[bad, "iso3"] = cgdf.loc[bad, "ISO_A3_EH"]
    cgdf = cgdf.merge(scores[["iso3", "country_name", "composite_score"]], on="iso3", how="left")
    matched = cgdf["composite_score"].notna().sum()
    print(f"\ncountry polygons matched to scores: {matched} / {len(cgdf)}")
    out_c, stats_c, gdf_c, lisa_c = run_layer(
        cgdf, "composite_score",
        id_cols=["iso3", "country_name"],
        label="COUNTRY (FLSRI composite)",
    )
    out_c.to_csv(f"{OUTDIR}/lisa_country.csv", index=False)
    summary["country"] = stats_c

    # ---------- RECONCILIATION: country Global Moran's I under alternative weights ----------
    # The locked validation reports composite Moran's I ~= 0.49. That figure was
    # computed on a centroid k-nearest-neighbour graph (the validation step has no
    # contiguity polygons), whereas our headline uses Queen contiguity on polygons.
    # We recompute Global Moran's I under KNN graphs to show the 0.57 (Queen) vs ~0.49
    # gap is a weights-definition effect, not a data discrepancy.
    from libpysal.weights import KNN
    recon = {"queen_contiguity_I": stats_c["global_moran_I"]}
    cc = gdf_c[gdf_c["composite_score"].notna()].reset_index(drop=True)
    yv = cc["composite_score"].values.astype(float)
    pts = np.array([(geom.representative_point().x, geom.representative_point().y)
                    for geom in cc.geometry])
    for k in (4, 6, 8):
        wk = KNN.from_array(pts, k=k)
        wk.transform = "r"
        np.random.seed(SEED)
        mik = Moran(yv, wk, permutations=PERMS)
        recon[f"knn{k}_I"] = float(mik.I)
    summary["country_reconciliation"] = recon
    print("\nCountry Global Moran's I reconciliation (weights sensitivity):")
    print(" ", recon)

    with open(f"{OUTDIR}/lisa_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print("\nWrote lisa_admin1.csv, lisa_country.csv, lisa_summary.json")

    # also persist the geometries used (for the map), with cluster join keys
    out_a1.to_json  # noqa
    gdf_a1[["id", "iso3", "cntry", "name", "geometry"]].assign(
        local_I=out_a1["local_I"].values,
        p_fdr=out_a1["p_fdr"].values,
        cluster=out_a1["cluster_fdr"].values,
        value=out_a1["value"].values,
    ).to_file(f"{OUTDIR}/_lisa_admin1_geo.geojson", driver="GeoJSON")
    gdf_c.assign(
        local_I=out_c["local_I"].values,
        p_fdr=out_c["p_fdr"].values,
        cluster=out_c["cluster_fdr"].values,
        value=out_c["value"].values,
    )[["iso3", "country_name", "geometry", "local_I", "p_fdr", "cluster", "value"]].to_file(
        f"{OUTDIR}/_lisa_country_geo.geojson", driver="GeoJSON")
    print("Wrote _lisa_admin1_geo.geojson, _lisa_country_geo.geojson")


if __name__ == "__main__":
    main()
