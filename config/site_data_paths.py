#!/usr/bin/env python3
"""Centralized paths for the site-data reproduction chain (pipeline/6_site_data).

Every site-data generator imports its paths from here. Repo-local inputs and
outputs are rooted at REPO (computed relative to this file). Large external
inputs (boundary shapefiles, EM-DAT disaster geojson, IPUMS-derived signal CSVs,
and prior-effort experiment scores) are NOT bundled in this repo; set the
FLSRI_EXTERNAL_DATA environment variable to a local directory that holds them to
rebuild the site data.
"""
import os

# ---- the live repo (this file is repo/config/site_data_paths.py) ----
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# ---- consolidated external inputs (in-repo at repo/external).
#      Large/licence-restricted files (IPUMS raw, GeoLev1 shapefile, EM-DAT geojson) are
#      gitignored — present locally, not committed. Override the root via FLSRI_EXTERNAL_DATA. ----
ULTRA = os.environ.get(
    "FLSRI_EXTERNAL_DATA",
    os.path.join(REPO, "external"))
EXP = os.path.join(
    ULTRA, "experiments")

# ---- experiment subfolders (signal CSVs / reference scores) ----
V01   = os.path.join(EXP, "rebuild-v0.1")
V02   = os.path.join(EXP, "rebuild-v0.2")
V03   = os.path.join(EXP, "rebuild-v0.3")
V04   = os.path.join(EXP, "rebuild-v0.4")
TEMP  = os.path.join(EXP, "rebuild-temporal")
KAF   = os.path.join(EXP, "source-tiedstatus")
IPUMS = os.path.join(EXP, "ipums-signals")
FLSIG = os.path.join(EXP, "fl-signals")
GEO   = os.path.join(EXP, "geospatial")
SPATIAL = os.path.join(GEO, "spatial-analysis")
SUBNAT  = os.path.join(GEO, "subnational")

# ---- repo data / outputs ----
PROC = os.path.join(REPO, "data", "processed")
AUX  = os.path.join(REPO, "data", "aux")
REPO_OUTPUTS = os.path.join(REPO, "outputs")
SCORES_CSV = os.path.join(REPO_OUTPUTS, "scores.csv")

# ---- the staging output dir (NEVER public/) ----
STAGING = os.path.join(REPO_OUTPUTS, "site_data_staging")

# ---- ported-generator working dirs (under pipeline/6_site_data) ----
SITE_DATA = os.path.join(REPO, "pipeline", "6_site_data")
PORT_V04     = os.path.join(SITE_DATA, "rebuild-v0.4")
PORT_GEO     = os.path.join(SITE_DATA, "geospatial")
PORT_SPATIAL = os.path.join(PORT_GEO, "spatial-analysis")
PORT_SUBNAT  = os.path.join(PORT_GEO, "subnational")

# ---- large external geodata inputs (gitignored; under repo/external/geo) ----
SHAPEFILE_GEOLEV1 = os.path.join(ULTRA, "geo", "world_geolev1_2025.shp")
EMDAT_GEOJSON = os.path.join(ULTRA, "geo", "emdat_points_2026-05-28.geojson")

# geospatial intermediate-input dirs (under the ported tree)
GDIS_CSV = os.path.join(PORT_SUBNAT, "gdis_disasterlocations.csv")
# IPUMS signal CSVs (canonical prior-effort inputs, read-only)
IPUMS_ADMIN1_CSV  = os.path.join(IPUMS, "candidate_signals_admin1.csv")
IPUMS_COUNTRY_CSV = os.path.join(IPUMS, "candidate_signals_country.csv")
# country polygon geojson for the country-level LISA layer (present in repo)
NE_ADMIN0_GEOJSON = os.path.join(REPO, "data", "geometry", "ne_admin0.geojson")

# repo-local processed inputs used by geospatial generators
WORLDBANK_CSV = os.path.join(PROC, "worldbank.csv")
ILOSTAT_CSV   = os.path.join(PROC, "ilostat.csv")
UCDP_CSV      = os.path.join(AUX, "ucdp_ged_country_year_2019_2023.csv")


def ensure_staging():
    os.makedirs(STAGING, exist_ok=True)
    return STAGING
