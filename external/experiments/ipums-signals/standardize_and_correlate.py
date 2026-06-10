#!/usr/bin/env python3
"""
Step 3 continued — turn raw weighted sums into standardized 0-1 signals at COUNTRY
(ISO3) and admin-1 (GEOLEV1) level, record coverage, and correlate each COUNTRY signal
with WGI rule-of-law (risk-aligned) to classify governance-orthogonal vs governance-laden.

Standardization: every signal is a population-weighted PROPORTION (share), so it is
already on [0,1] with meaningful ABSOLUTE anchors (0 = none, 1 = all). The share itself
IS the absolute-anchored 0-1 score. No sample min-max is applied (that would destroy
cross-country comparability). Migration-intensity shares are small in absolute terms;
we keep them absolute-anchored and report the distribution rather than rescaling.
"""
import numpy as np, pandas as pd, pycountry, json

OUT = "<EXTERNAL-DATA>/1-starting area/Research Deliverables/experiments/ipums-signals"
WGI = "<EXTERNAL-DATA>/4-final deliverable/working/github repo/data/processed/worldbank.csv"
XWALK = "<EXTERNAL-DATA>/1-starting area/Research Deliverables/experiments/subnational-geometry/ipums_geolev1_admin1_crosswalk.csv"

# minimum unweighted records to treat a group as reliable
MIN_REC_COUNTRY = 1000
MIN_REC_ADMIN = 200
# minimum unweighted records *contributing to a signal's denominator* — we don't have
# per-signal unweighted counts, so we guard on group n_rec + den>0. Documented as a limit.

def num2iso3(code):
    s = str(int(code)).zfill(3)
    try:
        c = pycountry.countries.get(numeric=s)
        if c: return c.alpha_3
    except Exception:
        pass
    return None

# Signals: (name, num_col, den_col, governance_expectation)
SIGNALS = [
    ("vulnerable_employment", "s1_num", "s1_den"),
    ("redflag_sector",        "s2_num", "s2_den"),
    ("child_labour",          "s3_num", "s3_den"),
    ("child_labour_oos",      "s3_num_oos", "s3_den"),
    ("excess_hours_48",       "s4_num48", "s4_den"),
    ("excess_hours_60",       "s4_num60", "s4_den"),
    ("internal_migration_5yr","s5a5_num","s5a5_den"),
    ("internal_migration_1yr","s5a1_num","s5a1_den"),
    ("foreign_born",          "s5b_nat_num","s5b_nat_den"),
    ("noncitizen",            "s5b_cit_num","s5b_cit_den"),
    ("birthreg_incomplete_u5","s6u5_num","s6u5_den"),
    ("birthreg_incomplete_u18","s6u18_num","s6u18_den"),
]

def build(level):
    raw = pd.read_csv(f"{OUT}/_raw_{'country' if level=='country' else 'admin1'}_sums.csv")
    key = "COUNTRY" if level=="country" else "GEOLEV1"
    df = pd.DataFrame()
    df[key] = raw[key].astype(int)
    df["n_records"] = raw["n_rec"].astype("int64")
    df["pop_weighted"] = raw["pop_w"].round(1)
    for name,num,den in SIGNALS:
        d = raw[den].to_numpy()
        n = raw[num].to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            share = np.where(d>0, n/d, np.nan)
        df[name] = np.round(share,5)
        df[name+"__den_w"] = raw[den].round(1)   # weighted denominator (coverage mass)
    return df, raw

def main():
    # ---------- COUNTRY ----------
    c, rawc = build("country")
    c["iso3"] = c["COUNTRY"].apply(num2iso3)
    unmapped = c[c["iso3"].isna()]["COUNTRY"].tolist()
    # child-labour universe-age caveat
    minage = pd.read_csv(f"{OUT}/_country_min_emp_age.csv")
    minage["COUNTRY"]=minage["COUNTRY"].astype(int)
    c = c.merge(minage, on="COUNTRY", how="left")
    c["child_labour_undercount_flag"] = c["min_emp_universe_age"] > 7
    c["low_sample"] = c["n_records"] < MIN_REC_COUNTRY
    # Comparable child-labour: only where the employment universe actually reaches
    # younger children (min age <= 12). Where employment is measured only for teens
    # (e.g. NLD/GBR min age 17), the 5-17 rate collapses to teen-LFP and is NOT a
    # comparable child-labour estimate — set to NaN for the comparable column.
    c["child_labour_comparable"] = np.where(c["min_emp_universe_age"] <= 12,
                                            c["child_labour"], np.nan)
    c["child_labour_oos_comparable"] = np.where(c["min_emp_universe_age"] <= 12,
                                            c["child_labour_oos"], np.nan)
    # reorder
    front = ["iso3","COUNTRY","n_records","pop_weighted","min_emp_universe_age",
             "child_labour_undercount_flag","low_sample",
             "child_labour_comparable","child_labour_oos_comparable"]
    sigcols = [s[0] for s in SIGNALS]
    dencols = [s[0]+"__den_w" for s in SIGNALS]
    c = c[front+sigcols+dencols].sort_values("iso3")
    c.to_csv(f"{OUT}/candidate_signals_country.csv", index=False)

    # ---------- ADMIN-1 ----------
    a, rawa = build("admin1")
    a["GEOLEVEL1"] = a["GEOLEV1"].apply(lambda v: str(int(v)).zfill(6))
    a["cntry_num"] = a["GEOLEVEL1"].str[:3].astype(int)
    a["iso3"] = a["cntry_num"].apply(num2iso3)
    xw = pd.read_csv(XWALK, dtype={"GEOLEVEL1":str})
    a = a.merge(xw[["GEOLEVEL1","ADMIN_NAME","CNTRY_NAME"]], on="GEOLEVEL1", how="left")
    a["low_sample"] = a["n_records"] < MIN_REC_ADMIN
    front = ["iso3","GEOLEVEL1","GEOLEV1","CNTRY_NAME","ADMIN_NAME","n_records","pop_weighted","low_sample"]
    a = a[front+sigcols+dencols].sort_values(["iso3","GEOLEVEL1"])
    a.to_csv(f"{OUT}/candidate_signals_admin1.csv", index=False)

    # ---------- COVERAGE ----------
    cov_rows=[]
    for name,num,den in SIGNALS:
        cc = c[c[name].notna() & (~c["low_sample"])]
        aa = a[a[name].notna() & (~a["low_sample"])]
        cov_rows.append({
            "signal":name,
            "countries_with_signal": int(c[name].notna().sum()),
            "countries_reliable": int(len(cc)),
            "admin1_with_signal": int(a[name].notna().sum()),
            "admin1_reliable": int(len(aa)),
            "country_median": round(float(c[name].median(skipna=True)),4),
            "country_min": round(float(c[name].min(skipna=True)),4) if c[name].notna().any() else None,
            "country_max": round(float(c[name].max(skipna=True)),4) if c[name].notna().any() else None,
        })
    cov = pd.DataFrame(cov_rows)
    cov.to_csv(f"{OUT}/coverage_by_signal.csv", index=False)

    # ---------- GOVERNANCE ORTHOGONALITY ----------
    wgi = pd.read_csv(WGI)[["iso3","wb_wgi_rule_of_law"]]
    m = c.merge(wgi, on="iso3", how="left")
    corr_rows=[]
    corr_names = [s[0] for s in SIGNALS] + ["child_labour_comparable","child_labour_oos_comparable"]
    for name in corr_names:
        sub = m[m[name].notna() & m["wb_wgi_rule_of_law"].notna() & (~m["low_sample"])]
        if len(sub) >= 8:
            r = float(np.corrcoef(sub[name], sub["wb_wgi_rule_of_law"])[0,1])
            # spearman
            rs = float(sub[[name,"wb_wgi_rule_of_law"]].corr(method="spearman").iloc[0,1])
        else:
            r=rs=float("nan")
        verdict = ("orthogonal" if abs(r)<0.30 else
                   "moderately-laden" if abs(r)<0.55 else "governance-laden")
        corr_rows.append({"signal":name,"n_countries":int(len(sub)),
                          "pearson_r_vs_RoL":round(r,3),"spearman_vs_RoL":round(rs,3),
                          "abs_r":round(abs(r),3),"verdict":verdict})
    corr = pd.DataFrame(corr_rows).sort_values("abs_r")
    corr.to_csv(f"{OUT}/governance_orthogonality.csv", index=False)

    summary = {
        "data_rows": 117163186,
        "countries": int(len(c)),
        "countries_mapped_iso3": int(c["iso3"].notna().sum()),
        "unmapped_country_codes": unmapped,
        "admin1_units": int(len(a)),
        "admin1_matched_crosswalk": int(a["ADMIN_NAME"].notna().sum()),
    }
    with open(f"{OUT}/_build_summary.json","w") as f: json.dump(summary,f,indent=2)
    print(json.dumps(summary,indent=2))
    print("\n=== COVERAGE ==="); print(cov.to_string(index=False))
    print("\n=== GOVERNANCE ORTHOGONALITY (sorted, low |r| = orthogonal) ==="); print(corr.to_string(index=False))

if __name__=="__main__":
    main()
