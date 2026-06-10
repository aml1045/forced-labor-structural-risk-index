#!/usr/bin/env python3
"""Build a merged admin-1 GeoJSON (risk surface attrs) for the IPUMS-covered units,
then it is simplified by mapshaper downstream. Raw .shp is 240MB; we filter + drop
unneeded attrs first so the simplifier has less to chew."""
import os, sys
import geopandas as gpd, pandas as pd, numpy as np

# ---- paths (from config/site_data_paths.py; logic unchanged) ----
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
sys.path.insert(0, _REPO_ROOT)
from config import site_data_paths as P  # noqa: E402
SUB = P.PORT_SUBNAT
SHP = P.SHAPEFILE_GEOLEV1

surf=pd.read_csv(f"{SUB}/admin1_risk_surface.csv", dtype={"GEOLEVEL1":str})
poly=gpd.read_file(SHP)
poly=poly[poly["GEOLEVEL1"].notna()].copy()
poly["GEOLEVEL1"]=poly["GEOLEVEL1"].astype(str).str.split(".").str[0].str.zfill(6)
poly=poly[["GEOLEVEL1","geometry"]]
m=poly.merge(surf, on="GEOLEVEL1", how="inner")
cols={"GEOLEVEL1":"id","iso3":"iso3","CNTRY_NAME":"cntry","ADMIN_NAME":"name",
      "precarity_index":"prec","vulnerable_employment":"vuln","redflag_sector":"sect",
      "child_labour_comp":"child","gdis_events":"gdis","shock_index":"shock",
      "risk_surface":"risk","n_records":"nrec","low_sample":"lowsamp"}
m=m[list(cols.keys())+["geometry"]].rename(columns=cols)
for c in ["prec","vuln","sect","child","shock","risk"]:
    m[c]=m[c].round(3)
m["gdis"]=m["gdis"].astype(int)
out=f"{SUB}/_full_admin1.geojson"
m.to_file(out, driver="GeoJSON")
print(f"wrote {out}: {len(m)} features")
print(m[["id","iso3","name","risk"]].head().to_string(index=False))
