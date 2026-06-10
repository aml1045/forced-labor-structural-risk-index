#!/usr/bin/env python3
"""FLSRI — Monte-Carlo rank-uncertainty, computed inside the site-data build.

Single source of truth for the Monte-Carlo noise model. The site's per-country rank bands
(p5/p50/p95) and tier-stability are computed here during build_scores_json();
outputs/monte_carlo.py imports this module for the standalone analysis report,
so the shipped bands and the analysis can never drift apart.

Noise model (documented, defensible — results depend on it, so it is stated openly):
  - Each country's R and E get additive Gaussian noise, clipped to [0,1].
  - sd is LARGER where the composite rests on a structurally reduced evidence
    base: sd = SD_NORMAL (0.04) baseline, SD_LOWCONF (0.07) where
    domains_not_scored >= LOWCONF_MIN_NOT_SCORED (2 of the 11 Product-1
    domains unscored). This is the SAME rule that drives the site's
    "lower confidence" badge, so badge and band are one statement.
    (An earlier standalone script keyed the wider sd off the `low_conf` flag
    list, which is non-empty for every scored country — a bug that silently
    applied 0.07 everywhere. Fixed here, 2026-06-09.)
  - The R-vs-E weight is drawn Uniform(0.40, 0.60) each iteration (the
    soft-conjunctive operator's weighting is a modelling choice, not a
    measured fact).
No data is fabricated: baseline R/E/composite come from the published build.
"""
import numpy as np

N_ITER = 10000
SEED = 20260602
SD_NORMAL = 0.04
SD_LOWCONF = 0.07
WEIGHT_RANGE = (0.40, 0.60)
TIER_CUTS = (0.281, 0.402)  # site tier bands: Higher >= .402, Middle .281-.402
LOWCONF_MIN_NOT_SCORED = 2
LOWCONF_RULE = "domains_not_scored >= 2"


def is_low_confidence(country):
    """The single rule behind both the wider noise sd and the site badge."""
    return int(country.get("domains_not_scored") or 0) >= LOWCONF_MIN_NOT_SCORED


def compute_uncertainty(countries, n_iter=N_ITER, seed=SEED,
                        sd_normal=SD_NORMAL, sd_lowconf=SD_LOWCONF,
                        weight_range=WEIGHT_RANGE, tier_cuts=TIER_CUTS):
    """Monte-Carlo rank bands for the scored countries.

    countries: list of dicts with iso3 / R / E / scored / domains_not_scored
    (the build_scores_json country records).

    Returns (per_iso, summary):
      per_iso : {iso3: {"rank_p5", "rank_p50", "rank_p95", "tier_stability"}}
                ranks are ints, 1 = highest risk; tier_stability in [0, 1]
      summary : the model statement + headline stability numbers, suitable for
                shipping verbatim as scores.json meta.uncertainty
    """
    scored = [c for c in countries
              if c.get("scored") and c.get("R") is not None and c.get("E") is not None]
    n = len(scored)
    iso = [c["iso3"] for c in scored]
    R0 = np.array([c["R"] for c in scored], float)
    E0 = np.array([c["E"] for c in scored], float)
    comp0 = R0 ** 0.5 * E0 ** 0.5
    base_rank = (-comp0).argsort().argsort() + 1            # 1 = highest risk
    lowconf = np.array([is_low_confidence(c) for c in scored])
    sd = np.where(lowconf, sd_lowconf, sd_normal)

    lo_cut, hi_cut = tier_cuts

    def tier(v):
        return np.where(v >= hi_cut, 2, np.where(v >= lo_cut, 1, 0))

    base_tier = tier(comp0)

    rng = np.random.default_rng(seed)
    Rp = np.clip(R0 + rng.normal(0, sd, (n_iter, n)), 1e-4, 1.0)
    Ep = np.clip(E0 + rng.normal(0, sd, (n_iter, n)), 1e-4, 1.0)
    w = rng.uniform(*weight_range, (n_iter, 1))
    comp = Rp ** w * Ep ** (1 - w)                          # weighted geometric mean

    order = (-comp).argsort(axis=1)
    ranks = np.empty((n_iter, n), int)
    rows = np.arange(n_iter)[:, None]
    ranks[rows, order] = np.arange(1, n + 1)[None, :]       # rank per country per iter

    rank_p05 = np.percentile(ranks, 5, axis=0)
    rank_p50 = np.median(ranks, axis=0)
    rank_p95 = np.percentile(ranks, 95, axis=0)
    tier_sim = tier(comp)
    tier_stab = (tier_sim == base_tier[None, :]).mean(axis=0)

    # overall stability: Spearman of each iter's ranking vs baseline (rank-rank Pearson)
    br = base_rank.astype(float); br -= br.mean()
    rr = ranks.astype(float); rr -= rr.mean(axis=1, keepdims=True)
    spear = (rr @ br) / (np.sqrt((rr ** 2).sum(axis=1)) * np.sqrt((br ** 2).sum()))

    def retain(k):
        base_top = set(np.argsort(base_rank)[:k])
        frac = [len(base_top & set(np.argsort(ranks[i])[:k])) / k
                for i in range(0, n_iter, 20)]
        return float(np.mean(frac))

    def _clamp_rank(x):
        return int(min(max(round(float(x)), 1), n))

    per_iso = {}
    for i in range(n):
        per_iso[iso[i]] = {
            "rank_p5": _clamp_rank(rank_p05[i]),
            "rank_p50": _clamp_rank(rank_p50[i]),
            "rank_p95": _clamp_rank(rank_p95[i]),
            "tier_stability": round(float(tier_stab[i]), 3),
        }

    band_widths = rank_p95 - rank_p05
    summary = {
        "method": ("Monte-Carlo perturbation of the published build: additive "
                   "Gaussian noise on R and E (clipped to [0,1]) and a "
                   "re-drawn R-vs-E weight each iteration; the field is "
                   "re-ranked per draw. Bands are the 5th-95th percentile of "
                   "each country's simulated rank."),
        "iterations": int(n_iter),
        "n_countries": int(n),
        "seed": int(seed),
        "noise_sd": sd_normal,
        "noise_sd_low_confidence": sd_lowconf,
        "low_confidence_rule": LOWCONF_RULE,
        "n_low_confidence": int(lowconf.sum()),
        "weight_range": list(weight_range),
        "tier_cuts": list(tier_cuts),
        "spearman_vs_baseline_median": round(float(np.median(spear)), 4),
        "spearman_vs_baseline_p05": round(float(np.percentile(spear, 5)), 4),
        "top10_retention": round(retain(10), 3),
        "top_decile_retention": round(retain(max(1, round(n / 10))), 3),
        "tier_stability_mean": round(float(tier_stab.mean()), 3),
        "median_band_width": round(float(np.median(band_widths)), 1),
        "share_band_within_10_ranks": round(float((band_widths <= 10).mean()), 3),
    }
    return per_iso, summary


if __name__ == "__main__":
    import json
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.abspath(os.path.join(here, "..", ".."))
    scores = json.load(open(os.path.join(repo, "public", "data", "scores.json")))
    per_iso, summary = compute_uncertainty(scores["countries"])
    print(json.dumps(summary, indent=2))
