#!/usr/bin/env python3
"""Build public/data/indicators.json — per-country standardized indicator values.

Exposes the signal layer the scorer consumes: every wired signal's 0-1
standardized, risk-aligned value per country (data/processed/*), with the
temporal variants substituted where the published build uses them (ND-GAIN,
UNCTAD), plus the three specialist slots (GLOTiP / DEMSCORE / kafala).

Display names follow the dashboard naming convention: a phase.domain.signal
number plus a human-readable title (e.g. "1.8.1 Disaster Shock Intensity").
Numbers are positional in the current structure; titles reuse the original
dashboard's names wherever the indicator survives.

Notes:
  - v2xcl_slave is wired in the crosswalk but excluded from scoring
    (DROP_SIGNALS, outcome-circularity) — it is not exported.
  - Values are as-entered EXCEPT the hazardous-sector materiality ramp,
    which is applied within the scorer; that one signal is footnoted.
  - DEMSCORE / kafala enter two domains (Constrained Mobility and State
    Production); they are listed under both, marked supplementary.

Run from the repo root:  python pipeline/6_site_data/build_indicators_json.py
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, REPO)
from pipeline import crosswalk as cw  # noqa: E402

PROC = os.path.join(REPO, "data", "processed")
EXP = os.path.join(REPO, "external", "experiments")
OUT = os.path.join(REPO, "public", "data", "indicators.json")

PHASE_NUM = {"recruitment": "1", "exploitation": "2", "monetization": "M"}
DROPPED = {"v2xcl_slave"}

# id -> display title (dashboard convention; survivors keep their original names)
TITLES = {
    "ep_poverty_headcount_685":        "Poverty Headcount ($6.85)",
    "ep_informal_employment_share":    "Informal Employment Share",
    "ep_agrarian_employment_share":    "Agrarian Employment Concentration",
    "ep_income_volatility":            "Income Volatility",
    "wb_gini":                         "Economic Inequality (Gini)",
    "findex_account_exclusion":        "Account Exclusion (Findex)",
    "findex_informal_borrowing":       "Informal Borrowing Share (Findex)",
    "findex_borrow_prevalence":        "Formal Credit Constraint (Findex)",
    "v2xcl_dmove":                     "Policy-Constrained Mobility",
    "henley_passport_access":          "Passport Restriction Index",
    "unhcr_refugees_by_coo":           "Refugee Origin Pressure",
    "epr_excluded_pop_share":          "Ascriptive Exclusion (EPR)",
    "lnr_birth_registration_incompleteness": "Civil Documentation Access",
    "lnr_statelessness_prevalence":    "Stateless Burden",
    "gs_lfp_gender_gap":               "Gendered Labor Force Gap",
    "gs_gender_inequality_index":      "Gender Inequality Index (UNDP)",
    "gs_mobility_constraint":          "Codified Mobility Constraint",
    "gs_sex_sector_channel_share":     "Sex-Sector Channel Share",
    "age_child_labor_prevalence":      "Child Labor Prevalence",
    "age_out_of_school_rate":          "Out-of-School Rate",
    "age_child_cohort_share":          "Child Cohort Share",
    "age_child_marriage_rate":         "Child Marriage Rate",
    "aux_emdat_disaster_shock":        "Disaster Shock Intensity",
    "ndgain_climate_vulnerability":    "Climate Vulnerability (ND-GAIN)",
    "ucdp_conflict_intensity":         "Armed Conflict Exposure",
    "unhcr_idps_by_country":           "IDP Displacement Severity",
    "LAI_INDE_NOC_RT_A":               "Labor Inspection Capacity",
    "ILR_CBCT_NOC_RT_A":               "Collective Bargaining Coverage",
    "ILR_TUMT_NOC_RT_A":               "Trade Union Density",
    "esd_d1_hazardous_sector_share":   "Hazardous-Sector Employment Share",
    "esd_d2_informal_employment_share": "Informal Risk Exposure",
    "aux_unctad_export_concentration": "Export Commodity Concentration",
    "v2xnp_client":                    "V-Dem Clientelism Index",
    "trace_bribery_total":             "TRACE Bribery Risk",
    "wb_bribery_incidence":            "Bribery Prevalence",
    "basel_fatf_me_effectiveness":     "AML Effectiveness Gap (FATF-ME)",
    "basel_tjn_fsi":                   "Financial Secrecy (TJN FSI)",
    "basel_fatf_listing_flag":         "FATF Listing Status",
    "monet_b_shadow_economy":          "Shadow Economy Size",
    "monet_b_financial_exclusion":     "Formal Financial Exclusion",
    "glotip_fl_share_of_detected":     "Forced-Labor Share of Detected (GLOTiP)",
    "demscore_tied_status_signal":     "Tied-Status Legal Coding (DEMSCORE)",
    "kafala_tied_status_signal":       "Kafala Sponsorship Regime",
}
EXTRA_SOURCES = {
    "aux_emdat_disaster_shock": "EM-DAT (CRED/UCLouvain) blend",
    "glotip_fl_share_of_detected": "UNODC GLOTiP (detected-victim composition)",
    "demscore_tied_status_signal": "DEMSCORE legal-status coding",
    "kafala_tied_status_signal": "FLSRI tied-status coding (8 adopting states)",
}


def load_table(name):
    path = os.path.join(PROC, f"{name}.csv")
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            iso = row.pop("iso3", "").strip()
            if iso:
                out[iso] = {k: (float(v) if v not in ("", None) else None)
                            for k, v in row.items()}
    return out


def main():
    register = {r["indicator"]: r for r in
                csv.DictReader(open(os.path.join(REPO, "config", "data_register.csv"),
                                    newline="", encoding="utf-8"))}
    scores = json.load(open(os.path.join(REPO, "public", "data", "scores.json")))
    names = {c["iso3"]: c["name"] for c in scores["countries"]}
    isos = sorted(names)

    # temporal variants the published build substitutes (as-entered values)
    tf = {}
    with open(os.path.join(EXP, "rebuild-temporal", "temporal_features.csv"),
              newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            tf[row["iso3"]] = row
    TEMPORAL = {("ndgain", "ndgain_climate_vulnerability"): "ndgain_vuln_temporal",
                ("aux_unctad", "aux_unctad_export_concentration"):
                    "unctad_export_concentration_temporal"}

    # specialist slots
    def csv_series(path, col, restrict=None):
        out = {}
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get(col, "") not in ("", None) and \
                        (restrict is None or row["iso3"] in restrict):
                    out[row["iso3"]] = float(row[col])
        return out
    KAFALA_ADOPT = {"ARE", "QAT", "SAU", "KWT", "BHR", "OMN", "JOR", "LBN"}
    extras = {
        "glotip_fl_share_of_detected": csv_series(
            os.path.join(EXP, "fl-signals", "fl_candidate_signals.csv"),
            "glotip_fl_share_of_detected"),
        "demscore_tied_status_signal": csv_series(
            os.path.join(EXP, "rebuild-v0.2", "demscore_signal.csv"),
            "demscore_tied_status_signal"),
        "kafala_tied_status_signal": csv_series(
            os.path.join(EXP, "source-tiedstatus", "kafala_signal.csv"),
            "kafala_tied_status_signal", restrict=KAFALA_ADOPT),
    }

    domains, signals, values = [], [], {iso: {} for iso in isos}
    tables = {}
    phase_domain_idx = {}
    domain_labels = {s: s.replace("-", " ").title() for s in cw.CROSSWALK}
    # nicer labels straight from the shipped domains.json
    dj = json.load(open(os.path.join(REPO, "public", "data", "domains.json")))
    first = dj[next(iter(dj))]
    for slug, obj in first.items():
        if isinstance(obj, dict) and obj.get("label"):
            domain_labels[slug] = obj["label"]

    for slug, spec in cw.CROSSWALK.items():
        phase = ("monetization" if slug in cw.MONETIZATION_DOMAINS else
                 next(p for p, ds in cw.PRODUCT1_PHASES.items() if slug in ds))
        pn = PHASE_NUM[phase]
        phase_domain_idx[pn] = phase_domain_idx.get(pn, 0) + 1
        dn = f"{pn}.{phase_domain_idx[pn]}"
        domains.append({"slug": slug, "num": dn, "label": domain_labels[slug],
                        "phase": phase})
        si = 0
        for (t, c) in spec["signals"]:
            if c in DROPPED:
                continue
            si += 1
            reg = register.get(c, {})
            sig = {"id": c, "num": f"{dn}.{si}",
                   "title": TITLES.get(c, c),
                   "source": (reg.get("source") or EXTRA_SOURCES.get(c, t))[:80],
                   "domain": slug}
            if (t, c) == ("econ_structure_demand", "esd_d1_hazardous_sector_share"):
                sig["note"] = "materiality ramp applied (shares below 0.20 scaled down, as scored)"
            signals.append(sig)
            if t not in tables:
                tables[t] = load_table(t)
            tcol = TEMPORAL.get((t, c))
            for iso in isos:
                v = None
                if tcol and iso in tf and tf[iso].get(tcol) not in ("", None):
                    v = float(tf[iso][tcol])
                elif iso in tables[t]:
                    v = tables[t][iso].get(c)
                if v is not None:
                    # materiality ramp: the scorer enters this signal ramped
                    if c == "esd_d1_hazardous_sector_share" and v < 0.20:
                        v = v * (v / 0.20)
                    values[iso][c] = round(v, 3)
        # supplementary slots
        for ex_id, dom_set in (("glotip_fl_share_of_detected",
                                {"state-production-of-unfreedom"}),
                               ("demscore_tied_status_signal", cw.__dict__.get(
                                   "DEM_DOMAINS", {"state-production-of-unfreedom",
                                                   "constrained-mobility"})),
                               ("kafala_tied_status_signal",
                                {"state-production-of-unfreedom",
                                 "constrained-mobility"})):
            if slug in dom_set:
                si += 1
                signals.append({"id": ex_id, "num": f"{dn}.{si}",
                                "title": TITLES[ex_id],
                                "source": EXTRA_SOURCES[ex_id],
                                "domain": slug, "supplementary": True})
                for iso, v in extras[ex_id].items():
                    if iso in values:
                        values[iso][ex_id] = round(v, 3)

    # ---- fenced modeled estimates (data/staging/modeled_estimates.csv) ----
    # design decisions: show the value but mark it modeled, with its 80%
    # interval and provenance; it is never presented as an observed value.
    modeled = []
    _mpath = os.path.join(REPO, "data", "staging", "modeled_estimates.csv")
    if os.path.exists(_mpath):
        with open(_mpath, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                iso = (row.get("iso3") or "").strip()
                sid = (row.get("signal") or "").strip()
                if iso in values:
                    val = round(float(row["value"]), 3)
                    values[iso][sid] = val
                    modeled.append({
                        "iso3": iso, "signal": sid, "value": val,
                        "interval": [round(float(row["pi80_lo"]), 3),
                                     round(float(row["pi80_hi"]), 3)],
                        "status": (row.get("status") or "modeled estimate").strip(),
                        "provenance": (row.get("provenance") or "").strip()})

    obj = {"meta": {
               "note": ("Standardized 0-1 risk-aligned signal values as they "
                        "enter the published scorer (temporal variants "
                        "substituted where used). 0 = lowest risk anchor, "
                        "1 = highest. Missing values are absent, never zero. "
                        "Values listed in `modeled` are fenced model estimates, "
                        "shown with an 80% interval and never as observations."),
               "n_signals": len({s['id'] for s in signals}),
               "n_countries": len(isos)},
           "domains": domains,
           "signals": signals,
           "modeled": modeled,
           "countries": [{"iso3": iso, "name": names[iso],
                          "values": values[iso]} for iso in isos]}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=True)
    print(f"WROTE {OUT} ({len(signals)} signal rows, "
          f"{sum(len(v) for v in values.values())} values, "
          f"{os.path.getsize(OUT)//1024} KB)")


if __name__ == "__main__":
    main()
