#!/usr/bin/env python3
"""
FLSRI — Monte Carlo rank-robustness report (standalone analysis wrapper).

The noise model and the band computation live in
pipeline/6_site_data/uncertainty.py — the same module the site-data build uses
to attach rank bands to public/data/scores.json — so this report and the
shipped bands can never drift apart.

Usage:
    python monte_carlo.py [--scores PATH]

--scores defaults to the published public/data/scores.json; point it at
outputs/site_data_staging/scores.json to report on a staged build.
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "pipeline", "6_site_data"))
from uncertainty import compute_uncertainty  # noqa: E402

OUT_CSV = os.path.join(HERE, "monte_carlo_results.csv")
OUT_JSON = os.path.join(HERE, "monte_carlo_summary.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores",
                    default=os.path.join(REPO, "public", "data", "scores.json"))
    args = ap.parse_args()

    d = json.load(open(args.scores))
    cs = d["countries"] if isinstance(d, dict) and "countries" in d else d
    scored = [c for c in cs
              if c.get("scored") and c.get("R") is not None and c.get("E") is not None]
    per_iso, summary = compute_uncertainty(scored)
    summary["scores_file"] = os.path.relpath(args.scores, REPO)

    json.dump(summary, open(OUT_JSON, "w"), indent=2)
    by_rank = sorted(scored, key=lambda c: c.get("rank") or 10**9)
    with open(OUT_CSV, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["iso3", "name", "composite", "rank", "mc_rank_median",
                      "mc_rank_p05", "mc_rank_p95", "tier_stability"])
        for c in by_rank:
            u = per_iso[c["iso3"]]
            wtr.writerow([c["iso3"], c["name"], round(float(c["composite"]), 4),
                          c.get("rank"), u["rank_p50"], u["rank_p5"], u["rank_p95"],
                          u["tier_stability"]])

    print("=== FLSRI Monte Carlo rank robustness ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\nwrote {os.path.basename(OUT_CSV)} + {os.path.basename(OUT_JSON)}")
    print("\n  country            rank   90% rank interval   tier-stability")
    for c in by_rank[:5] + by_rank[90:93]:
        u = per_iso[c["iso3"]]
        print(f"  {c['name'][:18]:18} {c.get('rank') or '-':>4}    "
              f"[{u['rank_p5']:3}, {u['rank_p95']:3}]            "
              f"{u['tier_stability']:.2f}")


if __name__ == "__main__":
    main()
