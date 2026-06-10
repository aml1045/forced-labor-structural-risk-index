#!/usr/bin/env python3
"""
FLSRI Stage-7 validation v2 — HONEST PROTOCOL with all four ratified fixes.

Structure under test (selectable via --build):
  v0_3        : the PLAIN v0.3 composite (`v0_3_composite`, ~184 countries), the
                CONSERVATIVE working structure the criteria were registered on.
  v0_4_spu_w05: the DISPLAYED site build (v0.4-SPU, SPU-only de-biasing at
                w=0.5, `v0_4_spu_w05_*` columns of scores_v0_4.csv), re-checked
                against the SAME pre-registered criteria.

Four design constraints baked in:
  1. Pre-registered expected sign AND numeric FAILURE threshold for every test
     (defined in PREREG below; a validation that cannot fail is coherence).
  2. Every association reported as a {zero-order, governance-residualized, effective-n}
     triplet — never the residual alone.
  3. Real spatial effective-df via Clifford-Richardson-Hemon / Dutilleil (1993) on a
     distance weights matrix built from Natural Earth country centroids (Step 0),
     plus region-blocked (UN subregion) leave-region-out checks.
  4. Benjamini-Hochberg FDR across the convergent/incremental family.

All inputs are real files on disk; geometry is fetched/derived from Natural Earth
(see data/geometry/). Nothing is fabricated. Every number printed is reproducible.

Outputs: prints a machine-readable JSON block; writes outputs/validation_results_v2.json.
"""
import argparse, os, sys, json, warnings, numpy as np, pandas as pd
from scipy import stats
warnings.filterwarnings("ignore")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from config import site_data_paths as _P  # noqa: E402
EXP = _P.EXP  # external/experiments (or FLSRI_EXTERNAL_DATA override)
GSI = os.path.join(REPO, "data", "raw", "walk_free_gsi",
                   "2023-Global-Slavery-Index-Data.xlsx")
if not os.path.exists(GSI):  # license-gated workbook may live in the external dir
    _alt = os.path.join(_P.ULTRA, "walk_free_gsi", "2023-Global-Slavery-Index-Data.xlsx")
    if os.path.exists(_alt):
        GSI = _alt

_ap = argparse.ArgumentParser()
_ap.add_argument("--build", choices=["v0_3", "v0_4_spu_w05"], default="v0_3")
_ap.add_argument("--out", default=os.path.join(REPO, "outputs", "validation_results_v2.json"))
ARGS = _ap.parse_args()

# ----------------------------------------------------------------------------
# 0. PRE-REGISTRATION (FIX 1) — written before computing. Expected sign + the
#    numeric threshold at which the claim is declared a FAILURE.
# ----------------------------------------------------------------------------
PREREG = {
  "discriminant_governance_R2": {
     "test": "R^2 of v0_3_composite regressed on wb_wgi_rule_of_law (risk-aligned).",
     "expected": "High positive association (governance co-travels) BUT R^2 < 1.",
     "pass_if":  "R^2 <= 0.80  (i.e. >=20% of composite variance is NOT governance).",
     "FAIL_if":  "R^2 > 0.90   (index is governance with a relabel -> discriminant FAIL).",
     "grey_zone":"0.80 < R^2 <= 0.90 -> CONDITIONAL (governance-dominated but not identical)."},
  "incremental_FL_structure": {
     "test": "Governance-residualized composite vs the orthogonal/low-laden IPUMS FL-proximate layer "
             "(child_labour, internal_migration_5yr; both <0.30 |r| with governance per ipums memo), "
             "and vs climate (ND-GAIN) & conflict (UCDP) net of governance.",
     "expected": "child_labour +, internal_migration +, climate +, conflict + (all positive risk).",
     "pass_if":  "AT LEAST ONE non-circular FL-proximate residual correlation is positive in the "
                 "pre-registered direction AND BH-significant at the spatial effective-df.",
     "FAIL_if":  "NO FL-proximate residual is positive & BH-significant -> incremental-validity NOT demonstrated."},
  "convergent_vs_realized_prevalence": {
     "test": "Residual-on-residual (governance netted BOTH sides) vs GSI estimated prevalence /1,000.",
     "expected": "weak positive after residualizing.",
     "pass_if":  "residualized rho > 0 and BH-significant at effective-df.",
     "FAIL_if":  "residualized rho <= 0 OR not significant -> 'convergent validity vs realized prevalence "
                 "NOT demonstrated' (reported, not hidden). NB benchmark is itself contaminated "
                 "(GSI prevalence Spearman ~0.50 vs governance) so a null here is uninformative, not disconfirming."},
  "REFUSED_criteria": {
     "GSI_Total_Vulnerability": "REFUSED — r~0.91 with governance == circular (shares the backbone).",
     "GLOTIP_detection": "REFUSED — sign-INVERTED (Spearman ~ -0.27 vs governance); detection is a "
                         "state-capacity artifact, not prevalence. Convergence with it would reward strong states."},
  "internal_structure_R_E": {
     "test": "Correlation of R (Recruitment/structural) and E (Exploitation/enabling) phase scores.",
     "expected": "positive but well below unity (distinct, not redundant).",
     "pass_if":  "0.30 <= r(R,E) <= 0.90.",
     "FAIL_if":  "r(R,E) > 0.95 (one dimension) OR r(R,E) < 0.20 (incoherent)."},
  "uncertainty_tiers": {
     "test": "Monte-Carlo rank stability -> report TIERS not precise mid-table ranks.",
     "expected": "extreme tiers (top/bottom) stable; mid-table wide.",
     "pass_if":  "top-decile retention >= 0.70 under mild noise (tiers defensible).",
     "FAIL_if":  "top-decile retention < 0.50 (even the extremes are unstable)."},
  "mechanism_kafala_COHERENCE": {
     "test": "Do kafala-system states sit elevated on R? (LABELLED COHERENCE, not validation.)",
     "expected": "kafala states above non-kafala median on R.",
     "note": "This is a coherence/face check, NOT an independent criterion. Cannot 'fail' the index; "
             "reported as coherence only."},
}

def L(p): return pd.read_csv(p)

# ----------------------------------------------------------------------------
# 1. ASSEMBLE THE ANALYSIS FRAME
# ----------------------------------------------------------------------------
if ARGS.build == "v0_4_spu_w05":
    sc = L(os.path.join(_P.PORT_V04, "scores_v0_4.csv"))
    sc = sc.rename(columns={"v0_4_spu_w05_composite": "composite",
                            "v0_4_spu_w05_R": "R", "v0_4_spu_w05_E": "E"})
    STRUCTURE = ("DISPLAYED v0.4-SPU build (SPU-only de-biasing, w=0.5; the "
                 "site's published columns), re-checked against the criteria "
                 "pre-registered on v0.3")
else:
    sc = L(f"{EXP}/rebuild-v0.3/scores_v0_3.csv")
    sc = sc.rename(columns={"v0_3_composite":"composite","v0_3_R":"R","v0_3_E":"E"})
    STRUCTURE = "PLAIN v0.3 composite (conservative working structure)"
wb = L(f"{REPO}/data/processed/worldbank.csv")[["iso3","wb_wgi_rule_of_law","wb_labor_productivity"]]
nd = L(f"{REPO}/data/processed/ndgain.csv")
uc = L(f"{REPO}/data/processed/aux_ucdp.csv")
geo = L(f"{REPO}/data/geometry/country_geometry.csv")
import os as _os
if not _os.path.exists(f"{EXP}/ipums-signals/candidate_signals_country.csv"):
    raise SystemExit("The validation suite needs the IPUMS-derived aggregate table "
                     "(gated local input, not redistributed; see docs/REPRODUCING.md).")
ip = L(f"{EXP}/ipums-signals/candidate_signals_country.csv")
ip_cols = ["child_labour","internal_migration_5yr","vulnerable_employment","redflag_sector"]
ip = ip[["iso3"]+[c for c in ip_cols if c in ip.columns]]

# GSI estimated prevalence — the ONLY semi-usable external benchmark (contaminated, caveated).
# The raw GSI workbook is NOT shipped in the repo (license-restricted; add it to
# data/raw/walk_free_gsi/ to enable the GSI-contamination check). If it is absent
# the GSI benchmark is skipped and the rest of the validation still runs.
if os.path.exists(GSI):
    import country_converter as coco
    cc = coco.CountryConverter()
    g = pd.read_excel(GSI, sheet_name="GSI 2023 summary data", header=2).dropna(subset=["Country"])
    g["iso3"] = cc.convert(g["Country"].tolist(), to="ISO3", not_found=None)
    prev_col = "Estimated prevalence of modern slavery per 1,000 population"
    gsi = g.dropna(subset=["iso3"])[["iso3", prev_col]].rename(columns={prev_col:"gsi_prevalence"})
    gsi = gsi.groupby("iso3", as_index=False).gsi_prevalence.mean()
else:
    print(f"[WARN] GSI workbook not found at {GSI}; GSI-contamination check skipped.")
    gsi = pd.DataFrame({"iso3": [], "gsi_prevalence": []})

df = (sc.merge(wb,on="iso3",how="left")
        .merge(nd,on="iso3",how="left")
        .merge(uc,on="iso3",how="left")
        .merge(geo[["iso3","lon","lat","region_un","subregion"]],on="iso3",how="left")
        .merge(ip,on="iso3",how="left")
        .merge(gsi,on="iso3",how="left"))
df = df.dropna(subset=["composite"]).reset_index(drop=True)
GOV = "wb_wgi_rule_of_law"
df = df.dropna(subset=[GOV]).reset_index(drop=True)   # need governance to residualize
N = len(df)
out = {"structure_under_test": STRUCTURE,
       "build": ARGS.build,
       "n_scored_with_governance": int(N),
       "prereg": PREREG}

# ----------------------------------------------------------------------------
# 2. SPATIAL EFFECTIVE DEGREES OF FREEDOM — Clifford-Richardson-Hemon (FIX 3)
# ----------------------------------------------------------------------------
# Build a row-standardised inverse-distance weights matrix from centroids, then
# compute Moran's I of the composite and the CRH effective sample size for a
# correlation test. CRH effective df for corr(x,y):
#   n_eff = 1 + 1 / sum_{k} ( rho_x(k) * rho_y(k) ) approximated via the trace
# We use the practical Dutilleil estimator: n_eff = 1 + N^2 / sum_ij (W~_ij^2)
# style is fragile; instead we use the standard CRH result that the variance of
# r is inflated by (1 + sum w_ij r_x(ij) r_y(ij)) -> effective N. We implement
# the widely-used approximation via spatial autocorrelation of BOTH series.
def haversine_km(lat1,lon1,lat2,lon2):
    R=6371.0
    p1,p2=np.radians(lat1),np.radians(lat2)
    dphi=np.radians(lat2-lat1); dl=np.radians(lon2-lon1)
    a=np.sin(dphi/2)**2+np.cos(p1)*np.cos(p2)*np.sin(dl/2)**2
    return 2*R*np.arcsin(np.sqrt(a))

gdf = df.dropna(subset=["lat","lon"]).reset_index(drop=True)
ng = len(gdf)
lat=gdf.lat.values; lon=gdf.lon.values
D=np.zeros((ng,ng))
for i in range(ng):
    D[i]=haversine_km(lat[i],lon[i],lat,lon)
# inverse-distance within a 4000km bandwidth (regional neighbourhood), row-standardised
BW=4000.0
W=np.where((D>0)&(D<=BW), 1.0/D, 0.0)
np.fill_diagonal(W,0.0)
rs=W.sum(axis=1, keepdims=True); rs[rs==0]=1.0
Wn=W/rs

def morans_I(z, Wm):
    zc=z-z.mean()
    num=(Wm*np.outer(zc,zc)).sum()
    den=(zc**2).sum()
    n=len(z); S0=Wm.sum()
    return (n/S0)*(num/den) if S0>0 else np.nan

mI_comp=morans_I(gdf.composite.values, Wn)

# Clifford-Richardson-Hemon effective sample size for a correlation between x,y.
# CRH (1989): Var(r) ~ 1/(n_eff-1), with
#   n_eff = 1 + N / [ (1/N) * sum_{i,j} c_x(i,j) c_y(i,j) / (sx^2 sy^2) ]  (matrix-trace form)
# Practical estimator used here (Dutilleil 1993 trace form):
#   n_eff = 1 + N^2 / trace( Sigma_x_hat * Sigma_y_hat ) using empirical spatial
# autocovariance binned by distance. We implement the binned-correlogram version.
def autocov_by_distance(z, Dm, edges):
    zc=z-z.mean(); n=len(z)
    cov=np.zeros(len(edges)-1); cnt=np.zeros(len(edges)-1)
    iu=np.triu_indices(n,1)
    dd=Dm[iu]; pp=(np.outer(zc,zc))[iu]
    for b in range(len(edges)-1):
        m=(dd>=edges[b])&(dd<edges[b+1])
        if m.sum()>0:
            cov[b]=pp[m].mean(); cnt[b]=m.sum()
    var=zc.var()
    return cov, cnt, var

def crh_neff(x, y, Dm):
    # bins out to global scale
    edges=np.array([0,500,1000,1500,2000,3000,4000,6000,8000,12000,20000,40000])
    cx,cnt,vx=autocov_by_distance(x,Dm,edges)
    cy,_,vy=autocov_by_distance(y,Dm,edges)
    rx=cx/vx; ry=cy/vy
    n=len(x)
    # CRH: 1/n_eff approx = 1/n + (2/n^2) * sum_k cnt_k * rx_k * ry_k  (k over distance bins)
    s=np.nansum(cnt*rx*ry)
    inv=1.0/n + (2.0/(n**2))*s
    neff=1.0/inv if inv>0 else n
    return float(max(2.0, min(n, neff)))

out["spatial"]={
  "geometry_source":"Natural Earth 1:110m admin_0 countries (LABEL_X/LABEL_Y centroids, REGION_UN, SUBREGION). data/geometry/country_geometry.csv",
  "n_with_centroid": int(ng),
  "n_without_centroid_dropped_from_spatial": int(N-ng),
  "weights":"inverse-distance, 4000km bandwidth, row-standardised; haversine on centroids",
  "morans_I_composite": round(float(mI_comp),3),
  "bandwidth_km": BW,
}

# ----------------------------------------------------------------------------
# 3. TRIPLET ENGINE (FIX 2 + FIX 3) — {zero-order, residualized, effective-n}
# ----------------------------------------------------------------------------
def residual(y, x):
    m=y.notna()&x.notna()
    if m.sum()<5: return pd.Series(np.nan,index=y.index)
    b1,b0=np.polyfit(x[m],y[m],1)
    r=pd.Series(np.nan,index=y.index); r[m]=y[m]-(b1*x[m]+b0); return r

res_comp_full = residual(df.composite, df[GOV])

def spatial_p(rho, neff):
    if neff<=2 or not np.isfinite(rho): return np.nan
    t=rho*np.sqrt((neff-2)/max(1e-9,(1-rho**2)))
    return float(2*stats.t.sf(abs(t), neff-2))

def triplet(name, benchmark_col, expected_sign, residualize_benchmark=True):
    """Return {zero-order, governance-residualized, naive-n, effective-n, p_eff}."""
    y=df.composite; b=df[benchmark_col]
    m0=y.notna()&b.notna()
    n0=int(m0.sum())
    if n0<10:
        return {"benchmark":benchmark_col,"n":n0,"note":"insufficient overlap (<10)"}
    zr_p=stats.pearsonr(y[m0],b[m0])[0]; zr_s=stats.spearmanr(y[m0],b[m0])[0]
    # residualized: composite residual vs (optionally residualized) benchmark
    rc=res_comp_full
    rb=residual(b, df[GOV]) if residualize_benchmark else b
    mr=rc.notna()&rb.notna()
    nr=int(mr.sum())
    res_p=stats.pearsonr(rc[mr],rb[mr])[0]; res_s=stats.spearmanr(rc[mr],rb[mr])[0]
    # effective-n on the residualized overlap using spatial geometry
    sub=df.loc[mr].dropna(subset=["lat","lon"])
    if len(sub)>=20:
        idx=sub.index.values
        ii=[list(gdf.index[gdf.iso3==df.loc[k,"iso3"]]) for k in idx]
        sel=[a[0] for a in ii if a]
        Dsub=D[np.ix_(sel,sel)]
        neff=crh_neff(rc.loc[idx].values[:len(sel)], rb.loc[idx].values[:len(sel)], Dsub)
    else:
        neff=float(nr)
    return {"benchmark":benchmark_col,"expected_sign":expected_sign,
            "zero_order_pearson":round(float(zr_p),3),"zero_order_spearman":round(float(zr_s),3),
            "residualized_pearson":round(float(res_p),3),"residualized_spearman":round(float(res_s),3),
            "n":n0,"n_residual_overlap":nr,"effective_n":round(neff,1),
            "p_residual_spearman_at_eff_df":spatial_p(res_s,neff)}

# ----------------------------------------------------------------------------
# 4. DISCRIMINANT (governance R^2) — PREREG discriminant_governance_R2
# ----------------------------------------------------------------------------
m=df.dropna(subset=["composite",GOV])
r=np.corrcoef(m[GOV],m.composite)[0,1]
r2=float(r**2)
verdict = "FAIL" if r2>0.90 else ("CONDITIONAL" if r2>0.80 else "PASS")
out["discriminant"]={
  "composite_vs_governance_pearson_r":round(float(r),3),
  "R2_composite_on_governance": round(r2,3),
  "pct_variance_NOT_governance": round((1-r2)*100,1),
  "prereg_verdict": verdict,
  "also_vs_labor_productivity": triplet("labprod","wb_labor_productivity","+ (poorer=more risk; risk-aligned check)",residualize_benchmark=False),
}

# ----------------------------------------------------------------------------
# 5. INCREMENTAL FL STRUCTURE + CONVERGENT family (FIX 2,3,4)
# ----------------------------------------------------------------------------
fam={}
# FL-proximate (IPUMS) — child_labour & internal_migration_5yr are the low/orthogonal-laden ones
if "child_labour" in df.columns:
    fam["ipums_child_labour"]=triplet("ipums_child_labour","child_labour","+",residualize_benchmark=True)
if "internal_migration_5yr" in df.columns:
    fam["ipums_internal_migration_5yr"]=triplet("ipums_internal_migration_5yr","internal_migration_5yr","+",residualize_benchmark=True)
if "vulnerable_employment" in df.columns:
    fam["ipums_vulnerable_employment_LADEN"]=triplet("ipums_vulnerable_employment","vulnerable_employment","+ (moderately governance-laden, caveat)",residualize_benchmark=True)
# orthogonal structural correlates net of governance
fam["climate_ndgain"]=triplet("ndgain","ndgain_climate_vulnerability","+",residualize_benchmark=True)
fam["conflict_ucdp"]=triplet("ucdp","ucdp_conflict_intensity","+",residualize_benchmark=True)
# convergent vs realized prevalence (residual-on-residual, contaminated benchmark)
fam["gsi_prevalence_RESIDUAL_ON_RESIDUAL"]=triplet("gsi_prevalence","gsi_prevalence","+ (weak; benchmark contaminated)",residualize_benchmark=True)

# Benjamini-Hochberg FDR across the family (FIX 4) on the residualized spearman p (eff-df)
fam_tests=[(k,v) for k,v in fam.items() if isinstance(v,dict) and v.get("p_residual_spearman_at_eff_df") is not None and np.isfinite(v.get("p_residual_spearman_at_eff_df"))]
ps=[v["p_residual_spearman_at_eff_df"] for _,v in fam_tests]
order=np.argsort(ps); mtests=len(ps)
bh=np.empty(mtests)
prev=1.0
for rank in range(mtests-1,-1,-1):
    i=order[rank]
    q=ps[i]*mtests/(rank+1)
    prev=min(prev,q); bh[i]=min(prev,1.0)
for j,(k,v) in enumerate(fam_tests):
    v["BH_q_value"]=round(float(bh[j]),4)
    v["BH_significant_q<0.05"]=bool(bh[j]<0.05)
out["incremental_and_convergent_family"]=fam
out["FDR"]={"method":"Benjamini-Hochberg across residualized convergent family",
            "m_tests":mtests,
            "family":[k for k,_ in fam_tests]}

# Pre-registered incremental verdict
fl_proximate_keys=["ipums_child_labour","ipums_internal_migration_5yr"]
fl_hits=[k for k in fl_proximate_keys if k in fam and isinstance(fam[k],dict)
         and fam[k].get("residualized_spearman",0)>0 and fam[k].get("BH_significant_q<0.05")]
out["incremental_validity_verdict"]= "PASS (FL-specific structure demonstrated)" if fl_hits else "NOT DEMONSTRATED"
out["incremental_validity_hits"]=fl_hits
# convergent prevalence verdict
gp=fam["gsi_prevalence_RESIDUAL_ON_RESIDUAL"]
if gp.get("note", "").startswith("insufficient overlap"):
    out["convergent_prevalence_verdict"]=("NOT RUN (GSI workbook not present locally; "
        "license-gated — see data/raw/walk_free_gsi/)")
else:
    out["convergent_prevalence_verdict"]=("demonstrated" if (gp.get("residualized_spearman",0)>0 and gp.get("BH_significant_q<0.05"))
        else "NOT DEMONSTRATED (disclosed; benchmark is governance-contaminated so a null is uninformative)")

# ----------------------------------------------------------------------------
# 6. INTERNAL STRUCTURE (R vs E)
# ----------------------------------------------------------------------------
re_=df.dropna(subset=["R","E"])
rRE=float(stats.spearmanr(re_.R,re_.E)[0]); pRE=float(stats.pearsonr(re_.R,re_.E)[0])
re_verdict="FAIL" if (pRE>0.95 or pRE<0.20) else "PASS"
out["internal_structure"]={
  "R_vs_E_pearson":round(pRE,3),"R_vs_E_spearman":round(rRE,3),"n":int(len(re_)),
  "R_vs_composite_pearson":round(float(stats.pearsonr(re_.R,re_.composite)[0]),3),
  "E_vs_composite_pearson":round(float(stats.pearsonr(re_.E,re_.composite)[0]),3),
  "prereg_verdict":re_verdict}

# ----------------------------------------------------------------------------
# 7. UNCERTAINTY -> TIERS (FIX: report tiers not precise mid-table ranks)
# ----------------------------------------------------------------------------
rng=np.random.default_rng(20260602)
comp=re_.composite.values; nC=len(comp)
B=4000
base_order=np.argsort(comp)[::-1]
ntop=max(10,int(0.1*nC)); nbot=max(10,int(0.1*nC))
base_top=set(base_order[:ntop]); base_bot=set(base_order[-nbot:])
def mc(level):
    sd=level
    keep_top=keep_bot=0.0
    ranks=np.empty((B,nC))
    for b in range(B):
        pert=np.clip(comp+rng.normal(0,sd,nC),0,1)
        o=np.argsort(pert)[::-1]
        ranks[b]=pd.Series(-pert).rank().values
        keep_top+=len(set(o[:ntop])&base_top)/ntop
        keep_bot+=len(set(o[-nbot:])&base_bot)/nbot
    w=np.percentile(ranks,97.5,axis=0)-np.percentile(ranks,2.5,axis=0)
    return {"sd":level,"top_decile_retention":round(keep_top/B,3),
            "bottom_decile_retention":round(keep_bot/B,3),
            "median_rank_CI_width":round(float(np.median(w)),1),
            "mean_rank_CI_width":round(float(w.mean()),1)}
mild=mc(0.02); aggr=mc(0.04)
unc_verdict="FAIL" if mild["top_decile_retention"]<0.50 else "PASS"
qs=np.quantile(comp,[.1,.25,.5,.75,.9])
# Tiered presentation: terciles by composite
tiers=pd.qcut(re_.composite, [0,1/3,2/3,1.0], labels=["Lower-risk tier","Mid tier","Higher-risk tier"])
out["uncertainty"]={
  "monte_carlo_draws":B,
  "mild_noise_sd_0.02":mild,"aggressive_noise_sd_0.04":aggr,
  "prereg_verdict":unc_verdict,
  "score_deciles":{"p10":round(float(qs[0]),3),"p25":round(float(qs[1]),3),"p50":round(float(qs[2]),3),"p75":round(float(qs[3]),3),"p90":round(float(qs[4]),3)},
  "packing_note":"composite tightly packed in mid-table -> report TIERS not precise mid-ranks",
  "tier_cut_thresholds":[round(float(re_.composite.quantile(1/3)),3),round(float(re_.composite.quantile(2/3)),3)],
}

# ----------------------------------------------------------------------------
# 8. MECHANISM REPRODUCTION (kafala) — COHERENCE, labelled as coherence
# ----------------------------------------------------------------------------
kafala_iso=["SAU","ARE","QAT","KWT","BHR","OMN","JOR","LBN"]
re2=df.dropna(subset=["R"]).copy()
re2["kafala"]=re2.iso3.isin(kafala_iso)
kmed=re2[re2.kafala].R.median(); nonk=re2[~re2.kafala].R.median()
present=[i for i in kafala_iso if i in set(re2.iso3)]
out["mechanism_kafala_COHERENCE"]={
  "LABEL":"COHERENCE CHECK — not an independent validation criterion; cannot pass/fail the index.",
  "kafala_states_present":present,
  "kafala_median_R":round(float(kmed),3),"nonkafala_median_R":round(float(nonk),3),
  "kafala_above_nonkafala_median": bool(kmed>nonk),
  "kafala_R_percentile_among_all": {i: round(float((re2.R<re2.loc[re2.iso3==i,"R"].iloc[0]).mean()),2) for i in present},
}

# ----------------------------------------------------------------------------
# 9. OVERALL VERDICT
# ----------------------------------------------------------------------------
disc=out["discriminant"]["prereg_verdict"]
incr=out["incremental_validity_verdict"].startswith("PASS")
intern=out["internal_structure"]["prereg_verdict"]=="PASS"
unc=out["uncertainty"]["prereg_verdict"]=="PASS"
if disc=="FAIL" or not intern:
    overall="FAIL"
elif disc=="PASS" and incr and intern and unc:
    overall="PASS"
else:
    overall="CONDITIONAL"
out["OVERALL_VERDICT"]={
  "verdict":overall,
  "discriminant":disc,"incremental_FL_structure":out["incremental_validity_verdict"],
  "internal_structure":out["internal_structure"]["prereg_verdict"],
  "uncertainty":out["uncertainty"]["prereg_verdict"],
  "convergent_vs_realized_prevalence":out["convergent_prevalence_verdict"],
}

with open(ARGS.out,"w") as f:
    json.dump(out,f,indent=2,default=str)
print(json.dumps(out,indent=2,default=str))
