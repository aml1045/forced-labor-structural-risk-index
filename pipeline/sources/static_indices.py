"""STATIC indices connector -- three manually-parsed indices.

Loads the three already-parsed static indices (reused as-is), normalizes to ISO3,
standardizes each chosen indicator to 0-1 against fixed absolute anchors with an
explicit direction, writes data/processed/static_indices.csv, and records
provenance + coverage to a per-source register fragment.

Indices:
  - TRACE Bribery Risk Matrix 2024  (Total Score 0-100, high_risk)
  - Basel AML Index 2025            (NO LIVE ROWS -- design decision: overall +
                                     AML/CFT-Framework composite both dropped as
                                     circular; intended FATF-ME / FSI signals are
                                     pending data-stage extraction from the
                                     Expert Edition)
  - Henley Passport Index May 2026  (Visa Free count, low_risk -> inverted)

This module copies the shape of pipeline/sources/_core_smoke.py: it IMPORTS and
REUSES iso_utils (normalize_to_iso3, load_sample), standardize (AnchorSpec,
anchor_scale), and register (upsert_rows) -- it never re-derives any of them.

LICENSE FLAGS (surfaced for review):
  TRACE  : public-release license UNCONFIRMED -> LICENSE-PENDING
  Henley : public-release license UNCONFIRMED -> LICENSE-PENDING
  Basel  : open with citation -> OK

Config:    config/api-config/static_indices.yaml
Run:       python -m pipeline.sources.static_indices
"""

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
CONFIG_PATH = _REPO_ROOT / "config" / "api-config" / "static_indices.yaml"
OUT_PATH = _REPO_ROOT / "data" / "processed" / "static_indices.csv"
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "static_indices.csv"


def _load_config():
    if not _HAS_YAML:
        raise RuntimeError(
            "PyYAML is required to read config/api-config/static_indices.yaml "
            "(pip install pyyaml)."
        )
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _resolve_source_file(load_dir, source_file):
    """Resolve a parsed-CSV path: try absolute under project root, else under repo."""
    candidate = _PROJECT_ROOT / load_dir / source_file
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"Parsed source CSV not found: {candidate}\n"
        f"(load_dir={load_dir!r}, source_file={source_file!r})"
    )


def _read_csv_column(path, iso_col, value_col):
    """Return {iso3: float_value} from a parsed CSV, normalizing the ISO column.

    Rows whose value is blank/non-numeric are dropped (stay missing, never -> 0).
    """
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if value_col not in reader.fieldnames:
            raise KeyError(
                f"Column {value_col!r} not in {path.name}; "
                f"have {reader.fieldnames}"
            )
        for row in reader:
            iso3 = iso_utils.normalize_to_iso3(row.get(iso_col))
            if not iso3:
                continue
            raw = (row.get(value_col) or "").strip()
            if raw == "":
                continue
            try:
                out[iso3] = float(raw)
            except ValueError:
                continue
    return out


def _spec_from(entry):
    """Build an AnchorSpec from a YAML headline/subscore entry dict."""
    return AnchorSpec(
        indicator=entry["output_col"],
        floor=float(entry["floor"]),
        ceiling=float(entry["ceiling"]),
        direction=entry["direction"],
        unit=entry.get("unit", ""),
        anchor_source=entry.get("anchor_source", ""),
    )


def run():
    cfg = _load_config()
    load_dir = cfg["_load_dir"]
    countries = iso_utils.load_sample()

    # ordered list of (output_col, ScaleResult, register_row-context)
    columns = []  # [(output_col, result)]
    register_rows = []

    for key, idx in cfg["indices"].items():
        src_path = _resolve_source_file(load_dir, idx["source_file"])
        iso_col = idx["iso_col"]
        source = idx["title"]
        license_ = idx["license"]
        series_file = idx["source_file"]
        ymin, ymax = idx["year_min"], idx["year_max"]

        # surface a LICENSE-PENDING flag in the flag stream (not just the
        # license column) so it is prominent in review.
        # TRACE + Henley public-release license is UNCONFIRMED.
        license_flags = []
        if "RE-PUBLICATION-UNCONFIRMED" in license_ or "LICENSE-PENDING" in license_:
            license_flags.append(
                f"RE-PUBLICATION-UNCONFIRMED (design decision): {source} public-release "
                "re-publication rights UNCONFIRMED -- confirm before public "
                "release; NOT blocking now"
            )

        # headline indicator + any kept sub-scores. headline is optional: an
        # index may be configured with NO live entries (e.g. Basel AML, whose
        # ingested signals are pending data-stage extraction per design decision)
        # -- in that case it contributes no rows and no output column.
        headline = idx.get("headline")
        entries = ([headline] if headline else []) + list(idx.get("subscores", []) or [])
        for entry in entries:
            raw = _read_csv_column(src_path, iso_col, entry["column"])
            spec = _spec_from(entry)
            result = anchor_scale(raw, spec, sample=countries)
            columns.append((entry["output_col"], result))

            row = result.register_row(
                source=source,
                series_id=f"{series_file}:{entry['column']}",
                license=license_,
                extra_flags=license_flags or None,
            )
            row["year_min"], row["year_max"] = ymin, ymax
            register_rows.append(row)

            print(
                f"[static_indices] {entry['output_col']}: "
                f"dir={result.meta['direction']} anchor={result.meta['anchor']} "
                f"coverage={result.meta['coverage_pct']:.1f}% "
                f"({result.meta['n_present']}/{result.meta['n_total']}) "
                f"below_floor={result.meta['below_floor']}"
            )

    # --- write data/processed/static_indices.csv (iso3 + one col per indicator) --
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = ["iso3"] + [c for c, _ in columns]
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for iso3 in countries:
            row = [iso3]
            for _, result in columns:
                v = result.get(iso3)
                row.append("" if v is None else round(v, 4))
            w.writerow(row)

    # --- write per-source register fragment (NOT the master register) ----------
    register.upsert_rows(register_rows, path=str(FRAGMENT_PATH))

    print(f"[static_indices] wrote {OUT_PATH}")
    print(f"[static_indices] wrote fragment {FRAGMENT_PATH} "
          f"({len(register_rows)} indicator rows)")
    return columns, register_rows


if __name__ == "__main__":
    run()
