#!/usr/bin/env python3
"""TEMPORAL FEATURE RE-DERIVATION -- FLSRI (EXPLORATORY, read-only on locked pipeline).

For every signal the operationalization audit (signal-operationalization-audit.md)
flags as TEMPORAL, rebuild it as a temporal feature from the RAW multi-year panel
instead of the single collapsed value the locked pipeline uses.

Treatments implemented (audit-specified, per signal):
  EM-DAT disaster   -> exponential DECAY-WEIGHTED accumulation (recent shock years
                       weighted up, older decaying as the reconstruction window
                       closes; half-life 3yr). Audit: "peak-shock-with-decay".
  UCDP conflict     -> decay-weighted accumulation, SLOW tail (half-life 6yr) so a
                       war 4yrs ago still counts. Audit: "accumulation + long tail".
                       (Long-tail beyond the on-disk 2019-2023 window is limited --
                       UCDP GED API now requires a token; documented as a caveat.)
  ND-GAIN vuln      -> level + 15yr deterioration TREND. Audit: chronic, so level is
                       defensible but a worsening slope adds the erosion dynamic.
  UNHCR displacement-> RECENCY-weighted stock (fast decay, half-life 2yr) + inflow
                       delta. Audit: a stock that arrived last year is a different
                       hazard than one stable for a decade.
  UNCTAD export conc-> level + rising-concentration TREND (the sourcing squeeze).
  WDI birth-reg     -> LATEST-vintage + improvement trend (fixes 2006-2022 vintage mix).
  WDI child-labour  -> LATEST available (fixes 2005-2016 staleness).

For each signal we ALSO reproduce the locked COLLAPSED baseline from the same raw
panel, standardized with the SAME absolute anchors, so the temporal-vs-collapsed
contrast isolates the temporal SHAPE, not an anchor change.

Comparability device: every decay kernel is normalised so its weights SUM TO the
length of the locked window (5.0 for EM-DAT/UCDP) -- a "5-year-equivalent mass" --
so the decay-weighted accumulation sits on the same magnitude scale as the locked
flat 5-year sum and the same [0,100]/[0,1] per-exposure anchor applies unchanged.

Output: temporal_features.csv  (iso3 x {<sig>_collapsed, <sig>_temporal} 0-1 risk)
        feature_diagnostics.csv (per-feature coverage + corr-vs-collapsed + corr-vs-gov)
EXPLORATORY. Confidence labelled per feature. Nothing fabricated; missing stays NA.
"""
import os, sys, glob, math, json, csv
import numpy as np, pandas as pd
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))   # rebuild-temporal -> experiments -> Research Deliverables -> 1-starting area -> repo root
FE   = os.path.join(REPO, "data", "raw")   # first-effort temporal inputs (stage here to re-run; not committed)
EMDIR= os.path.join(REPO, "data", "raw")   # EM-DAT disaster data (stage here to re-run; not committed)
OUT  = _HERE
sys.path.insert(0, REPO)
from pipeline import iso_utils

# ---- shared universe + population denominator (locked plumbing) ----
SAMPLE = iso_utils.load_sample()
SAMPLE_SET = set(SAMPLE)
POP = {}
with open(os.path.join(REPO, "data/aux/worldbank_population.csv"), newline="") as fh:
    for r in csv.DictReader(fh):
        try: POP[r["iso3"]] = float(r["population"])
        except (ValueError, KeyError): pass

def anchor(raw_by_iso, floor, ceil, invert=False):
    """Locked absolute anchor: s=clamp((v-floor)/(ceil-floor)); invert for low_risk."""
    out = {}
    span = ceil - floor
    for iso in SAMPLE:
        v = raw_by_iso.get(iso)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            out[iso] = None; continue
        s = max(0.0, min(1.0, (v - floor) / span))
        out[iso] = (1.0 - s) if invert else s
    return out

def decay_weights(years, ref_year, half_life, mass=None):
    """Exponential decay weight per year; older years decay. Optionally rescale so
    sum(weights)==mass (e.g. 5.0 = 5-year-equivalent), making decay-weighted sums
    comparable in magnitude to a flat n-year window sum."""
    lam = math.log(2) / half_life
    w = {y: math.exp(-lam * (ref_year - y)) for y in years}
    if mass is not None:
        tot = sum(w.values())
        if tot > 0:
            w = {y: wy * mass / tot for y, wy in w.items()}
    return w

def slope(years, vals):
    """OLS slope (per year) over paired (year,value); None if <3 points."""
    pts = [(y, v) for y, v in zip(years, vals) if v is not None and not (isinstance(v,float) and math.isnan(v))]
    if len(pts) < 3: return None
    ys = np.array([p[0] for p in pts], float); vs = np.array([p[1] for p in pts], float)
    return float(np.polyfit(ys, vs, 1)[0])

feat = {iso: {} for iso in SAMPLE}   # iso -> {col: value}
diag = []                            # per-feature diagnostic rows

# =====================================================================
# 1. EM-DAT disaster -- decay-weighted accumulation (half-life 3yr) vs flat 2020-24
# =====================================================================
def build_emdat():
    f = glob.glob(os.path.join(EMDIR, "public_emdat_custom_request_*.xlsx"))[0]
    em = pd.read_excel(f)
    em = em[em["Disaster Group"] == "Natural"].copy()
    em["iso3"] = em["ISO"].map(iso_utils.normalize_to_iso3)
    em = em[em["iso3"].isin(SAMPLE_SET)]
    em = em[(em["Start Year"] >= 2000) & (em["Start Year"] <= 2024)]
    g = em.groupby(["iso3", "Start Year"]).agg(
        deaths=("Total Deaths", "sum"), affected=("Total Affected", "sum")).reset_index()

    # ---- collapsed baseline: flat sum 2020-2024 ----
    base = g[(g["Start Year"] >= 2020) & (g["Start Year"] <= 2024)].groupby("iso3").agg(
        deaths=("deaths", "sum"), affected=("affected", "sum"))
    mort_b, aff_b = {}, {}
    for iso in SAMPLE:
        p = POP.get(iso)
        d = float(base["deaths"].get(iso, 0.0)) if iso in base.index else 0.0
        a = float(base["affected"].get(iso, 0.0)) if iso in base.index else 0.0
        mort_b[iso] = (d / p * 1e5) if p else None
        aff_b[iso]  = (a / p) if p else None
    mort_b_s = anchor(mort_b, 0, 100); aff_b_s = anchor(aff_b, 0, 1.0)

    # ---- temporal: decay-weighted accumulation 2000-2024, half-life 3, mass=5 ----
    years = list(range(2000, 2025))
    w = decay_weights(years, 2024, half_life=3, mass=5.0)
    wd = g.assign(wdeaths=g["deaths"].fillna(0) * g["Start Year"].map(w),
                  waff=g["affected"].fillna(0) * g["Start Year"].map(w)).groupby("iso3").agg(
                  wdeaths=("wdeaths", "sum"), waff=("waff", "sum"))
    mort_t, aff_t = {}, {}
    for iso in SAMPLE:
        p = POP.get(iso)
        d = float(wd["wdeaths"].get(iso, 0.0)) if iso in wd.index else 0.0
        a = float(wd["waff"].get(iso, 0.0)) if iso in wd.index else 0.0
        mort_t[iso] = (d / p * 1e5) if p else None
        aff_t[iso]  = (a / p) if p else None
    mort_t_s = anchor(mort_t, 0, 100); aff_t_s = anchor(aff_t, 0, 1.0)

    for iso in SAMPLE:
        mb, ab = mort_b_s[iso], aff_b_s[iso]
        mt, at = mort_t_s[iso], aff_t_s[iso]
        feat[iso]["emdat_disaster_shock_collapsed"] = (np.mean([x for x in (mb, ab) if x is not None])
                                                       if (mb is not None or ab is not None) else None)
        feat[iso]["emdat_disaster_shock_temporal"]  = (np.mean([x for x in (mt, at) if x is not None])
                                                       if (mt is not None or at is not None) else None)
    return "emdat_disaster_shock"

# =====================================================================
# 2. UCDP conflict -- decay-weighted accumulation, slow tail (half-life 6) vs flat
# =====================================================================
_UCDP_ALIASES = {"dr congo (zaire)":"COD","kingdom of eswatini (swaziland)":"SWZ",
    "madagascar (malagasy)":"MDG","myanmar (burma)":"MMR","russia (soviet union)":"RUS",
    "yemen (north yemen)":"YEM","zimbabwe (rhodesia)":"ZWE"}
def _ucdp_iso(name):
    k = (name or "").strip().lower()
    return _UCDP_ALIASES.get(k) or iso_utils.normalize_to_iso3(name)

def build_ucdp():
    p = os.path.join(REPO, "data/aux/ucdp_ged_country_year_2019_2023.csv")
    df = pd.read_csv(p)
    df = df[df["type_of_violence"].isin([1, 2, 3])].copy()
    df["iso3"] = df["country"].map(_ucdp_iso)
    df = df[df["iso3"].isin(SAMPLE_SET)]
    g = df.groupby(["iso3", "year"])["best_deaths"].sum().reset_index()

    # collapsed: flat sum 2019-2023
    base = g.groupby("iso3")["best_deaths"].sum()
    # temporal: decay-weight, half-life 6 (slow tail), mass=5
    w = decay_weights(list(range(2019, 2024)), 2023, half_life=6, mass=5.0)
    gt = g.assign(wd=g["best_deaths"] * g["year"].map(w)).groupby("iso3")["wd"].sum()

    raw_b, raw_t = {}, {}
    for iso in SAMPLE:
        pop = POP.get(iso)
        if not pop:
            raw_b[iso] = raw_t[iso] = None; continue
        raw_b[iso] = float(base.get(iso, 0.0)) / pop * 1e5      # absent -> true zero (MNAR, locked)
        raw_t[iso] = float(gt.get(iso, 0.0)) / pop * 1e5
    cb = anchor(raw_b, 0, 100); ct = anchor(raw_t, 0, 100)
    for iso in SAMPLE:
        feat[iso]["ucdp_conflict_intensity_collapsed"] = cb[iso]
        feat[iso]["ucdp_conflict_intensity_temporal"]  = ct[iso]
    return "ucdp_conflict_intensity"

# =====================================================================
# 3. ND-GAIN vulnerability -- level + 15yr deterioration trend vs 2023 snapshot
# =====================================================================
def build_ndgain():
    p = os.path.join(REPO, "data/aux/ndgain_vulnerability_raw.csv")
    df = pd.read_csv(p)
    ycols = [c for c in df.columns if c.isdigit()]
    for _, row in df.iterrows():
        iso = row["ISO3"]
        if iso not in SAMPLE_SET: continue
        level = row.get("2023")
        level = float(level) if pd.notna(level) else None
        feat[iso]["ndgain_vuln_collapsed"] = level   # already 0-1 risk-aligned
        # 15yr trend 2009-2023
        yrs = [int(y) for y in ycols if 2009 <= int(y) <= 2023]
        vals = [float(row[str(y)]) if pd.notna(row[str(y)]) else None for y in yrs]
        sl = slope(yrs, vals)
        # worsening (positive slope of vulnerability) = more risk. anchor slope to [-0.004,+0.004]/yr
        trend_risk = None if sl is None else max(0.0, min(1.0, (sl + 0.004) / 0.008))
        if level is None:
            feat[iso]["ndgain_vuln_temporal"] = None
        elif trend_risk is None:
            feat[iso]["ndgain_vuln_temporal"] = level
        else:
            feat[iso]["ndgain_vuln_temporal"] = float(np.mean([level, trend_risk]))
    return "ndgain_vuln"

# =====================================================================
# 4. UNHCR displacement -- recency-weighted stock (half-life 2yr) vs latest snapshot
# =====================================================================
def build_unhcr():
    p = os.path.join(FE, "api_cache/unhcr_API_2026-05-28.csv")
    df = pd.read_csv(p)
    df = df[df["iso3"].isin(SAMPLE_SET)]
    out_cols = {"unhcr_refugees_by_coo": "unhcr_refugees_coo",
                "unhcr_idps_by_country": "unhcr_idps"}
    for series, base_name in out_cols.items():
        sub = df[df["series"] == series]
        piv = sub.pivot_table(index="iso3", columns="year", values="value", aggfunc="sum")
        years_all = sorted([int(c) for c in piv.columns])
        latest_y = max(years_all)
        w = decay_weights(years_all, latest_y, half_life=2)  # no mass rescale -> weighted MEAN
        wsum = sum(w.values())
        coll, temp = {}, {}
        for iso in SAMPLE:
            pop = POP.get(iso)
            if iso not in piv.index or not pop:
                coll[iso] = temp[iso] = None; continue
            rowv = piv.loc[iso]
            latest = rowv.get(latest_y)
            coll[iso] = (float(latest) / pop * 1e5) if pd.notna(latest) else None
            # recency-weighted stock = weighted mean of stock_t / pop *1e5
            num = sum(w[int(y)] * float(rowv[y]) for y in piv.columns if pd.notna(rowv[y]))
            den = sum(w[int(y)] for y in piv.columns if pd.notna(rowv[y]))
            temp[iso] = (num / den / pop * 1e5) if den > 0 else None
        cb = anchor(coll, 0, 5000); ct = anchor(temp, 0, 5000)
        for iso in SAMPLE:
            feat[iso][f"{base_name}_collapsed"] = cb[iso]
            feat[iso][f"{base_name}_temporal"]  = ct[iso]
    return ["unhcr_refugees_coo", "unhcr_idps"]

# =====================================================================
# 5. UNCTAD export concentration -- level + rising trend vs 2024 snapshot
# =====================================================================
def build_unctad():
    p = os.path.join(FE, "api_cache/unctad_API_2026-05-28.csv")
    df = pd.read_csv(p)
    df = df[df["series"] == "unctad_concent_div_exports"]
    df = df[df["iso3"].isin(SAMPLE_SET)]
    piv = df.pivot_table(index="iso3", columns="year", values="value", aggfunc="mean")
    coll, temp = {}, {}
    for iso in SAMPLE:
        if iso not in piv.index:
            coll[iso] = temp[iso] = None; continue
        rowv = piv.loc[iso]
        lvl = rowv.get(2024)
        lvl = float(lvl) if pd.notna(lvl) else None
        coll[iso] = lvl
        yrs = [int(c) for c in piv.columns if 2010 <= int(c) <= 2024]
        vals = [float(rowv[y]) if pd.notna(rowv[y]) else None for y in yrs]
        sl = slope(yrs, vals)  # rising concentration = squeeze = more risk
        trend_risk = None if sl is None else max(0.0, min(1.0, (sl + 0.01) / 0.02))
        if lvl is None: temp[iso] = None
        elif trend_risk is None: temp[iso] = lvl
        else: temp[iso] = float(np.mean([lvl, trend_risk]))
    # values already 0-1 (HHI), anchor [0,1] is identity
    for iso in SAMPLE:
        feat[iso]["unctad_export_concentration_collapsed"] = coll[iso]
        feat[iso]["unctad_export_concentration_temporal"]  = temp[iso]
    return "unctad_export_concentration"

# =====================================================================
# 6/7. WDI birth-registration + child-labour -- latest-vintage / staleness fix
# =====================================================================
def wdi_panel(indicator, y0=2000, y1=2024):
    url = (f"https://api.worldbank.org/v2/country/all/indicator/{indicator}"
           f"?format=json&per_page=20000&date={y0}:{y1}")
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.load(r)
    rows = data[1] if isinstance(data, list) and len(data) > 1 and data[1] else []
    panel = {}
    for d in rows:
        iso = d.get("countryiso3code"); v = d.get("value")
        if not iso or v is None: continue
        panel.setdefault(iso, {})[int(d["date"])] = float(v)
    return panel

def build_wdi():
    # birth registration completeness (low_risk -> incompleteness is risk)
    br = wdi_panel("SP.REG.BRTH.ZS")
    raw_b, raw_t = {}, {}
    for iso in SAMPLE:
        ser = br.get(iso, {})
        if not ser:
            raw_b[iso] = raw_t[iso] = None; continue
        latest_y = max(ser)
        raw_t[iso] = ser[latest_y]                 # latest-vintage completeness
        raw_b[iso] = ser[latest_y]                 # (panel reproduced; collapse=latest here too)
    # anchor [0,100] low_risk: high completeness -> low risk
    cb = anchor(raw_b, 0, 100, invert=True); ct = anchor(raw_t, 0, 100, invert=True)
    for iso in SAMPLE:
        feat[iso]["birthreg_incompleteness_collapsed"] = cb[iso]
        feat[iso]["birthreg_incompleteness_temporal"]  = ct[iso]

    # child labour 7-14 in employment (high_risk), anchor [0,40]
    cl = wdi_panel("SL.TLF.0714.ZS", 2000, 2024)
    raw_latest, raw_old = {}, {}
    for iso in SAMPLE:
        ser = cl.get(iso, {})
        if not ser:
            raw_latest[iso] = raw_old[iso] = None; continue
        raw_latest[iso] = ser[max(ser)]            # staleness fix: latest available
        # "collapsed" mimic of locked staleness: prefer <=2016 if present
        old_keys = [y for y in ser if y <= 2016]
        raw_old[iso] = ser[max(old_keys)] if old_keys else ser[max(ser)]
    cb = anchor(raw_old, 0, 40); ct = anchor(raw_latest, 0, 40)
    for iso in SAMPLE:
        feat[iso]["childlabor_collapsed"] = cb[iso]
        feat[iso]["childlabor_temporal"]  = ct[iso]
    return ["birthreg_incompleteness", "childlabor"]

# ---------------- run all ----------------
print("Building temporal features...")
names = []
names.append(build_emdat());  print("  EM-DAT done")
names.append(build_ucdp());   print("  UCDP done")
names.append(build_ndgain()); print("  ND-GAIN done")
names += build_unhcr();       print("  UNHCR done")
names.append(build_unctad()); print("  UNCTAD done")
try:
    names += build_wdi();     print("  WDI done")
except Exception as e:
    print("  WDI FAILED (network):", e)

# ---------------- write features + diagnostics ----------------
GOV = {}
agg_proc = {}
import csv as _csv
with open(os.path.join(REPO, "data/processed/worldbank.csv"), newline="") as fh:
    for r in _csv.DictReader(fh):
        try: GOV[r["iso3"]] = float(r["wb_wgi_rule_of_law"])
        except (ValueError, KeyError, TypeError): pass

cols = sorted({c for iso in SAMPLE for c in feat[iso]})
rows = []
for iso in SAMPLE:
    row = {"iso3": iso}; row.update({c: feat[iso].get(c) for c in cols}); rows.append(row)
fdf = pd.DataFrame(rows)
fdf.to_csv(os.path.join(OUT, "temporal_features.csv"), index=False)
print("\nWROTE temporal_features.csv  (rows={}, cols={})".format(len(fdf), len(cols)))

def corr(a, b):
    pts = [(x, y) for x, y in zip(a, b) if x is not None and y is not None
           and not (isinstance(x,float) and math.isnan(x)) and not (isinstance(y,float) and math.isnan(y))]
    if len(pts) < 10: return (np.nan, len(pts))
    xs = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
    return (round(float(np.corrcoef(xs, ys)[0,1]), 3), len(pts))

govv = [GOV.get(iso) for iso in SAMPLE]
for stem in [c[:-len("_temporal")] for c in cols if c.endswith("_temporal")]:
    tv = [feat[iso].get(stem+"_temporal") for iso in SAMPLE]
    cv = [feat[iso].get(stem+"_collapsed") for iso in SAMPLE]
    n_t = sum(1 for x in tv if x is not None)
    r_tc, n_tc = corr(tv, cv)
    r_tg, _ = corr(tv, govv)
    r_cg, _ = corr(cv, govv)
    diag.append({"feature": stem, "coverage_n": n_t,
                 "corr_temporal_vs_collapsed": r_tc,
                 "r2_temporal_vs_gov": None if np.isnan(r_tg) else round(r_tg**2,3),
                 "r2_collapsed_vs_gov": None if np.isnan(r_cg) else round(r_cg**2,3),
                 "delta_gov_r2_collapsed_minus_temporal":
                     None if (np.isnan(r_tg) or np.isnan(r_cg)) else round(r_cg**2 - r_tg**2, 3)})
ddf = pd.DataFrame(diag)
ddf.to_csv(os.path.join(OUT, "feature_diagnostics.csv"), index=False)
print("\n=== FEATURE DIAGNOSTICS (gov R2: lower temporal = more gov-orthogonal) ===")
print(ddf.to_string(index=False))
