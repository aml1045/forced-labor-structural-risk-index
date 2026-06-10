#!/usr/bin/env python3
"""Orchestrator: regenerate every public/data site file into outputs/site_data_staging/.

Runs the ported prior-effort generators (pipeline/6_site_data/**) in dependency order,
then the reverse-engineered CSV->JSON glue (pipeline/6_site_data/to_public_json.py).
Writes ONLY to outputs/site_data_staging/ — never public/. Verify with site_data_verify.py.

Chain (dependency order):
  1. rebuild-v0.4/build_v0_4.py            -> scores_v0_4.csv
  2. rebuild-v0.4/export_domains.py        -> domains.json                (STAGING)
  3. to_public_json.build_scores_json      -> scores.json                 (STAGING)
  4. geospatial/build_overlay.py           -> hotspot_points/grid + corridor_pairs csv
  5. to_public_json.build_overlay_json     -> overlay.json                (STAGING)
  6. subnational/step4_surface.py          -> admin1_risk_surface + worst/hidden/spread csv
  7. geospatial/divergence_analysis.py     -> divergence_headline.csv
  8. to_public_json.build_divergence_json  -> divergence.json             (STAGING)
  9. to_public_json.build_subnational_json -> subnational.json            (STAGING)
 10. subnational/build_geojson.py          -> _full_admin1.geojson
 11. mapshaper simplify + topojson         -> admin1_risk_simplified.geojson, admin1_risk.topojson (STAGING)
 12. spatial-analysis/compute_lisa.py      -> lisa_admin1.csv + _lisa_*_geo.geojson + lisa_summary.json
 13. to_public_json.build_lisa_json        -> lisa.json                   (STAGING)
 14. to_public_json.build_lisa_admin1_json -> lisa_admin1.json            (STAGING)
 15. mapshaper topojson (lisa admin1)      -> lisa_admin1.topojson        (STAGING)

Geometry note: steps 11 + 15 use `npx mapshaper`. Its simplification is mapshaper-version
dependent; the resulting topojsons match the baseline on object name / feature count /
feature-id set / property schema but NOT byte-for-byte coordinates (immaterial). The 5
admin-1 LISA cluster flips downstream of that geometry are the only knock-on effect.

Overlay post-step (NOT run here): step 5 emits a STRICT-JSON overlay.json that is missing
the remittance-OUTFLOW `rout` field and ~47 destination centroids. After staging is
published to public/data/, run geospatial/add_remittance_outflows.py to add them. It is
kept out of this orchestrator because it needs geopandas + a live World Bank fetch, whereas
every step here is offline and deterministic.
"""
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from config import site_data_paths as P  # noqa: E402

SD = P.SITE_DATA
PY = sys.executable


def run_py(script, cwd=None):
    print(f"\n>>> python {os.path.relpath(script, HERE)}")
    subprocess.run([PY, script], cwd=cwd or os.path.dirname(script), check=True)


def run_mapshaper(args, cwd):
    print(f"\n>>> npx mapshaper {' '.join(args)}")
    subprocess.run(["npx", "mapshaper", *args], cwd=cwd, check=True)


def preflight():
    """Fail in the first second, not at step 11."""
    scores_csv = os.path.join(HERE, "outputs", "scores.csv")
    if not os.path.exists(scores_csv):
        sys.exit("PREFLIGHT FAIL: outputs/scores.csv missing (it is gitignored and "
                 "regenerated per run) — run `python run.py` or `python build_all.py score` first.")
    try:
        ms = subprocess.run(["npx", "--no-install", "mapshaper", "-v"],
                            capture_output=True, text=True, cwd=HERE)
        ms_ok = ms.returncode == 0
    except FileNotFoundError:
        ms_ok = False
    if not ms_ok:
        sys.exit("PREFLIGHT FAIL: mapshaper not available (steps 11/15 need it). "
                 "Run `npm ci` (uses the pinned package.json) or `npm i -g mapshaper`; "
                 "needs Node/npx on PATH.")
    print(f"preflight ok: outputs/scores.csv present; mapshaper {ms.stdout.strip()}")


def main():
    preflight()
    P.ensure_staging()
    sys.path.insert(0, SD)
    import to_public_json as J  # noqa: E402

    # 1-2: scores csv + domains.json
    run_py(os.path.join(P.PORT_V04, "build_v0_4.py"))
    run_py(os.path.join(P.PORT_V04, "export_domains.py"))
    # 3: scores.json
    J.build_scores_json()

    # 4-5: overlay
    run_py(os.path.join(P.PORT_GEO, "build_overlay.py"))
    J.build_overlay_json()

    # 6: admin-1 risk surface (+ worst/hidden/spread)
    run_py(os.path.join(P.PORT_SUBNAT, "step4_surface.py"))
    # 7-9: divergence + subnational
    run_py(os.path.join(P.PORT_GEO, "divergence_analysis.py"))
    J.build_divergence_json()
    J.build_subnational_json()

    # 10-11: admin-1 geojson -> simplify -> topojson (-> STAGING)
    run_py(os.path.join(P.PORT_SUBNAT, "build_geojson.py"))
    run_mapshaper(
        ["_full_admin1.geojson", "-simplify", "3%",
         "-filter", "$.width > 0 || $.height > 0",
         "-o", "precision=0.001", "admin1_risk_simplified.geojson"],
        cwd=P.PORT_SUBNAT)
    run_mapshaper(
        ["admin1_risk_simplified.geojson", "-rename-layers", "admin1",
         "-o", "format=topojson", "quantization=10000",
         os.path.join(P.STAGING, "admin1_risk.topojson")],
        cwd=P.PORT_SUBNAT)

    # 12-14: LISA + lisa json files
    run_py(os.path.join(P.PORT_SPATIAL, "compute_lisa.py"))
    J.build_lisa_json()
    J.build_lisa_admin1_json()
    # 15: lisa admin-1 topojson (-> STAGING)
    run_mapshaper(
        ["_lisa_admin1_geo.geojson",
         "-filter-fields", "iso3,cntry,name,cluster,value",
         "-o", "format=topojson", "quantization=10000",
         os.path.join(P.STAGING, "lisa_admin1.topojson")],
        cwd=P.PORT_SPATIAL)

    print("\n=== DONE. Staging dir:", P.STAGING)
    print("Run:  python site_data_verify.py")


if __name__ == "__main__":
    main()
