"""Merge the per-source register fragments into the master data register.

Each source connector writes provenance to its OWN fragment at
config/data_register.d/<source>.csv (via register.upsert_rows(rows, path=...)),
so concurrent writers never race on the shared file. This utility folds every
fragment into the master config/data_register.csv on the fixed column order,
keyed idempotently by (indicator, source). Re-runnable.

The synthetic smoke-test row is dropped from the master once real sources
exist (the smoke module/csv stay as test scaffolding).

Run:  python -m pipeline.merge_register
"""

from pathlib import Path
import csv

from pipeline import register

_REPO_ROOT = Path(__file__).resolve().parent.parent
FRAG_DIR = _REPO_ROOT / "config" / "data_register.d"
MASTER = _REPO_ROOT / "config" / "data_register.csv"

_DROP = {("_core_smoke", "core smoke (synthetic)")}  # synthetic demo


def merge():
    rows = []
    frags = sorted(FRAG_DIR.glob("*.csv"))
    for f in frags:
        with open(f, newline="", encoding="utf-8") as fh:
            frag_rows = [r for r in csv.DictReader(fh)]
        rows.extend(frag_rows)
        print(f"[merge] {f.name}: {len(frag_rows)} rows")

    # idempotent on (indicator, source); fragments authoritative; drop demo
    seen, out = set(), []
    for r in rows:
        key = (r.get("indicator"), r.get("source"))
        if key in _DROP or key in seen:
            continue
        seen.add(key)
        out.append({c: r.get(c, "") for c in register.COLUMNS})
    out.sort(key=lambda r: (str(r["source"]), str(r["indicator"])))

    with open(MASTER, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=register.COLUMNS)
        w.writeheader()
        w.writerows(out)

    print(f"[merge] wrote {MASTER}: {len(out)} indicator rows "
          f"from {len(frags)} source fragments")
    return out


if __name__ == "__main__":
    merge()
