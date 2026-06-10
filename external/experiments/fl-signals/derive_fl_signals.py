#!/usr/bin/env python3
"""
FLSRI experiment: derive forced-labour-specific candidate signals from CTDC, GLOTIP, GSI.
Throw-it-all-in pass. Standardize 0-1 keyed on ISO3. Correlate each with WGI rule-of-law.
EXPLORATORY. No fabrication. Read-only on locked pipeline; writes only to experiments/fl-signals/.
"""
import os, warnings
import numpy as np
import pandas as pd
from scipy.stats import spearmanr, pearsonr

warnings.filterwarnings("ignore")
BASE = "<EXTERNAL-DATA>/1-starting area"
RAW = f"{BASE}/_source-data/raw"
OUT = f"{BASE}/Research Deliverables/experiments/fl-signals"
WB = "<EXTERNAL-DATA>/4-final deliverable/working/github repo/data/processed/worldbank.csv"
os.makedirs(OUT, exist_ok=True)

import country_converter as coco
cc = coco.CountryConverter()

def to_iso3(series):
    return cc.pandas_convert(series=pd.Series(series), to="ISO3", not_found=None)

def minmax(s):
    s = pd.to_numeric(s, errors="coerce")
    lo, hi = s.min(), s.max()
    if pd.isna(lo) or hi == lo:
        return s * np.nan
    return (s - lo) / (hi - lo)

signals = {}   # iso3-indexed series, all 0-1
coverage = {}  # name -> dict

# ---------------- CTDC ----------------
ctdc = pd.read_csv(f"{RAW}/ctdc/CTDC_global_synthetic_data_v2025.csv", low_memory=False)
mech_cols = ["meansDebtBondageEarnings","meansThreats","meansAbusePsyPhySex","meansFalsePromises",
             "meansDrugsAlcohol","meansDenyBasicNeeds","meansExcessiveWorkHours","meansWithholdDocs"]
for c in mech_cols + ["isForcedLabour","isSexualExploit","isOtherExploit"]:
    ctdc[c] = pd.to_numeric(ctdc[c], errors="coerce").fillna(0)

ctdc = ctdc[ctdc["CountryOfExploitation"].notna()].copy()
ctdc["iso3"] = ctdc["CountryOfExploitation"].astype(str).str.strip()
ctdc = ctdc[ctdc["iso3"].str.len()==3]

# Restrict to forced-labour cases for mechanism-composition (structural FORM of coercion)
fl = ctdc[ctdc["isForcedLabour"]==1].copy()
g = fl.groupby("iso3")
n_fl = g.size()
MIN_N = 30  # min FL cases for a stable composition share
keep = n_fl[n_fl>=MIN_N].index

mech_label = {
 "meansDebtBondageEarnings":"ctdc_share_debt_bondage",
 "meansWithholdDocs":"ctdc_share_doc_withholding",
 "meansThreats":"ctdc_share_threats",
 "meansExcessiveWorkHours":"ctdc_share_excessive_hours",
 "meansDenyBasicNeeds":"ctdc_share_deny_basic_needs",
}
for raw_c, name in mech_label.items():
    share = g[raw_c].mean()  # share of FL cases in country flagged with this mechanism
    share = share.loc[keep]
    signals[name] = minmax(share)
    coverage[name] = dict(n_countries=int(share.notna().sum()), note=f"share among FL cases, min {MIN_N} FL cases/country, 2002-2023")

# Count signal: total FL victims detected per country (raw count, log)
fl_count_all = ctdc[ctdc["isForcedLabour"]==1].groupby("iso3").size()
signals["ctdc_fl_victim_count_log"] = minmax(np.log1p(fl_count_all))
coverage["ctdc_fl_victim_count_log"] = dict(n_countries=int(fl_count_all.notna().sum()), note="log count of FL victim records, 2002-2023 (DETECTION count)")

# ---------------- GLOTIP ----------------
gl = pd.read_excel(f"{RAW}/unodc_glotip/unodc_glotip_data.xlsx", skiprows=2)
gl.columns = ["iso3","country","region","subregion","indicator","dimension","category","sex","age","year","unit","txtVALUE","source"]
def parse_val(v):
    s = str(v).strip()
    if s in ("nan","","NaN"): return np.nan
    if s.startswith("<"):  # censored "<5" -> midpoint ~2.5
        try: return float(s[1:])/2.0
        except: return np.nan
    try: return float(s)
    except: return np.nan
gl["val"] = gl["txtVALUE"].apply(parse_val)

# Detected FL victims, Total sex & Total age, summed over years (most recent decade)
flv = gl[(gl.indicator=="Detected trafficking victims") &
         (gl.dimension=="by form of exploitation") &
         (gl.category=="Forced labour") &
         (gl.sex=="Total") & (gl.age=="Total")]
flv_recent = flv[flv.year>=2014]
fl_det = flv_recent.groupby("iso3")["val"].sum()
signals["glotip_fl_detected_log"] = minmax(np.log1p(fl_det))
coverage["glotip_fl_detected_log"] = dict(n_countries=int(fl_det.notna().sum()), note="sum detected FL victims 2014-2023, log (DETECTION count)")

# FL share of all detected trafficking victims (composition: how much of detected trafficking is FL vs sex/other)
allforms = gl[(gl.indicator=="Detected trafficking victims") &
              (gl.dimension=="by form of exploitation") &
              (gl.sex=="Total") & (gl.age=="Total") & (gl.year>=2014)]
tot_by_c = allforms.groupby("iso3")["val"].sum()
fl_by_c = allforms[allforms.category=="Forced labour"].groupby("iso3")["val"].sum()
fl_share = (fl_by_c / tot_by_c).replace([np.inf,-np.inf], np.nan)
fl_share = fl_share[tot_by_c>=10]  # min 10 detected victims for stable share
signals["glotip_fl_share_of_detected"] = minmax(fl_share)
coverage["glotip_fl_share_of_detected"] = dict(n_countries=int(fl_share.notna().sum()), note="FL victims / all detected trafficking victims, 2014-2023, min 10 detected")

# ---------------- GSI ----------------
gsi = pd.read_excel(f"{RAW}/walk_free_gsi/2023-Global-Slavery-Index-Data.xlsx",
                    sheet_name="GSI 2023 summary data", header=2)
gsi = gsi.rename(columns={
    gsi.columns[0]:"country",
    gsi.columns[3]:"prevalence",
    gsi.columns[5]:"vuln_governance",
    gsi.columns[6]:"vuln_basic_needs",
    gsi.columns[7]:"vuln_inequality",
    gsi.columns[8]:"vuln_disenfranchised",
    gsi.columns[9]:"vuln_conflict",
    gsi.columns[10]:"vuln_total",
})
gsi = gsi[gsi["country"].notna()].copy()
gsi["iso3"] = to_iso3(gsi["country"])
gsi = gsi[gsi["iso3"].notna() & (gsi["iso3"]!="not found")]
gsi = gsi.set_index("iso3")

signals["gsi_prevalence"] = minmax(gsi["prevalence"])
coverage["gsi_prevalence"] = dict(n_countries=int(pd.to_numeric(gsi["prevalence"],errors="coerce").notna().sum()), note="GSI 2023 estimated prevalence per 1000 (ESTIMATE)")
signals["gsi_vuln_total"] = minmax(gsi["vuln_total"])
coverage["gsi_vuln_total"] = dict(n_countries=int(pd.to_numeric(gsi["vuln_total"],errors="coerce").notna().sum()), note="GSI total vulnerability score (composite incl governance)")
signals["gsi_vuln_conflict"] = minmax(gsi["vuln_conflict"])
coverage["gsi_vuln_conflict"] = dict(n_countries=int(pd.to_numeric(gsi["vuln_conflict"],errors="coerce").notna().sum()), note="GSI vulnerability: effects of conflict dimension")
signals["gsi_vuln_disenfranchised"] = minmax(gsi["vuln_disenfranchised"])
coverage["gsi_vuln_disenfranchised"] = dict(n_countries=int(pd.to_numeric(gsi["vuln_disenfranchised"],errors="coerce").notna().sum()), note="GSI vulnerability: disenfranchised groups dimension")
signals["gsi_vuln_governance"] = minmax(gsi["vuln_governance"])
coverage["gsi_vuln_governance"] = dict(n_countries=int(pd.to_numeric(gsi["vuln_governance"],errors="coerce").notna().sum()), note="GSI vulnerability: governance issues (EXPLICITLY governance - circularity probe)")

# ---------------- assemble ----------------
allcsv = pd.DataFrame(signals)
allcsv.index.name = "iso3"
allcsv = allcsv.sort_index()
allcsv.to_csv(f"{OUT}/fl_candidate_signals.csv")

# ---------------- correlate with WGI rule of law ----------------
wb = pd.read_csv(WB).set_index("iso3")
rol = pd.to_numeric(wb["wb_wgi_rule_of_law"], errors="coerce")  # RISK-ALIGNED: high = weak governance

rows = []
for name, s in signals.items():
    s = pd.to_numeric(s, errors="coerce")
    j = pd.concat([s.rename("sig"), rol.rename("rol")], axis=1).dropna()
    n = len(j)
    if n >= 8:
        sr, sp = spearmanr(j["sig"], j["rol"])
        pr, pp = pearsonr(j["sig"], j["rol"])
    else:
        sr=sp=pr=pp=np.nan
    rows.append(dict(signal=name, n_overlap_with_WGI=n,
                     n_countries=coverage[name]["n_countries"],
                     spearman_vs_weakRoL=round(sr,3) if not pd.isna(sr) else None,
                     spearman_p=round(sp,4) if not pd.isna(sp) else None,
                     pearson_vs_weakRoL=round(pr,3) if not pd.isna(pr) else None,
                     note=coverage[name]["note"]))
corr = pd.DataFrame(rows).sort_values("spearman_vs_weakRoL", na_position="last")
corr.to_csv(f"{OUT}/fl_signal_correlations.csv", index=False)

print("=== COVERAGE + CORRELATIONS (Spearman vs WEAK rule-of-law; +=tracks weak governance) ===")
print(corr.to_string(index=False))
print(f"\nTotal candidate signals: {len(signals)}")
print(f"Signals CSV rows (union of countries): {len(allcsv)}")

# cross-correlation among the mechanism-composition signals (are they distinct from each other?)
mech_sig_names = list(mech_label.values())
ms = allcsv[mech_sig_names]
print("\n=== Mechanism-composition signals: pairwise Spearman ===")
print(ms.corr(method="spearman").round(2).to_string())
