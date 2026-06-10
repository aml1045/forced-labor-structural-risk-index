#!/usr/bin/env python3
"""Per-indicator vintage / staleness report.

Reads config/data_register.csv (the provenance layer every connector already
writes: years, coverage, flags) and derives the two facts the register does not
carry — acquisition MODE and last-RETRIEVED date — at the report layer:

  mode      from the connector classification (same table as build_all.py);
            the register schema itself is deliberately NOT extended, so the
            offline connector runs stay byte-stable against the tracked fragments.
  retrieved mtime of data/processed/<connector>.csv (when the connector last
            wrote the processed layer).

Outputs:
  outputs/vintage-report-<date>.md             human report (staleness, below-floor)
  outputs/site_data_staging/vintages.json      site data (indicators.html augments
                                               its table from this; graceful no-op
                                               when absent)
"""
import csv
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
from config import site_data_paths as P  # noqa: E402

REGISTER = os.path.join(REPO, "config", "data_register.csv")
FRAG_DIR = os.path.join(REPO, "config", "data_register.d")
PROC_DIR = os.path.join(REPO, "data", "processed")
TODAY = datetime.date.today()

# acquisition mode per connector (mirror of build_all.CONNECTORS semantics)
MODE = {
    "worldbank": "live API (cached)", "age_childhood": "live API (cached)",
    "legal_non_recognition": "live API (cached)", "recruitment_econprecarity": "live API (cached)",
    "unhcr": "live API (cached)", "ilostat": "live API",
    "gender_structuring": "live API", "econ_structure_demand": "live API",
    "monetization_b": "live API (partial cache)", "aux_unctad": "live API (credentialed)",
    "vdem": "manual file", "findex": "manual file", "basel_fatf": "manual file",
    "aux_emdat": "manual file (gated)", "ndgain": "static snapshot",
    "aux_ucdp": "static snapshot", "epr": "static file",
    "static_indices": "static parse", "state_production": "static parse",
    "foreclosed_exit": "via ilostat (verification rows)",
}


def fragment_index():
    """indicator -> connector, from the per-source fragments."""
    idx = {}
    for fn in sorted(os.listdir(FRAG_DIR)):
        if not fn.endswith(".csv"):
            continue
        con = fn[:-4]
        with open(os.path.join(FRAG_DIR, fn), newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                idx[(r["indicator"], r["source"])] = con
    return idx


def main():
    frag = fragment_index()
    with open(REGISTER, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    out_rows = []
    for r in rows:
        con = frag.get((r["indicator"], r["source"]))
        proc = os.path.join(PROC_DIR, f"{con}.csv") if con else None
        retrieved = (datetime.date.fromtimestamp(os.path.getmtime(proc)).isoformat()
                     if proc and os.path.exists(proc) else None)
        try:
            ymax = int(float(r["year_max"])) if r["year_max"] else None
        except ValueError:
            ymax = None
        try:
            ymin = int(float(r["year_min"])) if r["year_min"] else None
        except ValueError:
            ymin = None

        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        flags = r.get("flags") or ""
        # gap/note rows: no numeric country count ('0', '', 'n/a') AND no vintage
        is_gap = (not _num(r.get("countries"))) and ymax is None
        out_rows.append({
            "indicator": r["indicator"],
            "source": r["source"],
            "series_id": r["series_id"],
            "connector": con,
            "mode": MODE.get(con, "n/a" if is_gap else "unknown"),
            "retrieved": retrieved,
            "year_min": ymin,
            "year_max": ymax,
            "staleness_years": (TODAY.year - ymax) if ymax else None,
            "coverage_pct": _num(r.get("coverage_pct")),
            "countries": int(_num(r.get("countries")) or 0),
            "below_floor": "BELOW-COVERAGE-FLOOR" in flags,
            "gap_row": is_gap,
        })

    scored = [r for r in out_rows if not r["gap_row"]]
    stale = sorted((r for r in scored if r["staleness_years"] is not None),
                   key=lambda r: -r["staleness_years"])
    below = [r for r in scored if r["below_floor"]]

    P.ensure_staging()
    vjson = os.path.join(P.STAGING, "vintages.json")
    with open(vjson, "w") as f:
        json.dump({
            "meta": {
                "generated": TODAY.isoformat(),
                "n_indicators": len(scored),
                "n_gap_rows": len(out_rows) - len(scored),
                "note": ("Vintage heterogeneity is disclosed, not smoothed: signals "
                         "measure standing structural conditions, so mixed years are "
                         "carried openly. staleness_years = report year - year_max."),
            },
            "indicators": out_rows,
        }, f, indent=1)
    print(f"wrote {os.path.relpath(vjson, REPO)} ({len(scored)} indicators)")

    md = os.path.join(REPO, "outputs", f"vintage-report-{TODAY.isoformat()}.md")
    with open(md, "w") as f:
        f.write(f"# FLSRI vintage report — {TODAY.isoformat()}\n\n")
        f.write(f"{len(scored)} loaded indicators ({len(out_rows) - len(scored)} "
                f"documented gap/note rows excluded from stats).\n\n")
        f.write("## Stalest indicators (year_max oldest)\n\n")
        f.write("| Indicator | Connector | Years | Stale (yrs) | Coverage % | Mode |\n|---|---|---|---|---|---|\n")
        for r in stale[:15]:
            f.write(f"| {r['indicator']} | {r['connector']} | {r['year_min']}–{r['year_max']} "
                    f"| {r['staleness_years']} | {r['coverage_pct']} | {r['mode']} |\n")
        f.write("\n## Below the 50% coverage floor\n\n")
        f.write("| Indicator | Countries | Coverage % | Years |\n|---|---|---|---|\n")
        for r in below:
            f.write(f"| {r['indicator']} | {r['countries']} | {r['coverage_pct']} "
                    f"| {r['year_min']}–{r['year_max']} |\n")
        f.write("\n## Per-connector retrieval\n\n")
        f.write("| Connector | Mode | Processed-layer written | Indicators |\n|---|---|---|---|\n")
        per = {}
        for r in scored:
            if r["connector"]:
                per.setdefault(r["connector"], {"mode": r["mode"], "retrieved": r["retrieved"], "n": 0})
                per[r["connector"]]["n"] += 1
        for con in sorted(per):
            p = per[con]
            f.write(f"| {con} | {p['mode']} | {p['retrieved']} | {p['n']} |\n")
        f.write("\nMixed vintages are a disclosed property of the index (signals are "
                "standing structural conditions, not a panel); the binding cases are "
                "called out in docs/METHODS.md §7 and on the limitations page.\n")
    print(f"wrote {os.path.relpath(md, REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
