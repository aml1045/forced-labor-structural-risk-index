#!/usr/bin/env python3
"""Ad-hoc CSV/CSV+geo -> public/data JSON conversions, reconstructed from the
frozen published baseline schema (live dashboards/_published-baseline/data).

These glue steps were done by hand in the prior effort and never scripted. Each
function here was REVERSE-ENGINEERED from the baseline file's exact bytes:
  * serialization recipe: json.dump(..., indent=0, separators=(",",":"),
    ensure_ascii=True)  -- verified byte-identical for scores.json & domains.json.
  * field provenance documented inline per file.

Outputs go to outputs/site_data_staging/ (never public/).
"""
import datetime
import os
import sys
import json

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, _HERE)
from config import site_data_paths as P  # noqa: E402
import uncertainty as U  # noqa: E402  (sibling module — the MC band model)

# baseline meta strings carried verbatim (the published scores.json meta block)
_COVERAGE_NOTE = (
    "Per-country data-coverage percentages are not published as a single clean "
    "number: the pipeline records per-DOMAIN data-quality flags (low-confidence / "
    "not-scored), not a per-country share of indicators present. Profiles and "
    "rankings therefore surface the real domain-level flags from the domain-level "
    "data-quality flags, not a derived percentage. Composites already exclude "
    "not-scored domains via drop-and-re-average; missing inputs are never treated "
    "as zero."
)


def _dump(obj, path):
    """scores.json / domains.json recipe (verified byte-identical)."""
    with open(path, "w") as f:
        json.dump(obj, f, indent=0, separators=(",", ":"), ensure_ascii=True)


# Per-file serialization recipes, each reverse-engineered to byte-identity vs baseline:
def _dump_divergence(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, indent=0, ensure_ascii=False)


def _dump_subnational(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False)


def _dump_lisa(obj, path):              # lisa.json + overlay.json
    # allow_nan=False: emit STRICT JSON. Browsers' JSON.parse rejects the bare
    # NaN/Infinity tokens Python would otherwise write, so any stray non-finite
    # value must fail the build loudly here rather than ship an unparseable file.
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _dump_lisa_admin1(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=True)


def _r4(x):
    return None if pd.isna(x) else round(float(x), 4)


def _split_flags(x):
    if isinstance(x, str) and x.strip() and x != "nan":
        return sorted(s for s in x.split(",") if s)
    return []


PRODUCT1_PHASES = ("Recruitment", "Exploitation")  # Monetization is Product-2 only


def build_scores_json(scores_csv=None, domains_json=None, out_path=None,
                      locked_scores_csv=None):
    """scores.json = per-country composite/R/E (SPU-only w=0.5 columns from
    scores_v0_4.csv) + domain-flag rollups (low_conf / not_scored_domains).

    design decision: the displayed build is authoritative. The
    originally-published scores.json imported its flag lists from the LOCKED
    build (outputs/scores.csv), which made them STALE and internally
    contradictory -- e.g. Palau's published E=0.439 is exactly
    mean(economic-structure-demand 0.0893, state-production 0.7887), proving SPU
    was scored, yet the stale list claimed SPU not_scored. We therefore derive
    the flag fields from the displayed build (domains.json), restricted to the
    11 Product-1 domains (Recruitment + Exploitation; Monetization is Product-2
    only and never appears in the Product-1 flag lists). This is a deliberate,
    documented design correction to the published baseline; it changes exactly two
    countries (PLW, AND -- removes the stale state-production-of-unfreedom from
    not_scored) and leaves low_conf and every other country unchanged.

    Country ORDER = scores_v0_4.csv row order (sorted by v0_4_composite desc).
    rank = round(composite,4).rank(ascending=False, method="first") among scored.
    n_domains is the fixed published constant 11 (Product-1 domains).
    """
    scores_csv = scores_csv or os.path.join(P.PORT_V04, "scores_v0_4.csv")
    domains_json = domains_json or os.path.join(P.STAGING, "domains.json")
    out_path = out_path or os.path.join(P.STAGING, "scores.json")

    csv = pd.read_csv(scores_csv)
    with open(domains_json, encoding="utf-8") as fh:
        DOM = json.load(fh)

    def _p1_flags(iso):
        """(low_conf, not_scored) over the 11 Product-1 domains, sorted."""
        dd = DOM.get(iso, {})
        low = sorted(s for s, v in dd.items()
                     if v.get("phase") in PRODUCT1_PHASES
                     and v.get("scored") and v.get("low_conf"))
        ns = sorted(s for s, v in dd.items()
                    if v.get("phase") in PRODUCT1_PHASES and not v.get("scored"))
        return low, ns

    LOWCONF = {i: _p1_flags(i)[0] for i in DOM}
    NOTSCORED = {i: _p1_flags(i)[1] for i in DOM}

    comp_col, R_col, E_col = (
        "v0_4_spu_w05_composite", "v0_4_spu_w05_R", "v0_4_spu_w05_E")

    # rank on the ROUNDED displayed composite, ties broken by row order (method=first)
    comp_round = csv.set_index("iso3")[comp_col].round(4)
    ranks = comp_round.rank(ascending=False, method="first")

    countries = []
    for r in csv.itertuples():
        iso = r.iso3
        comp = _r4(getattr(r, comp_col))
        scored = comp is not None
        low_conf = LOWCONF.get(iso, [])
        not_scored = NOTSCORED.get(iso, [])
        rec = {
            "iso3": iso,
            "name": r.country_name,
            "composite": comp,
            "R": _r4(getattr(r, R_col)),
            "E": _r4(getattr(r, E_col)),
            "scored": bool(scored),
        }
        if scored:
            rec["rank"] = int(ranks[iso])
        rec["n_domains"] = 11
        rec["domains_low_conf"] = len(low_conf)
        rec["domains_not_scored"] = len(not_scored)
        rec["low_conf"] = low_conf
        rec["not_scored_domains"] = not_scored
        countries.append(rec)

    # Monte-Carlo rank bands + the "lower confidence" result class, computed on
    # the displayed build (model + the badge/band rule live in uncertainty.py).
    # Field name discipline: `low_confidence` is a bool; the pre-existing
    # `low_conf` key is a LIST of flagged domain slugs — do not conflate.
    per_iso, unc_meta = U.compute_uncertainty(countries)
    for c in countries:
        if c["scored"]:
            c.update(per_iso[c["iso3"]])
            c["low_confidence"] = U.is_low_confidence(c)

    scored_comps = [c["composite"] for c in countries if c["composite"] is not None]
    meta = {
        "n_universe": len(countries),
        "n_scored": len(scored_comps),
        "composite_min": round(min(scored_comps), 4),
        "composite_max": round(max(scored_comps), 4),
        "column_composite": comp_col,
        "column_R": R_col,
        "column_E": E_col,
        "source_csv": "rebuild-v0.4/scores_v0_4.csv",
        "n_domains": 11,
        "coverage_note": _COVERAGE_NOTE,
        "build_date": (os.environ.get("FLSRI_BUILD_DATE")
                       or datetime.date.today().isoformat()),
        "citation": ("Cite the framework and code; country scores are estimates of "
                     "structural conditions with published uncertainty bands."),
        "tier_cuts": list(U.TIER_CUTS),
        "uncertainty": unc_meta,
    }
    _dump({"meta": meta, "countries": countries}, out_path)
    print("WROTE:", out_path, f"({len(countries)} countries, {len(scored_comps)} scored)")
    return out_path


def _r3(x):
    return None if pd.isna(x) else round(float(x), 3)


def build_divergence_json(headline_csv=None, out_path=None):
    """divergence.json <- geospatial/divergence_headline.csv (12 rows).
    national_pctile rounded to 3dp; rows preserve the CSV order (divergence desc)."""
    headline_csv = headline_csv or os.path.join(P.PORT_GEO, "divergence_headline.csv")
    out_path = out_path or os.path.join(P.STAGING, "divergence.json")
    df = pd.read_csv(headline_csv)
    rows = []
    for r in df.itertuples():
        rows.append({
            "iso3": r.iso3,
            "country": r.country_name,
            "composite_rank": int(r.composite_rank),
            "national_pctile": _r3(r.national_pctile_risk),
            "n_units": int(r.n_units),
            "worst_corridor_name": r.worst_corridor_name,
            "worst_corridor_risk": _r3(r.worst_corridor_risk),
            "corridor_pctile": _r3(r.corridor_pctile),
            "divergence": _r3(r.divergence),
        })
    obj = {
        "meta": {
            "desc": ("Countries whose worst sub-national corridor sits far above "
                     "their national risk percentile. divergence = corridor global "
                     "percentile minus national global percentile."),
            "source": "geospatial/divergence_headline.csv",
            "n": len(rows),
        },
        "rows": rows,
    }
    _dump_divergence(obj, out_path)
    print("WROTE:", out_path, f"({len(rows)} rows)")
    return out_path


def build_subnational_json(subnat_dir=None, out_path=None):
    """subnational.json <- geospatial/subnational/{worst_corridors,hidden_risk_countries,
    within_country_spread}.csv produced by step4_surface.py."""
    subnat_dir = subnat_dir or P.PORT_SUBNAT
    out_path = out_path or os.path.join(P.STAGING, "subnational.json")
    worst = pd.read_csv(os.path.join(subnat_dir, "worst_corridors.csv"))
    hidden = pd.read_csv(os.path.join(subnat_dir, "hidden_risk_countries.csv"))
    spread = pd.read_csv(os.path.join(subnat_dir, "within_country_spread.csv"))

    corridors = [{
        "iso3": r.iso3, "country": r.CNTRY_NAME,
        "region": r.ADMIN_NAME, "risk": _r3(r.risk_surface),
    } for r in worst.itertuples()]
    hidden_risk = [{
        "iso3": r.iso3, "n_units": int(r.n_units),
        "risk_max": _r3(r.risk_max), "risk_mean": _r3(r.risk_mean),
        "country_precarity": _r3(r.country_precarity), "tier": r.country_tier,
    } for r in hidden.itertuples()]
    wspread = [{
        "iso3": r.iso3, "n_units": int(r.n_units),
        "risk_min": _r3(r.risk_min), "risk_max": _r3(r.risk_max),
        "risk_range": _r3(r.risk_range), "tier": r.country_tier,
    } for r in spread.itertuples()]
    obj = {
        "meta": {
            "coverage": "97 IPUMS-International countries, ~1,450 reliable admin-1 units",
            "source": "geospatial/subnational/",
            "weights": "0.70 precarity + 0.30 shock (illustrative, not locked)",
            "within_country_share": "~17-21% for IPUMS precarity signals",
        },
        "corridors": corridors,
        "hidden_risk": hidden_risk,
        "within_country_spread": wspread,
    }
    _dump_subnational(obj, out_path)
    print("WROTE:", out_path,
          f"({len(corridors)} corridors, {len(hidden_risk)} hidden, {len(wspread)} spread)")
    return out_path


def build_overlay_json(spatial_overlay_dir=None, out_path=None):
    """overlay.json <- geospatial/build_overlay.py outputs (hotspot_points.csv,
    hotspot_grid.csv, corridor_pairs.csv). The published file is the SUBSET
    {points, heat, corridor} of the full overlay_data.js payload; point records
    drop the `dth` (deaths) key.

    This emits STRICT JSON (no NaN/Inf — see _dump_lisa). It does NOT add the
    remittance-OUTFLOW `rout` field or backfill the ~47 destination centroids
    (Gulf states, etc.): those are a separate enrichment applied to the published
    file by geospatial/add_remittance_outflows.py, which MUST run AFTER this step.
    A bare re-run of this step therefore produces a VALID overlay.json that is
    missing only `rout` (the map degrades gracefully: destination dots disappear,
    origin dots and risk still render). Re-run add_remittance_outflows.py to
    restore the full shipped overlay.json."""
    geo = spatial_overlay_dir or P.PORT_GEO
    out_path = out_path or os.path.join(P.STAGING, "overlay.json")
    pts = pd.read_csv(os.path.join(geo, "hotspot_points.csv"))
    grid = pd.read_csv(os.path.join(geo, "hotspot_grid.csv"))
    corr = pd.read_csv(os.path.join(geo, "corridor_pairs.csv"))

    points = [{
        "lat": round(float(r.lat), 3), "lon": round(float(r.lon), 3),
        "t": r.dtype, "c": r.country,
        "y": int(r.year) if pd.notna(r.year) else None,
        "z": round(float(r.gi_z), 2), "hot": r.hot,
        "aff": int(r.affected) if pd.notna(r.affected) else None,
    } for r in pts.itertuples()]
    # build_overlay writes heat verbatim from the already-rounded grid CSV (no re-rounding)
    heat = [[float(g.lat), float(g.lon), float(g.d)] for g in grid.itertuples()]
    # corridor needs centroids (mean event lat/lon by country), as build_overlay derives
    cent = pts.groupby("country").agg(
        lat=("lat", "mean"), lon=("lon", "mean")).reset_index()
    name2cent = {}
    name2iso = {}
    for r in corr.itertuples():
        name2iso[r.country_name] = r.iso3
    centmap = {row.country: [round(float(row.lat), 3), round(float(row.lon), 3)]
               for row in cent.itertuples()}
    iso2cent = {name2iso[n]: c for n, c in centmap.items() if n in name2iso}
    corridor = [{
        "iso3": r.iso3, "name": r.country_name,
        "out": None if pd.isna(r.outbound_remit) else round(float(r.outbound_remit), 3),
        "inb": None if pd.isna(r.inbound_migrant) else round(float(r.inbound_migrant), 3),
        "role": r.corridor_role,
        # unscored micro-states have NaN composite_score; _r4 -> null (not NaN) so the
        # emitted file is strict JSON. A bare round(float(NaN),4) here was the source of
        # the 11 bare `NaN` tokens that made overlay.json invalid JSON.
        "comp": _r4(r.composite_score),
        "cent": iso2cent.get(r.iso3),
    } for r in corr.itertuples()]
    obj = {"points": points, "heat": heat, "corridor": corridor}
    _dump_lisa(obj, out_path)  # compact_tight, ensure_ascii=True
    print("WROTE:", out_path,
          f"({len(points)} points, {len(heat)} heat cells, {len(corridor)} corridor)")
    return out_path


def _lisa_summary_counts(lisa_csv, cluster_col="cluster_fdr"):
    df = pd.read_csv(lisa_csv)
    return df[cluster_col].value_counts().to_dict()


def build_lisa_json(spatial_dir=None, out_path=None):
    """lisa.json <- spatial-analysis/{lisa_admin1.csv, lisa_summary.json,
    _lisa_country_geo.geojson} produced by compute_lisa.py.
      admin1     : {id -> {q:cluster_fdr, v:round(value,3), name, iso3}}
      country_geo: the country LISA geojson, properties slimmed to {iso3,name,cluster,value}
      summary    : moran I + FDR cluster counts for both layers
    """
    sp = spatial_dir or P.PORT_SPATIAL
    out_path = out_path or os.path.join(P.STAGING, "lisa.json")
    a1 = pd.read_csv(os.path.join(sp, "lisa_admin1.csv"), dtype={"id": str})
    summ = json.load(open(os.path.join(sp, "lisa_summary.json")))
    cgeo = json.load(open(os.path.join(sp, "_lisa_country_geo.geojson")))

    admin1 = {}
    for r in a1.itertuples():
        admin1[str(r.id)] = {
            "q": r.cluster_fdr,
            "v": round(float(r.value), 3),
            "name": r.name,
            "iso3": r.iso3,
        }
    # slim country_geo feature properties to {iso3, name, cluster, value}
    feats = []
    for f in cgeo["features"]:
        p = f["properties"]
        f = dict(f)
        f["properties"] = {
            "iso3": p.get("iso3"),
            "name": p.get("country_name", p.get("name")),
            "cluster": p.get("cluster"),
            "value": p.get("value"),
        }
        feats.append(f)
    country_geo = {k: cgeo[k] for k in ("type", "name", "crs")} | {"features": feats}

    obj = {
        "admin1": admin1,
        "country_geo": country_geo,
        "summary": {
            "admin1_moran": summ["admin1"]["global_moran_I"],
            "country_moran": summ["country"]["global_moran_I"],
            "admin1_counts": summ["admin1"]["cluster_counts_fdr"],
            "country_counts": summ["country"]["cluster_counts_fdr"],
        },
    }
    _dump_lisa(obj, out_path)
    print("WROTE:", out_path, f"({len(admin1)} admin1 units)")
    return out_path


def build_lisa_admin1_json(spatial_dir=None, out_path=None):
    """lisa_admin1.json <- spatial-analysis/lisa_admin1.csv.
      {id -> {cluster, name, iso3, value}}  where value is a STRING of round(v,3)."""
    sp = spatial_dir or P.PORT_SPATIAL
    out_path = out_path or os.path.join(P.STAGING, "lisa_admin1.json")
    a1 = pd.read_csv(os.path.join(sp, "lisa_admin1.csv"), dtype={"id": str})
    out = {}
    for r in a1.itertuples():
        out[str(r.id)] = {
            "cluster": r.cluster_fdr,
            "name": r.name,
            "iso3": r.iso3,
            "value": str(round(float(r.value), 3)),
        }
    _dump_lisa_admin1(out, out_path)
    print("WROTE:", out_path, f"({len(out)} admin1 units)")
    return out_path


if __name__ == "__main__":
    P.ensure_staging()
    build_scores_json()
    build_divergence_json()
    build_subnational_json()
    build_overlay_json()
    build_lisa_json()
    build_lisa_admin1_json()
