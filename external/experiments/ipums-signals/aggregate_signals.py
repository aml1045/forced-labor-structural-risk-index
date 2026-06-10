#!/usr/bin/env python3
"""
FLSRI exploratory — IPUMS-International microdata signal aggregation.

ONE streaming pass over ipumsi_00002.csv.gz. PERWT-weighted numerators/denominators
accumulated per COUNTRY and per GEOLEV1 (admin-1). Universe handling is explicit and
recorded so coverage can be reported honestly.

READ-ONLY on the locked pipeline. Writes only under experiments/ipums-signals/.
"""
import sys, time, gzip
import numpy as np
import pandas as pd

CSV = "<EXTERNAL-DATA>/0-orchestration/resources/shapefiles/ipumsi_00002.csv.gz"
OUT = "<EXTERNAL-DATA>/1-starting area/Research Deliverables/experiments/ipums-signals"

USECOLS = ["COUNTRY","GEOLEV1","PERWT","AGE","EMPSTAT","CLASSWK","CLASSWKD",
           "INDGEN","HRSWORK1","SCHOOL","BTHCERT","MIGRATE1","MIGRATE5",
           "NATIVITY","CITIZEN"]

# metric columns we accumulate (all PERWT-weighted sums)
METRICS = [
    "pop_w","n_rec",
    # S1 vulnerable employment
    "s1_den","s1_num",
    # S2 red-flag sector
    "s2_den","s2_num",
    # S3 child labour (5-17)
    "s3_den","s3_num","s3_num_oos",
    # S4 excessive hours
    "s4_den","s4_num48","s4_num60",
    # S5a internal migration (5yr + 1yr)
    "s5a5_den","s5a5_num","s5a1_den","s5a1_num",
    # S5b foreign-born / non-citizen
    "s5b_nat_den","s5b_nat_num","s5b_cit_den","s5b_cit_num",
    # S6 birth registration incompleteness (under-5 and under-18)
    "s6u5_den","s6u5_num","s6u18_den","s6u18_num",
]

INDGEN_VALID = {10,20,30,40,50,60,70,80,90,100,110,111,112,113,114,120,130}
INDGEN_REDFLAG = {10,20,30,50,120}  # agri, mining, manufacturing, construction, domestic
OWN_ACCOUNT = {120,121,122,123,124,125,126}

def process_chunk(df):
    w = df["PERWT"].to_numpy(dtype="float64")
    w = np.where(np.isfinite(w), w, 0.0)
    AGE = df["AGE"].to_numpy(dtype="float64")
    EMP = df["EMPSTAT"].to_numpy(dtype="float64")
    CW  = df["CLASSWK"].to_numpy(dtype="float64")
    CWD = df["CLASSWKD"].to_numpy(dtype="float64")
    IND = df["INDGEN"].to_numpy(dtype="float64")
    HRS = df["HRSWORK1"].to_numpy(dtype="float64")
    SCH = df["SCHOOL"].to_numpy(dtype="float64")
    BC  = df["BTHCERT"].to_numpy(dtype="float64")
    M1  = df["MIGRATE1"].to_numpy(dtype="float64")
    M5  = df["MIGRATE5"].to_numpy(dtype="float64")
    NAT = df["NATIVITY"].to_numpy(dtype="float64")
    CIT = df["CITIZEN"].to_numpy(dtype="float64")

    out = {}
    out["pop_w"] = w
    out["n_rec"] = np.ones_like(w)

    # S1 vulnerable employment: denom = employed w/ known class (CLASSWK 1-4); num = unpaid OR own-account
    s1_den = np.isin(CW, [1,2,3,4])
    s1_num = (CW == 3) | np.isin(CWD, list(OWN_ACCOUNT))
    out["s1_den"] = w*s1_den
    out["s1_num"] = w*(s1_num & s1_den)

    # S2 red-flag sector: denom = known industry; num = red-flag industries
    s2_den = np.isin(IND, list(INDGEN_VALID))
    s2_num = np.isin(IND, list(INDGEN_REDFLAG))
    out["s2_den"] = w*s2_den
    out["s2_num"] = w*(s2_num & s2_den)

    # S3 child labour: children 5-17 with employment measured; num = employed; oos = employed & not in school
    child = (AGE >= 5) & (AGE <= 17)
    s3_den = child & np.isin(EMP, [1,2,3])      # employment status in universe
    s3_num = child & (EMP == 1)
    s3_oos = s3_num & np.isin(SCH, [2,3,4])      # working AND not attending school
    out["s3_den"] = w*s3_den
    out["s3_num"] = w*s3_num
    out["s3_num_oos"] = w*s3_oos

    # S4 excessive hours: denom = valid hours (0-140); num = >48, >60
    hvalid = (HRS >= 0) & (HRS <= 140)
    out["s4_den"]   = w*hvalid
    out["s4_num48"] = w*(hvalid & (HRS > 48))
    out["s4_num60"] = w*(hvalid & (HRS > 60))

    # S5a internal migration: cross-major-admin move (code 20). 5yr primary, 1yr fallback.
    m5valid = np.isin(M5, [10,11,12,20,30])
    out["s5a5_den"] = w*m5valid
    out["s5a5_num"] = w*(m5valid & (M5 == 20))
    m1valid = np.isin(M1, [10,11,12,20,30])
    out["s5a1_den"] = w*m1valid
    out["s5a1_num"] = w*(m1valid & (M1 == 20))

    # S5b foreign-born (NATIVITY) and non-citizen (CITIZEN 4 not-citizen, 5 stateless)
    natvalid = np.isin(NAT, [1,2])
    out["s5b_nat_den"] = w*natvalid
    out["s5b_nat_num"] = w*(natvalid & (NAT == 2))
    citvalid = np.isin(CIT, [1,2,3,4,5])
    out["s5b_cit_den"] = w*citvalid
    out["s5b_cit_num"] = w*(citvalid & np.isin(CIT, [4,5]))

    # S6 birth registration incompleteness: BTHCERT==2 (no), among children w/ valid response
    u5  = AGE < 5
    u18 = AGE < 18
    bcvalid = np.isin(BC, [1,2])
    out["s6u5_den"]  = w*(u5 & bcvalid)
    out["s6u5_num"]  = w*(u5 & (BC == 2))
    out["s6u18_den"] = w*(u18 & bcvalid)
    out["s6u18_num"] = w*(u18 & (BC == 2))

    cdf = pd.DataFrame(out)
    cdf["COUNTRY"] = df["COUNTRY"].to_numpy()
    cdf["GEOLEV1"] = df["GEOLEV1"].to_numpy()

    g_country = cdf.groupby("COUNTRY")[METRICS].sum()
    # admin-1: valid GEOLEV1 only (not NaN, not 0)
    gl = cdf["GEOLEV1"]
    mask = gl.notna() & (gl != 0)
    g_admin = cdf.loc[mask].groupby("GEOLEV1")[METRICS].sum()

    # min employment-universe age per country (for child-labour caveat)
    emp_in = cdf[["COUNTRY"]].copy()
    emp_in["age_emp"] = np.where(np.isin(EMP,[1,2,3]) & (w>0), AGE, np.nan)
    minage = emp_in.groupby("COUNTRY")["age_emp"].min()
    return g_country, g_admin, minage

def main():
    t0 = time.time()
    acc_c = None; acc_a = None; minages = []
    total_rows = 0
    dtypes = {c:"float64" for c in USECOLS}
    reader = pd.read_csv(CSV, usecols=USECOLS, dtype=dtypes,
                         chunksize=3_000_000, compression="gzip")
    for i, chunk in enumerate(reader):
        total_rows += len(chunk)
        gc, ga, ma = process_chunk(chunk)
        acc_c = gc if acc_c is None else acc_c.add(gc, fill_value=0)
        acc_a = ga if acc_a is None else acc_a.add(ga, fill_value=0)
        minages.append(ma)
        print(f"  chunk {i}: {len(chunk):,} rows | cum {total_rows:,} | {time.time()-t0:.0f}s", flush=True)

    minage = pd.concat(minages, axis=1).min(axis=1)
    acc_c.to_csv(f"{OUT}/_raw_country_sums.csv")
    acc_a.to_csv(f"{OUT}/_raw_admin1_sums.csv")
    minage.to_frame("min_emp_universe_age").to_csv(f"{OUT}/_country_min_emp_age.csv")
    with open(f"{OUT}/_rowcount.txt","w") as f:
        f.write(f"data_rows={total_rows}\nelapsed_s={time.time()-t0:.0f}\n")
    print(f"DONE: {total_rows:,} rows in {time.time()-t0:.0f}s", flush=True)
    print(f"  countries: {len(acc_c)}  admin1 units: {len(acc_a)}", flush=True)

if __name__ == "__main__":
    main()
