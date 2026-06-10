"""Build the shared raw-population denominator table.

Several connectors (UNHCR displacement, EM-DAT disaster intensity, any
per-capita indicator) need RAW population to compute a per-exposure
absolute-anchored score per Locked Rule 1. The World Bank connector
(pipeline/sources/worldbank.py) pulls SP.POP.TOTL but only emits the
STANDARDIZED 0-1 risk table -- population there is a registered denominator,
not a usable raw column. This utility extracts raw SP.POP.TOTL (most-recent
non-NaN year per country, filtered to the FLSRI 195 sample) and writes it to
the generic shared path the per-capita connectors look for:

    data/aux/worldbank_population.csv   (columns: iso3, population)

Source: the harvested first-effort World Bank cache (vintage 2026-05-28).
Run:  python -m pipeline.build_population
"""

from pathlib import Path
import csv

from pipeline import iso_utils

_REPO_ROOT = Path(__file__).resolve().parent.parent
# Harvested first-effort WB cache (raw SP.POP.TOTL lives here), staged in-repo.
_WB_CACHE = _REPO_ROOT / "data" / "aux" / "worldbank_cache.csv"
OUT_PATH = _REPO_ROOT / "data" / "aux" / "worldbank_population.csv"


def build():
    sample = set(iso_utils.load_sample())
    # most-recent non-NaN SP.POP.TOTL per iso3
    best = {}  # iso3 -> (year, value)
    with open(_WB_CACHE, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("series") != "SP.POP.TOTL":
                continue
            raw = (r.get("value") or "").strip()
            if not raw:
                continue
            try:
                val = float(raw)
                year = int(r.get("year") or 0)
            except ValueError:
                continue
            iso3 = iso_utils.normalize_to_iso3(r.get("iso3") or r.get("country_name"))
            if not iso3 or iso3 not in sample:
                continue
            if iso3 not in best or year > best[iso3][0]:
                best[iso3] = (year, val)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3", "population"])
        for iso3 in sorted(best):
            w.writerow([iso3, int(best[iso3][1])])

    print(f"[build_population] wrote {OUT_PATH}")
    print(f"[build_population] {len(best)}/{len(sample)} countries "
          f"({100*len(best)/len(sample):.1f}%) have raw population")
    return best


if __name__ == "__main__":
    build()
