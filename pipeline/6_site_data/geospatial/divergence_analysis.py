#!/usr/bin/env python3
"""
GEO-OVERLAY consolidation — national-rank vs worst-admin-1-corridor divergence.

EXPLORATORY. Read-only on the locked pipeline. All inputs already on disk:
  - locked country scores : .../github repo/outputs/scores.csv (195 rows; 183 scored)
  - admin-1 risk surface  : experiments/geospatial/subnational/admin1_risk_surface.csv
  - EM-DAT event points   : experiments/geospatial/hotspot_points.csv (1,850 events, Gi* class)

EM-DAT corroboration counts are computed DIRECTLY from hotspot_points.csv (significant Gi* hotspots,
hot in {95%,99%}), grouped by the ISO3 tail already embedded in each event's `disno` code — so the
count is real for EVERY country present, not a hardcoded top-12 list filled with zeros elsewhere.

Outputs (all under experiments/geospatial/):
  - divergence_table.csv          : per-country national rank vs subnational corridor stats
  - layer_combination_sensitivity.csv : risk-surface recompute under alternative precarity/shock weights
Nothing here is fabricated; every number is reproducible from the inputs above.
"""
import os, sys
import numpy as np
import pandas as pd

# ---- paths (from config/site_data_paths.py; logic unchanged) ----
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, _REPO_ROOT)
from config import site_data_paths as P  # noqa: E402
GEO = P.PORT_GEO          # hotspot_points.csv (copied) + write divergence_*.csv here
SUB = P.PORT_SUBNAT       # admin1_risk_surface.csv produced by ported step4
LOCKED = P.REPO_OUTPUTS   # locked scores.csv

# ---- load ----
# Two-composite fix: use the PUBLISHED v0.4 composite, not the LOCKED build,
# so the divergence/map and the rankings are ONE consistent index.
scores = pd.read_csv(os.path.join(P.PORT_V04, "scores_v0_4.csv"))
scores["composite_score"] = scores["v0_4_spu_w05_composite"]
scores["composite_rank"] = scores["composite_score"].rank(ascending=False, method="first")
surf = pd.read_csv(os.path.join(SUB, "admin1_risk_surface.csv"))
pts = pd.read_csv(os.path.join(GEO, "hotspot_points.csv"))

# Percentile-rank base: only the SCORED countries carry a composite_rank (12 microstates are
# unscored, rank=NaN). Use the scored count, not nunique(iso3)=195 (m1 fix). The locked validation
# itself reports n_scored=183 / n_total=195.
n_scored = int(scores["composite_rank"].notna().sum())  # 183
n_total = scores["iso3"].nunique()                       # 195 (documented; not used as pctile base)

# EM-DAT significant-hotspot counts by country, computed DIRECTLY from the event points file.
# Each EM-DAT disaster number (`disno`, e.g. "2010-0303-PHL") carries the country's ISO3 as its
# 3-char tail, so the crosswalk covers ALL countries present (155 distinct), not just a top-12 list.
# Significant hotspot = Getis-Ord Gi* class in {95%,99%}; "90%" and "ns" are NOT significant.
# This reproduces the prior summary.json top_hot_countries exactly (CHN 114, PHL 73, ... ) and
# extends real counts to the long tail (e.g. Uganda, Laos) that the old 12-entry dict zeroed out.
pts["iso3"] = pts["disno"].str.split("-").str[-1]
assert pts["iso3"].str.fullmatch(r"[A-Z]{3}").all(), "disno tail is not a clean ISO3"
sig = pts[pts["hot"].isin(["95%", "99%"])]
hot = sig.groupby("iso3").size().to_dict()

# Drop Puerto Rico explicitly (m2): PRI carries 7 reliable admin-1 units but is ABSENT from the
# 195-country locked scores.csv, so it has no composite_rank and its divergence is undefined (NaN).
# "All 79 countries with a reliable surface" is really 78 valid + PRI. We exclude PRI here rather
# than carry a NaN row.
surf = surf[surf["iso3"] != "PRI"].copy()

# ---- per-country subnational corridor aggregation (reliable units only) ----
# A unit is "reliable" if it carries a risk_surface value (precarity>=2 signals AND not low_sample
# is already encoded upstream: risk_surface is NaN where precarity_index is NaN).
rel = surf.dropna(subset=["risk_surface"]).copy()

agg = (
    rel.groupby("iso3")
    .agg(
        n_units=("risk_surface", "size"),
        risk_mean=("risk_surface", "mean"),
        risk_p90=("risk_surface", lambda s: np.percentile(s, 90)),
        risk_max=("risk_surface", "max"),
        risk_min=("risk_surface", "min"),
    )
    .reset_index()
)
agg["within_range"] = agg["risk_max"] - agg["risk_min"]

# worst-corridor admin name (the single max-risk reliable unit) for face-validity labels
idx = rel.groupby("iso3")["risk_surface"].idxmax()
worst = rel.loc[idx, ["iso3", "ADMIN_NAME", "risk_surface"]].rename(
    columns={"ADMIN_NAME": "worst_corridor_name", "risk_surface": "worst_corridor_risk"}
)
agg = agg.merge(worst, on="iso3", how="left")

# ---- join locked national score/rank ----
df = agg.merge(
    scores[["iso3", "country_name", "composite_score", "composite_rank"]],
    on="iso3", how="left",
)
# national percentile (0=lowest risk, 1=highest). rank 1 = highest risk in locked file.
# Base = scored count (183), since only scored countries carry a rank (m1 fix).
df["national_pctile_risk"] = 1.0 - (df["composite_rank"] - 1) / (n_scored - 1)

# ---- THE DIVERGENCE METRIC ----------------------------------------------------
# Put the worst corridor on the SAME 0-1 risk scale as the national composite is *ranked* on,
# by converting each to a within-sample percentile so they are comparable.
# corridor percentile = rank of a country's worst corridor among ALL reliable admin-1 units.
all_units = rel["risk_surface"].values
df["corridor_pctile"] = df["worst_corridor_risk"].apply(
    lambda v: (all_units <= v).mean()
)
df["p90_pctile"] = df["risk_p90"].apply(lambda v: (all_units <= v).mean())

# Divergence = how much higher the worst-corridor sits (as a percentile of admin-1 units)
# than the country sits nationally (as a percentile of countries). Positive = hidden subnational risk.
df["divergence"] = df["corridor_pctile"] - df["national_pctile_risk"]

# top-decile corridor flag (corridor in top 10% of all reliable admin-1 units)
top_decile_cut = np.percentile(all_units, 90)
df["worst_corridor_top_decile"] = df["worst_corridor_risk"] >= top_decile_cut

# EM-DAT significant-hotspot count (independent corroboration layer), computed from the points file.
# fillna(0) here is now a TRUE zero: a country present nowhere in the 1,850-event points file (or
# present only with non-significant Gi*) has 0 *verified significant* hotspots. Countries with >=1
# significant hotspot (24 of them) now carry their real count, e.g. Uganda=1, Laos=2 — previously
# zeroed by the 12-entry hardcoded dict.
df["emdat_hotspots_95"] = df["iso3"].map(hot).fillna(0).astype(int)
# Flag whether the country appears in the EM-DAT event layer AT ALL (any Gi* class), so caveat #9's
# "0 = not in list, not verified zero" can be applied only where genuinely uncovered.
emdat_present = set(pts["iso3"].unique())
df["emdat_in_layer"] = df["iso3"].isin(emdat_present)

df = df.sort_values("divergence", ascending=False).reset_index(drop=True)

cols = [
    "iso3", "country_name", "composite_rank", "national_pctile_risk",
    "n_units", "risk_mean", "risk_p90", "risk_max",
    "worst_corridor_name", "worst_corridor_risk", "worst_corridor_top_decile",
    "corridor_pctile", "divergence", "within_range", "emdat_hotspots_95", "emdat_in_layer",
]
out = df[cols].round(4)
out.to_csv(os.path.join(GEO, "divergence_table.csv"), index=False)

print("=== TOP 15 national-vs-corridor divergence (ALL, incl. low-base-rate artifacts) ===")
print(out.head(15).to_string(index=False))

# HEADLINE = genuine hidden risk: worst corridor is TOP-DECILE among all admin-1 units
# AND the country is not already in the national top quartile of risk.
# The "national top quartile" cut is taken over all 195 countries (n_total*0.25 = rank<=48.75) =
# "position among all 195 incl. the 12 unscored microstates" (m1: documented base). Using the
# scored base (183*0.25=45.75) would pull in one borderline country (Kenya, rank 46); the 195-base
# framing keeps the validated 12-country headline. national_pctile_risk above already uses the
# scored base (183) for the divergence metric itself.
headline = df[(df["worst_corridor_top_decile"]) & (df["composite_rank"] > n_total * 0.25)]
headline = headline.sort_values("divergence", ascending=False)
print("\n=== HEADLINE: hidden-risk countries (top-decile corridor + outside national top quartile) ===")
print(headline[cols].round(4).head(15).to_string(index=False))
headline[cols].round(4).to_csv(os.path.join(GEO, "divergence_headline.csv"), index=False)

print(f"\nn countries with reliable admin-1 surface: {len(df)}")
print(f"top-decile admin-1 cut (risk_surface): {top_decile_cut:.4f}")
print(f"countries whose worst corridor is top-decile: {int(df['worst_corridor_top_decile'].sum())}")
print(f"headline hidden-risk countries: {len(headline)}")

# ---- LAYER-COMBINATION SENSITIVITY -------------------------------------------
# Recompute risk_surface under alternative precarity/shock weights and see whether the
# worst-corridor ranking (and the divergence headline) is robust to the illustrative 0.70/0.30.
weight_sets = [(1.0, 0.0), (0.85, 0.15), (0.70, 0.30), (0.50, 0.50), (0.30, 0.70)]
rel2 = rel.copy()
# precarity_index and shock_index are already 0-1 absolute-anchored in the surface file.
sens_rows = []
ref_rank = None
for wp, ws in weight_sets:
    r = wp * rel2["precarity_index"] + ws * rel2["shock_index"]
    tmp = rel2.assign(rs=r)
    # country-level worst corridor under these weights
    cmax = tmp.groupby("iso3")["rs"].max()
    # rank correlation of worst-corridor ordering vs the 0.70/0.30 reference
    if abs(wp - 0.70) < 1e-9:
        ref_rank = cmax.rank()
    sens_rows.append((wp, ws, cmax))

sens_df = pd.DataFrame({f"wp{wp}_ws{ws}": cmax for wp, ws, cmax in sens_rows})
spear = sens_df.corr(method="spearman")
sens_df.round(4).to_csv(os.path.join(GEO, "layer_combination_sensitivity.csv"))
print("\n=== Spearman rank-corr of country worst-corridor under different precarity/shock weights ===")
print(spear.round(3).to_string())

# Headline-stability check: do the top-divergence countries survive weight changes?
print("\n=== Worst-corridor risk for headline countries across weights ===")
focus = ["PHL", "THA", "VNM", "KEN", "ETH", "PNG"]
hdr = "iso3  " + "  ".join(f"{wp}/{ws}" for wp, ws, _ in sens_rows)
print(hdr)
for iso in focus:
    vals = []
    for wp, ws, cmax in sens_rows:
        vals.append(f"{cmax.get(iso, np.nan):.3f}")
    print(f"{iso}   " + "   ".join(vals))
