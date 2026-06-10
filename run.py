#!/usr/bin/env python3
"""FLSRI build run -- v1 machinery over the processed data layer.

Pipeline: load processed 0-1 signal tables -> domain->signal crosswalk ->
aggregate (equal-weight signals->domain->phase, ONE domain-level governance
attenuator, rule-9 coverage floor) -> Product-1 composite (geometric mean of
Recruitment x Exploitation; Monetization excluded) -> rank.

Also runs the governance-attenuation rank-stability sensitivity pass
(f_gov applied vs not) and writes:
  outputs/scores.csv, outputs/rankings.csv, outputs/build-notes-<date>.md

Run:  python run.py
Deps: stdlib + pycountry (for country names; falls back to ISO3 if absent).
"""
import csv
import os
from datetime import date

from pipeline import aggregate, composite, crosswalk

try:
    import pycountry
    _HAS_PC = True
except ImportError:
    _HAS_PC = False

HERE = os.path.dirname(os.path.abspath(__file__))
COUNTRIES = os.path.join(HERE, "config", "iso3_countries.csv")
OUTDIR = os.path.join(HERE, "outputs")
TODAY = date.today().isoformat()


def load_countries(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return [r["iso3"].strip() for r in csv.DictReader(fh)
                if r.get("iso3", "").strip()]


def country_name(iso3):
    if _HAS_PC:
        rec = pycountry.countries.get(alpha_3=iso3)
        if rec is not None:
            return getattr(rec, "common_name", None) or rec.name
    return iso3


# ---------- sensitivity helpers ----------
def kendall_tau(rank_a, rank_b):
    """Kendall's tau-b between two {iso3: rank} dicts over shared keys."""
    keys = sorted(set(rank_a) & set(rank_b))
    n = len(keys)
    if n < 2:
        return None
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            ai, aj = rank_a[keys[i]], rank_a[keys[j]]
            bi, bj = rank_b[keys[i]], rank_b[keys[j]]
            sa = (ai > aj) - (ai < aj)
            sb = (bi > bj) - (bi < bj)
            p = sa * sb
            if p > 0:
                concordant += 1
            elif p < 0:
                discordant += 1
    denom = concordant + discordant
    return (concordant - discordant) / denom if denom else None


def ranks_from_composite(comp):
    ranking = composite.rank(comp)
    return {iso3: r for (r, iso3, _s) in ranking}


def main():
    countries = load_countries(COUNTRIES)
    tables = aggregate.load_processed()

    # --- main run: governance attenuation ON ---
    agg = aggregate.aggregate_all(countries, tables, apply_governance=True)
    comp = composite.composite_scores(agg)
    ranking = composite.rank(comp)
    rank_map = {iso3: r for (r, iso3, _s) in ranking}

    # --- sensitivity run: governance attenuation OFF ---
    agg_off = aggregate.aggregate_all(countries, tables, apply_governance=False)
    comp_off = composite.composite_scores(agg_off)
    ranks_on = ranks_from_composite(comp)
    ranks_off = ranks_from_composite(comp_off)
    tau = kendall_tau(ranks_on, ranks_off)

    # top-quartile (top-48) churn on the shared scored set
    shared = set(ranks_on) & set(ranks_off)
    q = 48
    top_on = {c for c in ranks_on if ranks_on[c] <= q and c in shared}
    top_off = {c for c in ranks_off if ranks_off[c] <= q and c in shared}
    churn_in = top_on - top_off
    churn_out = top_off - top_on
    churn = (len(churn_in | churn_out)) / (2 * q) if q else None

    os.makedirs(OUTDIR, exist_ok=True)
    _write_scores(countries, agg, comp, rank_map)
    _write_rankings(ranking)
    coverage, anomalies = _coverage_ledger(countries, agg, comp)
    _write_build_notes(ranking, comp, agg, tau, churn, churn_in, churn_out,
                       coverage, anomalies)

    # --- console summary ---
    print(f"FLSRI BUILD run -- {len(countries)} countries, 13 domains, "
          f"Product-1 = geomean(R, E)")
    print(f"Complete Product-1 composite: {coverage['composite_scored']}/195")
    print(f"Kendall's tau (gov-attenuated vs unattenuated): "
          f"{tau:.4f}" if tau is not None else "tau: n/a")
    print(f"Top-48 churn: {churn:.3f}" if churn is not None else "churn n/a")
    print("Top 10 (composite):")
    for r, iso3, s in ranking[:10]:
        print(f"  {r:>3}  {iso3}  {country_name(iso3):<28} {s:.4f}")
    if anomalies:
        print("ANOMALIES:")
        for a in anomalies:
            print(f"  - {a}")


def _write_scores(countries, agg, comp, rank_map):
    path = os.path.join(OUTDIR, "scores.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "iso3", "country_name", "R_score", "E_score", "composite_score",
            "composite_rank", "low_confidence_flags", "not_scored_flags",
            "governance_modulator_applied",
        ])
        for c in countries:
            obj = agg[c]
            r = obj["phases"]["recruitment"]["score"]
            e = obj["phases"]["exploitation"]["score"]
            cs = comp[c]
            low_conf = [s for s, d in obj["domains"].items()
                        if not d.get("product2_only")
                        and d["scored"]
                        and d["coverage_flag"] in ("low_confidence", "insufficient_data")]
            not_scored = [s for s, d in obj["domains"].items()
                          if not d.get("product2_only") and not d["scored"]]
            w.writerow([
                c, country_name(c),
                _fmt(r), _fmt(e), _fmt(cs),
                rank_map.get(c, ""),
                ",".join(sorted(low_conf)),
                ",".join(sorted(not_scored)),
                obj["governance_applied"],
            ])
    print(f"Wrote {path}")


def _write_rankings(ranking):
    path = os.path.join(OUTDIR, "rankings.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "iso3", "country_name", "composite_score"])
        for r, iso3, s in ranking:
            w.writerow([r, iso3, country_name(iso3), round(s, 6)])
    print(f"Wrote {path}")


def _fmt(v):
    return round(v, 6) if v is not None else ""


def _coverage_ledger(countries, agg, comp):
    ledger = {
        "n_countries": len(countries),
        "composite_scored": sum(1 for c in countries if comp[c] is not None),
        "R_scored": sum(1 for c in countries
                        if agg[c]["phases"]["recruitment"]["score"] is not None),
        "E_scored": sum(1 for c in countries
                        if agg[c]["phases"]["exploitation"]["score"] is not None),
        "governance_applied": sum(1 for c in countries
                                  if agg[c]["governance_applied"]),
    }
    anomalies = []
    for c in countries:
        if comp[c] is not None and comp[c] == 0.0:
            anomalies.append(f"{c}: composite == 0.0 (check missing->0 bug)")
    return ledger, anomalies


def _write_build_notes(ranking, comp, agg, tau, churn, churn_in, churn_out,
                       coverage, anomalies):
    path = os.path.join(OUTDIR, f"build-notes-{TODAY}.md")
    top10 = ranking[:10]
    bottom10 = ranking[-10:]

    # per-domain scored / flag tally across countries
    domain_tally = {}
    for slug in crosswalk.CROSSWALK:
        scored = sum(1 for c in agg if agg[c]["domains"][slug]["scored"])
        flag = next(iter(agg.values()))["domains"][slug]["design_confidence"]
        domain_tally[slug] = (scored, flag)

    lines = []
    lines.append(f"# FLSRI build notes -- {TODAY}\n")
    lines.append("v1 machinery run over the processed data layer. "
                 "Product-1 composite = equal-weight geometric mean of "
                 "Recruitment x Exploitation phase scores. Monetization "
                 "excluded from Product-1 (Product-2 lens only, rule 7). "
                 "Governance enters ONCE as a domain-level attenuate-only "
                 "modulator: domain_score = (1 - f_gov) * domain_raw "
                 "(rule 8b -- this reproduction build only; the published "
                 "v0.4-SPU site build has no (1 - f_gov) term and de-biases "
                 "signals instead, rule 8a). See the resolved polarity note "
                 "below for how f_gov is encoded.\n")

    lines.append("## Governance-modulator polarity -- RESOLVED\n")
    lines.append("The v1 formula reads `f_gov = 1 - "
                 "wb_wgi_rule_of_law`, written assuming that column is a "
                 "GOODNESS encoding (high = strong rule of law). The PROCESSED "
                 "column is RISK-aligned (high = weak governance = more risk; "
                 "verified: SOM 0.94 worst, NOR 0.10 best). Substituting the "
                 "risk-aligned column LITERALLY double-inverts the dial: it "
                 "attenuates the WORST-governed countries hardest (AFG R 0.73 "
                 "-> 0.08), inverts the index's central thesis, and collapses "
                 "face validity (Kendall's tau vs unattenuated ~0.15; top-10 "
                 "becomes Rwanda/Estonia/Botswana).\n")
    lines.append("The methodology describes governance "
                 "as a PROTECTIVE defeater (STRONG governance attenuates risk; "
                 "e.g. Constrained Mobility: 'f_gov rises with rule-of-law "
                 "strength'; Structural Disruption z.1: 'low_risk -> f'). This "
                 "build therefore implements the methodology-consistent PROTECTIVE "
                 "reading: good_gov = 1 - risk_aligned_wgi; f_gov = good_gov; "
                 "domain_score = (1 - f_gov) * raw (weak governance -> little "
                 "attenuation; strong governance -> more). **Resolved "
                 "(2026-06-09): the protective reading is ADOPTED.** It matches "
                 "the methodology prose; the literal formula-as-written reading "
                 "is face-invalid, is NOT used, and is retained below only as "
                 "the sensitivity contrast. Note this entire term exists only "
                 "in this reproduction build -- the published v0.4-SPU site "
                 "build contains no (1 - f_gov) term (it de-biases "
                 "governance-correlated signals instead; docs/METHODS.md §6).\n")

    lines.append("## Rank-stability sensitivity (governance attenuation)\n")
    lines.append("Treatment A (one-modulator) and Treatment B (split) are "
                 "IDENTICAL for Product-1: the financial-integrity second "
                 "component (FATF-ME) lives in Monetization, which never enters "
                 "Product-1, so the split-vs-one distinction has no effect at "
                 "the composite level. The split matters only inside the "
                 "Monetization/Product-2 governance application. The sensitivity "
                 "reported here is therefore the governance-attenuated vs "
                 "unattenuated comparison (the proxy that estimates how much the "
                 "domain-level f_gov attenuation moves Product-1 ranks).\n")
    lines.append(f"- **Kendall's tau (attenuated vs unattenuated ranks):** "
                 f"{tau:.4f}" if tau is not None else "- Kendall's tau: n/a")
    lines.append(f"- **Top-quartile (top-48) churn:** "
                 f"{churn:.3f} "
                 f"({len(churn_in)} enter, {len(churn_out)} leave the top-48 "
                 f"when attenuation is removed)" if churn is not None
                 else "- Top-48 churn: n/a")
    lines.append("")
    lines.append("> **Resolved.** The rank-stability result "
                 "above is a property of this reproduction build only; the "
                 "published v0.4-SPU build replaces the modulator with signal "
                 "de-biasing, which was stress-tested across scope and "
                 "governance-reference choices (Kendall tau >= 0.96 across all "
                 "arms -- docs/METHODS.md §6) and passed the pre-registered "
                 "validation suite on the displayed build "
                 "(docs/validation/validation_results_v2_v04spu_w05.json). The "
                 "surviving corruption/FI component count (FATF-ME vs FSI vs "
                 "the backbone) remains an open data-stage collinearity "
                 "question, tracked in docs/METHODS.md §12.\n")

    lines.append("## Coverage ledger (of 195)\n")
    lines.append(f"- Complete Product-1 composite (R and E both scored): "
                 f"**{coverage['composite_scored']}/195**")
    lines.append(f"- Recruitment phase scored: {coverage['R_scored']}/195")
    lines.append(f"- Exploitation phase scored: {coverage['E_scored']}/195")
    lines.append(f"- Governance modulator applied (wgi present): "
                 f"{coverage['governance_applied']}/195\n")
    lines.append("### Per-domain coverage (countries scored / design confidence)\n")
    lines.append("| Domain | Scored /195 | Design confidence |")
    lines.append("|---|---|---|")
    for slug, (n, flag) in domain_tally.items():
        p2 = " (Product-2 only)" if crosswalk.CROSSWALK[slug].get("product2_only") else ""
        lines.append(f"| {slug}{p2} | {n} | {flag} |")
    lines.append("")

    lines.append("## Face-validity spot-check\n")
    lines.append("**Top 10 (highest Product-1 risk):**\n")
    for r, iso3, s in top10:
        lines.append(f"{r}. {iso3} {country_name(iso3)} -- {s:.4f}")
    lines.append("\n**Bottom 10 (lowest scored Product-1 risk):**\n")
    for r, iso3, s in bottom10:
        lines.append(f"{r}. {iso3} {country_name(iso3)} -- {s:.4f}")
    lines.append("")

    lines.append("## Anomalies\n")
    if anomalies:
        for a in anomalies:
            lines.append(f"- {a}")
    else:
        lines.append("- No composite == 0.0 detected (no missing->0 bug). "
                     "Geometric-mean spine annihilates only on a genuine 0.0 "
                     "phase; none observed.")
    lines.append("")

    lines.append("## Carried domain flags (from the data register; see docs/data-provenance.md)\n")
    for slug, spec in crosswalk.CROSSWALK.items():
        for f in spec.get("flags", []):
            lines.append(f"- **{slug}** [{spec['confidence']}]: {f}")
    lines.append("")

    lines.append("## Build-stage caveats (pending decision)\n")
    lines.append("- **Foreclosed Exit (Structural)** has NO scoreable "
                 "GENERATING signal: its named spine (D1 monopsony/exit-cost) "
                 "is unsourceable, and the only sourced columns are ILOSTAT "
                 "Z-DEFEATERS (protective). The domain is scored here as a "
                 "low-confidence STAND-IN and flagged insufficient_data. "
                 "Standalone-low-confidence vs fold-in is a pending decision. "
                 "This is the "
                 "single biggest construct-validity caveat in Exploitation.")
    lines.append("- **State Production of Unfreedom** runs on 2 of 5 designed "
                 "drivers (insufficient_data) and its D4 input v2xcl_slave "
                 "carries a CIRCULARITY_FLAG (V-Dem freedom-from-forced-labour "
                 "is an outcome/de-facto proxy). Both Exploitation domains other "
                 "than Economic Structure & Demand are sub-floor by design -- "
                 "Exploitation phase scores rest heavily on Economic Structure "
                 "& Demand. Flagged for review.")
    lines.append("- Monetization is mapped but EXCLUDED from Product-1 "
                 "(rule 7); the Domain-B Product-1 retention slice is a "
                 "conditional Phase-2 entrant NOT resolved at build.")
    lines.append("- The operator + corruption-component count is fixed at build per "
                 "the methodology: geometric R x E spine; governance scored once as "
                 "the domain-level attenuator; FATF-ME/FSI as the distinct "
                 "Monetization/Product-2 component. The numeric component-count "
                 "and the material-swing threshold remain open design questions.")
    lines.append("")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
