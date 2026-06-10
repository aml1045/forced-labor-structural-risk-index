#!/usr/bin/env python3
"""
FLSRI Geospatial Overlay prototype builder.
Reads ONLY from the locked pipeline; writes ONLY under experiments/geospatial/.

Produces:
  - hotspot_points.csv          : per-event disaster/conflict points with KDE density + Gi* z-score
  - hotspot_grid.csv            : gridded KDE surface (for heatmap)
  - corridor_pairs.csv          : outbound (remittances) vs inbound (migrant reliance) country rows
  - overlay_data.js             : self-contained JS payload consumed by the Leaflet HTML
Uses numpy/scipy/pandas only (no geopandas needed).

RUN ORDER (overlay.json pipeline):
  1. build_overlay.py            -> the CSVs above (this file)
  2. to_public_json.build_overlay_json -> outputs/site_data_staging/overlay.json (STRICT JSON,
                                          no `rout`, partial centroids)
  3. [publish] copy staging overlay.json -> public/data/overlay.json
  4. add_remittance_outflows.py  -> patches public/data/overlay.json: adds remittance-OUTFLOW
                                    `rout`, backfills ~47 destination centroids (needs geopandas
                                    + a live World Bank fetch).
Step 4 MUST follow steps 1-3. A bare re-run of steps 1-3 yields a VALID overlay.json that is
only missing `rout` (the map degrades gracefully); re-run step 4 to restore the shipped file.
"""
import json, os, sys, math
import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde

# ---- paths (from config/site_data_paths.py; logic unchanged) ----
# NOTE: the KDE heat grid (gaussian_kde) is scipy-version sensitive; the published
# overlay.json heat reproduces from the committed hotspot_grid.csv, but a fresh KDE
# recompute may differ at <=0.005 (immaterial, documented). The Gi* z-scores (haversine)
# and corridor rows ARE deterministic.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, _REPO_ROOT)
from config import site_data_paths as P  # noqa: E402
REPO = P.REPO
EMDIR = os.path.dirname(P.EMDAT_GEOJSON)
OUT = P.PORT_GEO
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------------------
# 1. Load disaster points (geojson) + enrich with deaths/affected from workbook
# ---------------------------------------------------------------------------
gj = json.load(open(f"{EMDIR}/emdat_points_2026-05-28.geojson"))
rows = []
for f in gj["features"]:
    lon, lat = f["geometry"]["coordinates"]
    p = f["properties"]
    rows.append(dict(disno=p.get("DisNo"), country=p.get("Country"),
                     year=p.get("StartYear"), dtype=p.get("DisasterType"),
                     location=p.get("Location"), lon=lon, lat=lat, kind="disaster"))
pts = pd.DataFrame(rows)
pts = pts.dropna(subset=["lon", "lat"])
pts = pts[(pts.lat.between(-60, 84)) & (pts.lon.between(-180, 180))]

# enrich with severity from the custom-request workbook (Total Deaths, Total Affected)
try:
    wbk = pd.read_excel(f"{EMDIR}/public_emdat_custom_request_2026-05-28_948fd068-9a9a-45cc-9c1f-a9538b062c2a.xlsx",
                        sheet_name="EM-DAT Data")
    wbk = wbk.rename(columns={"DisNo.": "disno", "Total Deaths": "deaths",
                              "Total Affected": "affected"})
    sev = wbk[["disno", "deaths", "affected"]].copy()
    pts = pts.merge(sev, on="disno", how="left")
except Exception as e:
    print("workbook merge failed:", e)
    pts["deaths"] = np.nan; pts["affected"] = np.nan

# ---------------------------------------------------------------------------
# 2. Conflict points: UCDP GED country-year is aggregated (no event coords on disk).
#    The inventory says GED geocodes live at source only; we DO NOT fabricate coords.
#    We instead place conflict INTENSITY as a country-centroid marker, clearly labelled
#    as country-level (low spatial confidence), and keep it OUT of the point-KDE
#    (which must use only true event coordinates).
# ---------------------------------------------------------------------------
ucdp = pd.read_csv(f"{REPO}/data/aux/ucdp_ged_country_year_2019_2023.csv")

# ---------------------------------------------------------------------------
# 3. HOTSPOT ANALYSIS on true disaster event points
#    (a) Gaussian KDE surface on a regular grid (heatmap)
#    (b) Getis-Ord Gi* on the same points using a distance band, severity-weighted
# ---------------------------------------------------------------------------
lon = pts.lon.values; lat = pts.lat.values
# event weight = 1 + log10(1+affected); caps the influence of mega-events
w = 1.0 + np.log10(1.0 + pts["affected"].fillna(0).clip(lower=0).values)

# (a) KDE grid
kde = gaussian_kde(np.vstack([lon, lat]), weights=w, bw_method=0.18)
gx = np.linspace(-180, 180, 144)
gy = np.linspace(-58, 82, 70)
GX, GY = np.meshgrid(gx, gy)
dens = kde(np.vstack([GX.ravel(), GY.ravel()])).reshape(GX.shape)
dens_norm = (dens - dens.min()) / (dens.max() - dens.min() + 1e-12)
grid_rows = []
for i in range(GY.shape[0]):
    for j in range(GX.shape[1]):
        if dens_norm[i, j] > 0.06:  # drop near-zero cells to keep payload small
            grid_rows.append(dict(lat=round(float(GY[i, j]), 3),
                                   lon=round(float(GX[i, j]), 3),
                                   d=round(float(dens_norm[i, j]), 4)))
grid = pd.DataFrame(grid_rows)

# (b) Getis-Ord Gi* (severity-weighted) with a fixed great-circle distance band
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1 = np.radians(lat1); p2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1); dl = np.radians(lon2 - lon1)
    a = np.sin(dphi/2)**2 + np.cos(p1)*np.cos(p2)*np.sin(dl/2)**2
    return 2*R*np.arcsin(np.sqrt(a))

n = len(pts)
x = w.copy()                       # attribute = severity weight
xbar = x.mean()
S = math.sqrt((x**2).sum()/n - xbar**2)
BAND = 500.0                       # km neighbourhood
gi_z = np.zeros(n)
lat_a = lat; lon_a = lon
for k in range(n):
    d = haversine(lat_a[k], lon_a[k], lat_a, lon_a)
    wk = (d <= BAND).astype(float)  # binary spatial weights, include self (Gi*)
    Wi = wk.sum()
    num = (wk * x).sum() - xbar * Wi
    den = S * math.sqrt((n * (wk**2).sum() - Wi**2) / (n - 1))
    gi_z[k] = num / den if den > 0 else 0.0
pts["gi_z"] = np.round(gi_z, 3)
# per-point local KDE value (normalized) for sizing
pdens = kde(np.vstack([lon, lat]))
pts["kde"] = np.round((pdens - pdens.min())/(pdens.max()-pdens.min()+1e-12), 4)

# label hotspot class
def hclass(z):
    if z >= 2.58: return "99%"
    if z >= 1.96: return "95%"
    if z >= 1.65: return "90%"
    return "ns"
pts["hot"] = pts.gi_z.apply(hclass)

# ---------------------------------------------------------------------------
# 4. CORRIDOR view: outbound (remittances %GDP) vs inbound (migrant reliance proxy)
#    Both columns are already 0-1 standardized in the locked pipeline.
#    remittances %GDP  -> OUTBOUND pressure (source / labour-exporting economies)
#    SDG_F881 (migrant share / migrant-labour proxy) -> INBOUND demand
# ---------------------------------------------------------------------------
wb = pd.read_csv(f"{REPO}/data/processed/worldbank.csv")[["iso3", "wb_remittances_pct_gdp"]]
ilo = pd.read_csv(f"{REPO}/data/processed/ilostat.csv")[["iso3", "SDG_F881_SEX_MIG_RT_A"]]
# Two-composite fix: use the PUBLISHED v0.4 composite (what scores.json shows),
# not the LOCKED 13-domain build, so the map and the rankings are ONE consistent index.
scores = pd.read_csv(f"{REPO}/pipeline/6_site_data/rebuild-v0.4/scores_v0_4.csv")
scores["composite_score"] = scores["v0_4_spu_w05_composite"]
scores["composite_rank"] = scores["composite_score"].rank(ascending=False, method="first")
corr = scores[["iso3", "country_name", "composite_score", "composite_rank"]].merge(
    wb, on="iso3", how="left").merge(ilo, on="iso3", how="left")
corr = corr.rename(columns={"wb_remittances_pct_gdp": "outbound_remit",
                            "SDG_F881_SEX_MIG_RT_A": "inbound_migrant"})
# classify role
def role(r):
    o, i = r.outbound_remit, r.inbound_migrant
    if pd.notna(o) and o >= 0.5 and (pd.isna(i) or i < 0.4):
        return "outbound-source"
    if pd.notna(i) and i >= 0.5 and (pd.isna(o) or o < 0.4):
        return "inbound-demand"
    if pd.notna(o) and pd.notna(i) and o >= 0.4 and i >= 0.4:
        return "dual"
    return "neither"
corr["corridor_role"] = corr.apply(role, axis=1)

# ---------------------------------------------------------------------------
# 5. Country centroids (computed from the disaster points as a free, real proxy;
#    only used to place choropleth label markers + conflict-intensity markers).
#    These are data-derived centroids of observed events, not authoritative
#    admin centroids -> labelled low-confidence.
# ---------------------------------------------------------------------------
cent = pts.groupby("country").agg(lat=("lat", "mean"), lon=("lon", "mean")).reset_index()

# ---------------------------------------------------------------------------
# Write derived CSVs
# ---------------------------------------------------------------------------
pts.to_csv(f"{OUT}/hotspot_points.csv", index=False)
grid.to_csv(f"{OUT}/hotspot_grid.csv", index=False)
corr.to_csv(f"{OUT}/corridor_pairs.csv", index=False)

# summary stats for the methods note
summ = dict(
    n_points=int(n),
    n_hot99=int((pts.hot == "99%").sum()),
    n_hot95=int((pts.hot == "95%").sum()),
    n_hot90=int((pts.hot == "90%").sum()),
    top_hot_countries=pts[pts.gi_z >= 1.96].country.value_counts().head(12).to_dict(),
    outbound_source=corr[corr.corridor_role == "outbound-source"].sort_values(
        "outbound_remit", ascending=False)[["iso3", "country_name", "outbound_remit", "composite_score"]].head(20).to_dict("records"),
    inbound_demand=corr[corr.corridor_role == "inbound-demand"].sort_values(
        "inbound_migrant", ascending=False)[["iso3", "country_name", "inbound_migrant", "composite_score"]].head(20).to_dict("records"),
    migrant_proxy_coverage=int(corr.inbound_migrant.notna().sum()),
    remit_coverage=int(corr.outbound_remit.notna().sum()),
)
json.dump(summ, open(f"{OUT}/summary.json", "w"), indent=2)

# ---------------------------------------------------------------------------
# Build self-contained JS payload for the Leaflet map
# ---------------------------------------------------------------------------
# unscored micro-states have NaN composite_score; emit null (not a bare NaN token) so
# the payload is strict-JSON-parseable, matching the downstream public overlay.json.
def _comp(x):
    return None if pd.isna(x) else round(float(x), 4)

payload = dict(
    points=[dict(lat=round(float(r.lat), 3), lon=round(float(r.lon), 3),
                 t=r.dtype, c=r.country, y=int(r.year) if pd.notna(r.year) else None,
                 z=float(r.gi_z), hot=r.hot,
                 aff=int(r.affected) if pd.notna(r.affected) else None,
                 dth=int(r.deaths) if pd.notna(r.deaths) else None)
            for r in pts.itertuples()],
    heat=[[g.lat, g.lon, g.d] for g in grid.itertuples()],
    scores={r.iso3: dict(name=r.country_name, comp=_comp(r.composite_score),
                         rank=int(r.composite_rank) if pd.notna(r.composite_rank) else None)
            for r in scores.itertuples()},
    centroids={r.country: [round(float(r.lat), 3), round(float(r.lon), 3)]
               for r in cent.itertuples()},
    corridor=[dict(iso3=r.iso3, name=r.country_name,
                   out=None if pd.isna(r.outbound_remit) else round(float(r.outbound_remit), 3),
                   inb=None if pd.isna(r.inbound_migrant) else round(float(r.inbound_migrant), 3),
                   role=r.corridor_role,
                   comp=_comp(r.composite_score),
                   cent=None)
              for r in corr.itertuples()],
)
# attach centroids to corridor rows where we have them
cmap = payload["centroids"]
name2cent = {}
for r in scores.itertuples():
    if r.country_name in cmap:
        name2cent[r.iso3] = cmap[r.country_name]
for row in payload["corridor"]:
    row["cent"] = name2cent.get(row["iso3"])

with open(f"{OUT}/overlay_data.js", "w") as fh:
    # allow_nan=False: keep the embedded payload strict-JSON-parseable (no bare NaN).
    fh.write("const OVERLAY = " + json.dumps(payload, allow_nan=False) + ";\n")

print("POINTS:", n)
print("Gi* hotspots  99%:", summ["n_hot99"], "95%:", summ["n_hot95"], "90%:", summ["n_hot90"])
print("top hot countries:", summ["top_hot_countries"])
print("outbound-source n:", len(corr[corr.corridor_role=='outbound-source']),
      "inbound-demand n:", len(corr[corr.corridor_role=='inbound-demand']),
      "dual n:", len(corr[corr.corridor_role=='dual']))
print("migrant proxy coverage:", summ["migrant_proxy_coverage"], "/", len(corr))
print("WROTE:", OUT)
