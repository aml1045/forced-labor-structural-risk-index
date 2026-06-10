"""V-Dem Country-Year Core v16 connector for FLSRI.

Loads selected V-Dem variables from the local CC-BY-4.0 zip, normalizes the
V-Dem country code to ISO3, takes the most-recent non-null country-year per
variable, standardizes each to a 0-1 risk scale against fixed absolute anchors
(the variables are V-Dem interval indices on [0,1], so the anchors are the
index's own scale endpoints), and writes:

  - data/processed/vdem.csv          (iso3 + one 0-1 column per variable)
  - config/data_register.d/vdem.csv  (per-variable provenance + coverage fragment)

Follows the v1 scoring rules (docs/scoring-rules.md):
  Rule 1 — 0-1 absolute-anchored scale (here: the V-Dem index endpoints).
  Rule 9 — drop-and-re-average / coverage floor handled by standardize; missing
           countries stay None, NEVER -> 0; below-floor variables get flagged.
  Direction — set explicitly per variable in config/api-config/vdem.yaml.

DIRECTION (verified against the v16 codebook):
  v2xcl_dmove / v2xcl_slave / v2x_rule : higher = freer/better -> low_risk (invert)
  v2xnp_client                         : V-Dem REVERSED this index, higher = MORE
                                         clientelism = worse -> high_risk (no invert)

Reuses pipeline.iso_utils (normalize_to_iso3, load_sample), pipeline.standardize
(AnchorSpec, anchor_scale), and pipeline.register (upsert_rows). Nothing
re-derived. Modeled on pipeline/sources/_core_smoke.py.

Run:  python -m pipeline.sources.vdem
"""

from pathlib import Path
import csv
import io
import zipfile

import yaml

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PROJECT_ROOT = _REPO_ROOT  # raw inputs resolve repo-relative under data/raw/

CONFIG_PATH = _REPO_ROOT / "config" / "api-config" / "vdem.yaml"
OUT_PATH = _REPO_ROOT / "data" / "processed" / "vdem.csv"
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "vdem.csv"

# Local CC-BY V-Dem Core v16 zip (staged in-repo under data/raw/).
ZIP_PATH = (
    _PROJECT_ROOT
    / "data" / "raw"
    / "V-Dem-CY-Core-v16_csv.zip"
)


def load_config(path=None):
    p = Path(path) if path else CONFIG_PATH
    with open(p, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load(cfg, zip_path=None):
    """Return ({code: {iso3: most_recent_value}}, year_min, year_max, unmatched).

    Reads ONLY the id columns + selected variable columns from the Core CSV
    (which has ~1908 columns / ~200 MB uncompressed) — never loads the full
    frame. Takes the most-recent non-null country-year per (variable, country).
    """
    zp = Path(zip_path) if zip_path else ZIP_PATH
    var_codes = [v["code"] for v in cfg["variables"]]
    member = cfg["zip_member"]
    win_min = int(cfg.get("year_window_min", 0))

    # per-variable: iso3 -> (year, value) for the most-recent non-null
    latest = {code: {} for code in var_codes}
    unmatched = {}  # raw code/name -> count, for V-Dem ids that don't map to ISO3
    year_min, year_max = None, None

    with zipfile.ZipFile(zp) as z:
        with z.open(member) as fh:
            reader = csv.reader(io.TextIOWrapper(fh, encoding="utf-8"))
            header = next(reader)
            idx = {h: i for i, h in enumerate(header)}
            for c in ("country_text_id", "country_name", "year"):
                if c not in idx:
                    raise KeyError(f"V-Dem CSV missing id column {c!r}")
            for code in var_codes:
                if code not in idx:
                    raise KeyError(
                        f"V-Dem variable {code!r} NOT in Core file columns "
                        "— do not fabricate; flag as Full-only/absent."
                    )
            cti, cni, yi = idx["country_text_id"], idx["country_name"], idx["year"]
            vidx = {code: idx[code] for code in var_codes}

            for row in reader:
                try:
                    year = int(row[yi])
                except (ValueError, IndexError):
                    continue
                if year < win_min:
                    continue

                iso3 = iso_utils.normalize_to_iso3(row[cti])
                if not iso3:
                    iso3 = iso_utils.normalize_to_iso3(row[cni])
                if not iso3:
                    key = f"{row[cti]} ({row[cni]})"
                    unmatched[key] = unmatched.get(key, 0) + 1
                    continue

                row_had_value = False
                for code in var_codes:
                    raw = row[vidx[code]]
                    if raw == "" or raw is None:
                        continue
                    try:
                        val = float(raw)
                    except ValueError:
                        continue
                    cur = latest[code].get(iso3)
                    if cur is None or year > cur[0]:
                        latest[code][iso3] = (year, val)
                    row_had_value = True

                if row_had_value:
                    year_min = year if year_min is None else min(year_min, year)
                    year_max = year if year_max is None else max(year_max, year)

    raw_by_code = {
        code: {iso3: yv[1] for iso3, yv in d.items()} for code, d in latest.items()
    }
    return raw_by_code, year_min, year_max, unmatched


def run():
    cfg = load_config()
    countries = iso_utils.load_sample()
    raw_by_code, year_min, year_max, unmatched = load(cfg)

    results = {}      # code -> ScaleResult
    for var in cfg["variables"]:
        code = var["code"]
        spec = AnchorSpec(
            indicator=code,
            floor=float(var["floor"]),
            ceiling=float(var["ceiling"]),
            direction=var["direction"],
            unit=var["scale"],
            anchor_source=(
                f"V-Dem v16 interval index endpoints [{var['floor']},{var['ceiling']}] "
                f"— {var['label']}"
            ),
        )
        results[code] = anchor_scale(raw_by_code[code], spec, sample=countries)

    # --- write data/processed/vdem.csv (iso3 + one column per variable) -----
    var_codes = [v["code"] for v in cfg["variables"]]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3"] + var_codes)
        for iso3 in countries:
            rowvals = []
            for code in var_codes:
                v = results[code].get(iso3)
                rowvals.append("" if v is None else round(v, 4))
            w.writerow([iso3] + rowvals)

    # --- register provenance + coverage fragment ---------------------------
    unmatched_flag = None
    if unmatched:
        items = ", ".join(f"{k}" for k in sorted(unmatched))
        unmatched_flag = f"V-Dem codes unmatched to ISO3 (dropped, non-FLSRI sub-units): {items}"

    rows = []
    for code in var_codes:
        res = results[code]
        extra = [unmatched_flag] if unmatched_flag else None
        row = res.register_row(
            source=cfg["source"], series_id=code, license=cfg["license"],
            extra_flags=extra,
        )
        row["year_min"], row["year_max"] = year_min, year_max
        rows.append(row)
    register.upsert_rows(rows, path=str(FRAGMENT_PATH))

    # --- report ------------------------------------------------------------
    print(f"[vdem] wrote {OUT_PATH}")
    print(f"[vdem] year range {year_min}-{year_max}; fragment -> {FRAGMENT_PATH}")
    if unmatched:
        print(f"[vdem] UNMATCHED V-Dem codes (dropped): {sorted(unmatched)}")
    for var in cfg["variables"]:
        code = var["code"]
        m = results[code].meta
        print(
            f"[vdem] {code:14s} dir={m['direction']:9s} "
            f"coverage {m['coverage_pct']:.1f}% ({m['n_present']}/{m['n_total']}) "
            f"below_floor={m['below_floor']} anchor={m['anchor']}"
        )
        if m["flags"]:
            print(f"         flags: {m['flags']}")
    return results


if __name__ == "__main__":
    run()
