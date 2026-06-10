"""Shared cache write-back for connector live pulls.

The long-format caches under data/aux/ and data/raw/ (schema:
iso3, country_name, year, series, value) are the offline-reproducibility pins:
a build run with --cache must reproduce the tracked processed layer from them
byte-for-byte. Until 2026-06 live pulls never wrote back, so a cache could only
go stale (and several connectors' --cache paths found no rows at all because
the shared World Bank cache lacked their series).

`upsert_series` replaces exactly the rows for the series codes being written
and preserves every other series in the file — essential because four
connectors share data/aux/worldbank_cache.csv. Each write also stamps
data/aux/cache_manifest.json with the retrieval date, row count, and sha256,
so the vintage report and the input manifest can say when a cache was last
refreshed without touching the cache schema.
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = _REPO_ROOT / "data" / "aux" / "cache_manifest.json"

FIELDS = ["iso3", "country_name", "year", "series", "value"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upsert_series(cache_path, rows_by_code: dict, source_label: str = "") -> Path:
    """Replace the cache rows for the series in `rows_by_code` ({code: [row]},
    rows carrying at least iso3/year/series/value), preserving all other
    series. Rows with a null/empty value are dropped (the reducers skip them
    anyway). Output is sorted (series, iso3, year) so repeated write-backs of
    identical data are byte-stable."""
    cache_path = Path(cache_path)
    # SAFETY GATE: a series with zero non-null incoming values must never
    # replace existing cached rows — an empty 200-response (API hiccup,
    # schema change) would otherwise silently destroy a gitignored pin.
    import sys
    empty = [c for c, rows in rows_by_code.items()
             if not any(r.get("value") not in (None, "") for r in rows)]
    if empty:
        print(f"[raw_cache] REFUSING write-back for series with no non-null "
              f"values (cache rows preserved): {sorted(empty)}", file=sys.stderr)
        rows_by_code = {c: r for c, r in rows_by_code.items() if c not in empty}
        if not rows_by_code:
            return cache_path
    codes = set(rows_by_code)
    kept: list[dict] = []
    name_by_iso3: dict = {}
    if cache_path.exists():
        with open(cache_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("country_name") and row.get("iso3"):
                    name_by_iso3.setdefault(row["iso3"], row["country_name"])
                if row.get("series") not in codes:
                    kept.append({f: row.get(f, "") for f in FIELDS})
    added = 0
    for code, rows in rows_by_code.items():
        for r in rows:
            v = r.get("value")
            if v is None or v == "":
                continue
            iso3 = r.get("iso3", "") or ""
            kept.append({
                "iso3": iso3,
                "country_name": (r.get("country_name") or
                                 name_by_iso3.get(iso3, "")),
                "year": r.get("year", "") or "",
                "series": code,
                "value": v,
            })
            added += 1
    kept.sort(key=lambda r: (r["series"], r["iso3"], str(r["year"])))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(kept)
    stamp(cache_path, source_label or ", ".join(sorted(codes)), len(kept))
    print(f"[raw_cache] upserted {added} rows for {sorted(codes)} -> {cache_path.name} "
          f"({len(kept)} total rows)")
    return cache_path


def stamp(cache_path, source_label: str, n_rows: int) -> None:
    """Record the write in data/aux/cache_manifest.json (sidecar — the cache
    schema itself is never extended)."""
    cache_path = Path(cache_path)
    manifest = {}
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text())
        except json.JSONDecodeError:
            manifest = {}
    manifest[cache_path.name] = {
        "retrieved": datetime.date.today().isoformat(),
        "series": source_label,
        "n_rows": n_rows,
        "sha256": _sha256(cache_path),
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True))
