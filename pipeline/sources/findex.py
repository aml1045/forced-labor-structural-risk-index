"""World Bank Global Findex 2025 connector for FLSRI (Layer-2 DATA-MAP).

DOMAIN: Recruitment / Debt & Financialized Dependency.
Fills the two GAP signals the Layer-2 crosswalk flagged for this domain
(see docs/METHODS.md):
  - Findex credit access / microfinance penetration
  - household over-indebtedness / advance-debt-financing proxy
(The c_A mobility-foreclosure gate was dropped by design decision --
the mobility construct is scored ONCE in Constrained Mobility; NOT re-mapped
here. Debt is cross-cutting: only its Recruitment form is scored here, not the
Exploitation/Monetization forms.)

SOURCE : World Bank Global Findex Database 2025 (calendar-year 2024 wave; ~148k
         adults, 141 economies). Reused as-is (GlobalFindexDatabase2025.csv
         direct upload, vintage 2026-05-28). No API/auth needed -- the survey
         microdata are a static CSV.
LICENSE: CC BY 3.0 IGO (World Bank open data; attribution required) -- publish-safe.

The aggregate file is long: one row per (country x year x demographic group).
We keep group=='all' (national totals) only, take the most-recent wave per
country (preferring 2024), normalize the WB code to ISO3, build each signal's
per-exposure share, and standardize to 0-1 against absolute [0,1] share anchors.

Three signals (config/api-config/findex.yaml):
  findex_account_exclusion   1 - account_t_d            -> high_risk  (financial exclusion)
  findex_informal_borrowing  fin22b + fin22c (clamped)  -> high_risk  (informal-lender reliance, R-D2)
  findex_borrow_prevalence   borrow_any_t_d             -> high_risk  (over-indebtedness PROXY, R-D3)

Follows the shared connector shape (pipeline/sources/_core_smoke.py) and reuses
iso_utils (normalize_to_iso3, load_sample), standardize (AnchorSpec,
anchor_scale), and register (upsert_rows) -- never re-deriving any of them.

COVERAGE NOTE (surfaced for review): account ownership is asked of every
economy (~137 in the FLSRI 195 sample); the borrowing-SOURCE detail module
(fin22*, borrow_any) is NOT asked in many high-income economies, so the two
borrowing signals cover materially fewer countries -- they are flagged
low-confidence where they fall below the coverage floor. Never imputed to 0.

Run:  python -m pipeline.sources.findex
"""

from __future__ import annotations

from pathlib import Path
import csv

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PROJECT_ROOT = _REPO_ROOT  # raw inputs resolve repo-relative under data/raw/
CONFIG_PATH = _REPO_ROOT / "config" / "api-config" / "findex.yaml"
OUT_PATH = _REPO_ROOT / "data" / "processed" / "findex.csv"
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "findex.csv"

_NA_TOKENS = {"", "NA", "N/A", "na", "null", "NULL", "."}


def _load_config():
    if not _HAS_YAML:
        raise RuntimeError(
            "PyYAML is required to read config/api-config/findex.yaml "
            "(pip install pyyaml)."
        )
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _resolve_source_file(rel_path):
    candidate = _PROJECT_ROOT / rel_path
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"Findex source CSV not found: {candidate}\n(rel_path={rel_path!r})"
    )


def _to_float(raw):
    if raw is None:
        return None
    s = str(raw).strip()
    if s in _NA_TOKENS:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _select_national_rows(path, cfg):
    """Return {iso3: row_dict} keeping group=='all' national totals, most-recent
    wave per country (preferring cfg['preferred_year']). ISO3-normalized."""
    group_col = cfg["group_col"]
    group_all = cfg["group_all"]
    year_col = cfg["year_col"]
    iso_col = cfg["iso_col"]
    name_col = cfg["name_col"]
    pref = str(cfg.get("preferred_year", "")).strip()

    best = {}  # iso3 -> (year_int, row, is_pref)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if (row.get(group_col) or "").strip() != group_all:
                continue
            iso3 = (iso_utils.normalize_to_iso3(row.get(iso_col))
                    or iso_utils.normalize_to_iso3(row.get(name_col)))
            if not iso3:
                continue
            yr_raw = (row.get(year_col) or "").strip()
            try:
                yr = int(yr_raw)
            except ValueError:
                continue
            is_pref = (yr_raw == pref)
            cur = best.get(iso3)
            # Prefer the configured wave outright; else most-recent year.
            if cur is None:
                best[iso3] = (yr, row, is_pref)
            else:
                cyr, _, cpref = cur
                if (is_pref and not cpref) or (is_pref == cpref and yr > cyr):
                    best[iso3] = (yr, row, is_pref)
    return best


def _signal_values(best_rows, sig):
    """Build {iso3: per-exposure share} for one signal spec. Missing -> absent
    (never 0). Applies complement / add_column transforms, clamps to [0,1]."""
    col = sig["column"]
    add_col = sig.get("add_column")
    transform = sig.get("transform")
    out = {}
    for iso3, (_, row, _) in best_rows.items():
        v = _to_float(row.get(col))
        if v is None:
            continue
        if add_col:
            a = _to_float(row.get(add_col))
            # if the secondary source is missing, fall back to the primary only
            v = v + a if a is not None else v
        if transform == "complement":
            v = 1.0 - v
        # clamp to the share range here too (sum-of-shares can exceed 1)
        v = max(0.0, min(1.0, v))
        out[iso3] = v
    return out


def run():
    cfg = _load_config()
    src_path = _resolve_source_file(cfg["source_file"])
    countries = iso_utils.load_sample()
    source = cfg["source"]
    license_ = cfg["license"]
    series_file = Path(cfg["source_file"]).name

    best_rows = _select_national_rows(src_path, cfg)
    years = sorted({y for (y, _, _) in best_rows.values()})
    ymin = years[0] if years else ""
    ymax = years[-1] if years else ""
    print(f"[findex] national-total rows mapped to ISO3: {len(best_rows)} "
          f"(years {ymin}-{ymax})")

    columns = []        # ordered output indicator columns
    scored_cols = {}    # indicator -> ScaleResult
    register_rows = []

    pref_year = str(cfg.get("preferred_year", "")).strip()

    for ind_name, sig in cfg["signals"].items():
        raw = _signal_values(best_rows, sig)
        # how many of THIS signal's in-sample countries come from a pre-preferred
        # wave (the fall-back-to-most-recent vintage staleness, surfaced).
        n_stale = sum(
            1 for iso3 in raw
            if iso3 in countries and pref_year
            and str(best_rows[iso3][0]) != pref_year
        )
        spec = AnchorSpec(
            indicator=ind_name,
            floor=float(sig["floor"]),
            ceiling=float(sig["ceiling"]),
            direction=sig["direction"],
            unit=sig.get("unit", ""),
            anchor_source=sig.get("anchor_source", ""),
        )
        result = anchor_scale(raw, spec, sample=countries)
        scored_cols[ind_name] = result
        columns.append(ind_name)

        extra = []
        if n_stale:
            extra.append(
                f"VINTAGE-MIX: {n_stale} country values fall back to a pre-{pref_year} "
                f"wave (latest available per country, 2011-{pref_year}); "
                "the rest are 2024 -- re-examine staleness at data-stage review"
            )
        # surface the proxy / overlap notes into the flag stream for the gate
        if ind_name == "findex_borrow_prevalence":
            extra.append(
                "PROXY: standing borrowing prevalence, NOT a debt-service-distress "
                "(DSTI) measure -- Findex has no over-indebtedness series; "
                "low-confidence direction, surfaced for review"
            )
        if ind_name == "findex_informal_borrowing":
            extra.append(
                "UPPER-BOUND: fin22b + fin22c summed and clamped to [0,1] "
                "(a person can use both informal sources) -- re-examine ceiling "
                "vs observed distribution at data-stage review"
            )

        row = result.register_row(
            source=source,
            series_id=f"{series_file}:{sig['column']}"
                      + (f"+{sig['add_column']}" if sig.get("add_column") else "")
                      + ("(1-x)" if sig.get("transform") == "complement" else ""),
            license=license_,
            extra_flags=extra or None,
        )
        row["year_min"], row["year_max"] = ymin, ymax
        register_rows.append(row)

        m = result.meta
        print(f"[findex] {ind_name}: dir={m['direction']} anchor={m['anchor']} "
              f"coverage {m['coverage_pct']:.1f}% ({m['n_present']}/{m['n_total']}), "
              f"below_floor={m['below_floor']}")

    # --- write data/processed/findex.csv (iso3 + one col per signal) ----------
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3"] + columns)
        for iso3 in countries:
            line = [iso3]
            for col in columns:
                v = scored_cols[col].get(iso3)
                line.append("" if v is None else round(v, 4))
            w.writerow(line)

    # --- per-source register fragment (merged downstream) --------------------
    register.ensure_header(path=str(FRAGMENT_PATH))
    register.upsert_rows(register_rows, path=str(FRAGMENT_PATH))

    print(f"\n[findex] wrote {OUT_PATH} "
          f"({len(countries)} rows x {len(columns)} signals)")
    print(f"[findex] wrote register fragment {FRAGMENT_PATH} "
          f"({len(register_rows)} rows)")
    return scored_cols, register_rows


if __name__ == "__main__":
    run()
