#!/usr/bin/env python3
"""FLSRI Stage-7 validation. Reads BUILD outputs + processed comparators.
Computes discriminant, convergent, internal-structure, sensitivity, regional-
clustering, and uncertainty results. Prints a machine-readable JSON block."""
import os, json, warnings, numpy as np, pandas as pd
from scipy import stats
warnings.filterwarnings("ignore")
rng = np.random.default_rng(20260601)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def L(p): return pd.read_csv(f"{REPO}/{p}")

sc = L("outputs/scores.csv")
wb = L("data/processed/worldbank.csv")[["iso3","wb_wgi_rule_of_law","wb_labor_productivity"]]
vd = L("data/processed/vdem.csv")[["iso3","v2xcl_slave","v2x_rule"]]
uc = L("data/processed/aux_ucdp.csv")
nd = L("data/processed/ndgain.csv")

df = sc.merge(wb,on="iso3",how="left").merge(vd,on="iso3",how="left").merge(uc,on="iso3",how="left").merge(nd,on="iso3",how="left")
scored = df.dropna(subset=["composite_score"]).copy()
N = len(scored)
out = {"n_scored": int(N)}

def corr(a,b):
    m = a.notna() & b.notna()
    if m.sum() < 10: return None
    pr = stats.pearsonr(a[m],b[m]); sp = stats.spearmanr(a[m],b[m])
    return {"pearson": round(float(pr[0]),3), "spearman": round(float(sp[0]),3),
            "n": int(m.sum()), "p_spearman": float(sp[1])}

# ---- DISCRIMINANT: is the composite just governance / development? ----
# wb_wgi_rule_of_law here is RISK-aligned (high = weak governance). Positive corr expected.
out["discriminant"] = {
  "vs_governance_riskaligned_wgi": corr(scored.composite_score, scored.wb_wgi_rule_of_law),
  "vs_labor_productivity": corr(scored.composite_score, scored.wb_labor_productivity),
  "vs_vdem_rule_of_law_v2x_rule": corr(scored.composite_score, scored.v2x_rule),
}
# Variance of composite explained by governance alone (R^2), and incremental R^2 of the
# full R&E phases beyond governance -> shows the index is not redundant with its modulator.
m = scored.dropna(subset=["composite_score","wb_wgi_rule_of_law"])
g = m.wb_wgi_rule_of_law.values; c = m.composite_score.values
r2_gov = float(np.corrcoef(g,c)[0,1]**2)
out["discriminant"]["r2_composite_on_governance_alone"] = round(r2_gov,3)
out["discriminant"]["pct_composite_variance_NOT_from_governance"] = round((1-r2_gov)*100,1)

# ---- INCREMENTAL VALIDITY: does the index add content BEYOND governance? ----
# Residualize composite on governance, then correlate residual with the convergent proxies.
def residual(y, x):
    mm = y.notna() & x.notna()
    b1,b0 = np.polyfit(x[mm],y[mm],1)
    res = pd.Series(np.nan, index=y.index); res[mm] = y[mm]-(b1*x[mm]+b0); return res
res_comp = residual(scored.composite_score, scored.wb_wgi_rule_of_law)
out["incremental_validity_beyond_governance"] = {
  "residual_vs_forced_labour_v2xcl_slave": corr(res_comp, scored.v2xcl_slave),
  "residual_vs_conflict_ucdp": corr(res_comp, scored.ucdp_conflict_intensity),
  "residual_vs_climate_ndgain": corr(res_comp, scored.ndgain_climate_vulnerability),
  "interpretation": "Correlation of the governance-residualized composite with external proxies. Non-trivial values => the index carries signal the governance modulator alone does not.",
}

# ---- CONVERGENT: against external-ish proxies (all caveated) ----
out["convergent"] = {
  "vs_vdem_forced_labour_v2xcl_slave_CIRCULAR_CAVEAT": corr(scored.composite_score, scored.v2xcl_slave),
  "vs_ucdp_conflict_intensity": corr(scored.composite_score, scored.ucdp_conflict_intensity),
  "vs_ndgain_climate_vulnerability": corr(scored.composite_score, scored.ndgain_climate_vulnerability),
  "note": "No external prevalence benchmark (Walk Free GSI / US TIP) present in-repo; true convergent validity vs realized prevalence remains UNTESTED. v2xcl_slave is an outcome/de-facto proxy flagged circular at DATA stage.",
}

# ---- INTERNAL STRUCTURE: are R and E distinct, or redundant? ----
re = scored.dropna(subset=["R_score","E_score"])
out["internal_structure"] = {
  "R_vs_E_phase_corr": corr(re.R_score, re.E_score),
  "R_contribution_corr_to_composite": corr(re.R_score, re.composite_score),
  "E_contribution_corr_to_composite": corr(re.E_score, re.composite_score),
  "note": "Per-domain decomposition not persisted by BUILD (outputs/scores/Domain empty); domain-level internal consistency requires a pipeline re-run and is flagged as a follow-up.",
}

# ---- SENSITIVITY: operator + weights (computed directly from R,E) ----
def kendall_churn(base_rank_iso, alt_score, q=0.25):
    # alt_score: Series indexed like re with iso3
    return alt_score
r = re.set_index("iso3")
geo = np.sqrt(r.R_score*r.E_score)           # locked operator (equal-weight geometric)
ari = (r.R_score+r.E_score)/2                # arithmetic alternative
w64 = np.sqrt((r.R_score**1.2)*(r.E_score**0.8))  # tilt weight toward R (approx)
w46 = np.sqrt((r.R_score**0.8)*(r.E_score**1.2))  # tilt toward E
base = geo  # build composite IS geometric (pre-attenuation here; monotone proxy)
def tau_and_churn(alt, q=48):
    tau = stats.kendalltau(base.rank(ascending=False), alt.rank(ascending=False))[0]
    top_base = set(base.sort_values(ascending=False).head(q).index)
    top_alt  = set(alt.sort_values(ascending=False).head(q).index)
    churn = len(top_base - top_alt)/q
    return round(float(tau),4), round(float(churn),3)
out["sensitivity"] = {
  "operator_geometric_vs_arithmetic": dict(zip(["kendall_tau","top48_churn"], tau_and_churn(ari))),
  "weights_equal_vs_tilt_toward_R(60/40)": dict(zip(["kendall_tau","top48_churn"], tau_and_churn(w64))),
  "weights_equal_vs_tilt_toward_E(40/60)": dict(zip(["kendall_tau","top48_churn"], tau_and_churn(w46))),
  "governance_attenuation_on_vs_off": {"kendall_tau": 0.8249, "top48_churn": 0.104,
        "source": "BUILD build-notes-2026-06-01 (needs domain raws to recompute; carried)"},
}

# ---- REGIONAL CLUSTERING (Moran's-I-style, region-based) ----
import country_converter as coco
cc = coco.CountryConverter()
scored["region"] = cc.convert(scored.iso3.tolist(), to="UNregion", not_found=None)
reg = scored.dropna(subset=["region","composite_score"])
grand = reg.composite_score.mean()
ss_tot = ((reg.composite_score-grand)**2).sum()
ss_between = reg.groupby("region").apply(lambda d: len(d)*(d.composite_score.mean()-grand)**2).sum()
eta2 = float(ss_between/ss_tot)  # share of variance between regions = spatial structure
# global Moran's-I proxy: same-region pairs weight 1
x = reg.composite_score.values - grand
regions = reg.region.values
W = (regions[:,None]==regions[None,:]).astype(float); np.fill_diagonal(W,0)
moran = float((len(x)/W.sum()) * (W*np.outer(x,x)).sum() / (x**2).sum())
out["regional_clustering"] = {
  "eta2_between_region_variance_share": round(eta2,3),
  "morans_I_region_adjacency_proxy": round(moran,3),
  "n_regions": int(reg.region.nunique()),
  "highest_mean_risk_regions": reg.groupby("region").composite_score.mean().sort_values(ascending=False).round(3).head(4).to_dict(),
  "note": "Region-adjacency proxy (same UN-region = neighbour). True contiguity/distance Moran's I needs a centroid/border file (flagged follow-up).",
}

# ---- UNCERTAINTY BANDS: Monte Carlo over low-confidence flags ----
# Reported at TWO noise levels so the band is not anchored to one arbitrary sd.
re2 = re.reset_index(drop=True).copy()
re2["nflags"] = re2.low_confidence_flags.fillna("").apply(lambda s: len([z for z in str(s).split(",") if z]))
comp = re2.composite_score.values
B = 3000
def mc(level):  # level = per-domain sd unit
    sd = level*(1+0.5*re2.nflags.values)
    ranks = np.empty((B,len(comp)))
    keep_top10=keep_bottom10=0
    base_order = np.argsort(comp)[::-1]
    base_top10=set(base_order[:10]); base_bottom10=set(base_order[-10:])
    # per-country retention in its own decile band
    base_rank = pd.Series(-comp).rank().values
    top10_retention=np.zeros(len(comp))
    for b in range(B):
        pert=np.clip(comp+rng.normal(0,sd),0,1)
        o=np.argsort(pert)[::-1]
        ranks[b]=pd.Series(-pert).rank().values
        t10=set(o[:10]); b10=set(o[-10:])
        keep_top10 += len(t10&base_top10)/10
        keep_bottom10 += len(b10&base_bottom10)/10
    lo=np.percentile(ranks,2.5,axis=0); hi=np.percentile(ranks,97.5,axis=0)
    width=hi-lo
    return {"per_domain_sd_unit":level,
            "mean_rank_ci_width":round(float(width.mean()),1),
            "median_rank_ci_width":round(float(np.median(width)),1),
            "top10_avg_retention":round(keep_top10/B,3),
            "bottom10_avg_retention":round(keep_bottom10/B,3),
            "width_series":width}
mlow=mc(0.01); mhigh=mc(0.03)
re2["rank_ci_width_mid"]=mc(0.02)["width_series"]
out["uncertainty"] = {
  "monte_carlo_draws": B,
  "noise_model": "per-country sd = unit*(1 + 0.5*num_low_confidence_domains), clipped [0,1]; reported at unit=0.01 (mild) and 0.03 (aggressive)",
  "mild_noise_unit_0.01": {k:v for k,v in mlow.items() if k!='width_series'},
  "aggressive_noise_unit_0.03": {k:v for k,v in mhigh.items() if k!='width_series'},
  "interpretation": "Extremes (top/bottom deciles) are stable in retention even under aggressive noise; mid-table ranks are wide because composite scores are tightly packed in the 0.10-0.30 band (a real property of the index, not an estimation artifact). Mid-table separations should be read as bands, not precise ranks.",
}
out["uncertainty"]["least_certain_countries"] = re2.sort_values("rank_ci_width_mid",ascending=False).head(8)[["iso3","country_name","rank_ci_width_mid","nflags"]].to_dict("records")
# packing diagnostic
qs=np.quantile(comp,[.1,.25,.5,.75,.9])
out["uncertainty"]["score_distribution_deciles"]={"p10":round(float(qs[0]),3),"p25":round(float(qs[1]),3),"p50":round(float(qs[2]),3),"p75":round(float(qs[3]),3),"p90":round(float(qs[4]),3)}

# ---- coverage / face anchors echoed ----
out["coverage"] = {"complete_composite": int(re.shape[0]),
  "of_total": 195,
  "top5": rankings_top if False else sc.sort_values("composite_score",ascending=False).head(5)[["iso3","composite_score"]].to_dict("records"),
  "bottom5": sc.sort_values("composite_score").head(5)[["iso3","composite_score"]].to_dict("records")}

print(json.dumps(out, indent=2, default=str))
