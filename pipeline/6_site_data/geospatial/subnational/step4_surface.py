#!/usr/bin/env python3
"""
Step 4 (analysis half) — build the admin-1 risk surface and quantify how much
sub-country structure a country composite misses.

1. Bring admin-1 IPUMS precarity signals together; null child-labour where the
   country's employment universe is teen-only (not comparable).
2. Spatial-join GDIS subnational disaster points (1960-2018) into GEOLEV1 polygons
   -> subnational shock count.
3. Compose a transparent admin-1 precarity index + combined risk surface.
4. Variance decomposition: eta^2 = between-country / total SS across admin-1 units
   (1-eta^2 = within-country share the national composite cannot see). Ties to the
   locked-pipeline validation eta^2=0.53 / Moran's I 0.445.
5. Identify worst internal corridors and mid-ranked countries hiding high-risk regions.

READ-ONLY on the locked pipeline. Geometry NOT loaded here except for the GDIS join.
"""
import os, sys
import numpy as np, pandas as pd, geopandas as gpd, json

# ---- paths (from config/site_data_paths.py; logic unchanged) ----
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
sys.path.insert(0, _REPO_ROOT)
from config import site_data_paths as P  # noqa: E402
EXP = P.EXP
SIG = P.IPUMS               # candidate_signals_{admin1,country}.csv live here
SUB = P.PORT_SUBNAT         # write surface/corridor/spread outputs into the ported tree
SHP = P.SHAPEFILE_GEOLEV1
GDIS = P.GDIS_CSV

PRECARITY = ["vulnerable_employment","redflag_sector","child_labour_comp"]

def eta_squared(df, value, group):
    """share of variance in `value` across units explained by `group` membership."""
    d = df[[value,group]].dropna()
    if d[group].nunique() < 2 or len(d) < 10: return np.nan, np.nan
    grand = d[value].mean()
    ss_tot = ((d[value]-grand)**2).sum()
    ss_between = d.groupby(group)[value].apply(lambda x: len(x)*(x.mean()-grand)**2).sum()
    eta2 = ss_between/ss_tot if ss_tot>0 else np.nan
    return eta2, 1-eta2

def main():
    import os, sys
    if not os.path.exists(f"{SIG}/candidate_signals_admin1.csv"):
        sys.exit("IPUMS-derived gated inputs absent (see docs/REPRODUCING.md); "
                 "cannot rebuild the subnational surface. The committed outputs "
                 "remain authoritative.")
    a = pd.read_csv(f"{SIG}/candidate_signals_admin1.csv", dtype={"GEOLEVEL1":str})
    cc = pd.read_csv(f"{SIG}/candidate_signals_country.csv")
    # bring country min_emp_age in to make child_labour comparable at admin-1
    age = cc[["COUNTRY","min_emp_universe_age"]].copy()
    a["cntry_num"] = a["GEOLEVEL1"].str[:3].astype(int)
    a = a.merge(age.rename(columns={"COUNTRY":"cntry_num"}), on="cntry_num", how="left")
    a["child_labour_comp"] = np.where(a["min_emp_universe_age"]<=12, a["child_labour"], np.nan)

    # precarity index = mean of available precarity signals (>=2 present)
    P = a[PRECARITY]
    a["n_precarity"] = P.notna().sum(axis=1)
    a["precarity_index"] = P.mean(axis=1, skipna=True).where(a["n_precarity"]>=2)

    # ---- GDIS spatial join: disaster points -> GEOLEV1 polygon ----
    g = pd.read_csv(GDIS)
    g = g.dropna(subset=["latitude","longitude"])
    gpts = gpd.GeoDataFrame(g, geometry=gpd.points_from_xy(g["longitude"], g["latitude"]), crs="EPSG:4326")
    print(f"GDIS points: {len(gpts):,}", flush=True)
    poly = gpd.read_file(SHP)
    poly = poly[poly["GEOLEVEL1"].notna()].copy()
    poly["GEOLEVEL1"] = poly["GEOLEVEL1"].astype(str).str.split(".").str[0].str.zfill(6)
    print(f"polygons with GEOLEVEL1: {len(poly):,}", flush=True)
    j = gpd.sjoin(gpts, poly[["GEOLEVEL1","geometry"]], how="inner", predicate="within")
    shock = j.groupby("GEOLEVEL1").agg(
        gdis_events=("id","count"),
        gdis_years=("year","nunique")).reset_index()
    shock.to_csv(f"{SUB}/gdis_shock_by_geolev1.csv", index=False)
    print(f"GDIS joined to {shock['GEOLEVEL1'].nunique():,} admin-1 units; {shock['gdis_events'].sum():,} events placed", flush=True)

    a = a.merge(shock, on="GEOLEVEL1", how="left")
    a["gdis_events"] = a["gdis_events"].fillna(0)
    # shock standardized 0-1 absolute-ish: log1p then divide by global log1p max (anchored)
    lg = np.log1p(a["gdis_events"])
    a["shock_index"] = (lg/np.log1p(a["gdis_events"].max())).round(4)

    # combined illustrative risk surface (documented weights; precarity-dominant)
    a["risk_surface"] = (0.70*a["precarity_index"] + 0.30*a["shock_index"]).round(4)
    a["precarity_index"]=a["precarity_index"].round(4)

    keep = ["iso3","GEOLEVEL1","CNTRY_NAME","ADMIN_NAME","n_records","low_sample",
            "vulnerable_employment","redflag_sector","child_labour_comp","n_precarity",
            "precarity_index","gdis_events","shock_index","risk_surface"]
    surf = a[keep].sort_values(["iso3","GEOLEVEL1"])
    surf.to_csv(f"{SUB}/admin1_risk_surface.csv", index=False)

    # ---- variance decomposition (eta^2) ----
    rel = a[(~a["low_sample"]) & a["precarity_index"].notna()].copy()
    eta_rows=[]
    for v in ["vulnerable_employment","redflag_sector","child_labour_comp","precarity_index","risk_surface"]:
        e2, within = eta_squared(rel, v, "iso3")
        eta_rows.append({"signal":v,"eta2_between_country":round(e2,4) if e2==e2 else None,
                         "within_country_share":round(within,4) if within==within else None,
                         "n_admin1":int(rel[v].notna().sum())})
    eta = pd.DataFrame(eta_rows)
    eta.to_csv(f"{SUB}/variance_decomposition.csv", index=False)

    # ---- corridors & hidden-risk countries ----
    # country-level precarity (pop-weighted-ish: use mean of admin precarity, and country signal file)
    csig = cc[["iso3","vulnerable_employment","redflag_sector","child_labour_comparable"]].copy()
    csig["country_precarity"] = csig[["vulnerable_employment","redflag_sector","child_labour_comparable"]].mean(axis=1,skipna=True)
    cprec = csig[["iso3","country_precarity"]]
    # within-country spread of admin-1 risk
    spread = rel.groupby("iso3").agg(
        n_units=("GEOLEVEL1","count"),
        risk_min=("risk_surface","min"),
        risk_max=("risk_surface","max"),
        risk_p90=("risk_surface", lambda x: x.quantile(0.90)),
        risk_mean=("risk_surface","mean")).reset_index()
    spread["risk_range"] = (spread["risk_max"]-spread["risk_min"]).round(4)
    spread = spread.merge(cprec, on="iso3", how="left")
    # mid-ranked countries: country_precarity in middle tercile but with high-risk admin-1 units
    q33,q66 = cprec["country_precarity"].quantile([0.33,0.66])
    spread["country_tier"] = np.where(spread["country_precarity"]>=q66,"high",
                              np.where(spread["country_precarity"]<=q33,"low","mid"))
    spread = spread.sort_values("risk_range",ascending=False)
    spread.round(4).to_csv(f"{SUB}/within_country_spread.csv", index=False)

    # worst corridors: highest-risk admin-1 units overall (reliable)
    worst = rel.nlargest(25,"risk_surface")[["iso3","CNTRY_NAME","ADMIN_NAME","precarity_index","gdis_events","risk_surface","n_records"]]
    worst.round(4).to_csv(f"{SUB}/worst_corridors.csv", index=False)
    # hidden risk: mid-tier countries whose top admin-1 risk is high
    hidden = spread[(spread["country_tier"]=="mid") & (spread["risk_p90"]>=rel["risk_surface"].quantile(0.75))]
    hidden = hidden.sort_values("risk_p90",ascending=False)
    hidden.round(4).to_csv(f"{SUB}/hidden_risk_countries.csv", index=False)

    summary={
        "admin1_units_total": int(len(a)),
        "admin1_reliable_with_precarity": int(len(rel)),
        "gdis_events_placed": int(a["gdis_events"].sum()),
        "eta2_precarity_index": eta.set_index("signal").loc["precarity_index","eta2_between_country"],
        "within_country_share_precarity": eta.set_index("signal").loc["precarity_index","within_country_share"],
        "eta2_risk_surface": eta.set_index("signal").loc["risk_surface","eta2_between_country"],
    }
    with open(f"{SUB}/_step4_summary.json","w") as f: json.dump(summary,f,indent=2,default=str)
    print(json.dumps(summary,indent=2,default=str))
    print("\n=== VARIANCE DECOMPOSITION ===");print(eta.to_string(index=False))
    print("\n=== WORST CORRIDORS (top 12) ===");print(worst.head(12).round(3).to_string(index=False))
    print("\n=== HIDDEN-RISK MID-TIER COUNTRIES ===");print(hidden[["iso3","n_units","country_precarity","risk_mean","risk_p90","risk_max","risk_range"]].head(12).round(3).to_string(index=False))

if __name__=="__main__":
    main()
