#!/usr/bin/env python3
"""Verify outputs/site_data_staging/ — the gate between a build and publication.

Three layers of checking, reported per file:

1. Baseline diff (needs FLSRI_BASELINE_DATA or ./_baseline/data): sha256 PASS,
   or a characterized DELTA (schema / value / magnitude) so an immaterial
   KDE/mapshaper geometry delta is visibly distinguished from a real bug.
   scores.json carries SANCTIONED divergences from the frozen baseline:
     - the PLW/AND not_scored correction (design decision), and
     - the 2026-06 schema ADDITIONS (rank bands + result class + meta keys);
   any change to a pre-existing value outside that set still FAILs.
2. Baseline-independent invariants: required files present, scores.json schema
   and counts coherent, Monte-Carlo band self-checks (ordering, badge/noise
   rule consistency, no bands on unscored countries).
3. Regression vs the committed reference summary
   (docs/validation/verify_summary_reference.json): fail on country-count or
   scored-count drops. --write-reference refreshes it after a reviewed change.

Exit code: 0 = ok. Non-zero on FAIL/BLOCKED, or (with --strict) on any
unsanctioned schema drift, out-of-tolerance numeric delta, or invariant breach.
"""
import argparse
import os
import sys
import json
import hashlib

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from config import site_data_paths as P  # noqa: E402

# Frozen published baseline to diff staging against. NOT bundled; set
# FLSRI_BASELINE_DATA to a local directory holding the baseline data files.
BASELINE = os.environ.get(
    "FLSRI_BASELINE_DATA",
    os.path.join(_HERE, "_baseline", "data"))

REFERENCE_SUMMARY = os.path.join(_HERE, "docs", "validation",
                                 "verify_summary_reference.json")

FILES = [
    "scores.json", "domains.json", "divergence.json", "subnational.json",
    "overlay.json", "lisa.json", "lisa_admin1.json",
    "admin1_risk.topojson", "lisa_admin1.topojson",
]

# numeric tolerance for non-scores JSON deltas under --strict
NUM_TOLERANCE = 1e-9


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _flatten(obj, prefix=""):
    """yield (path, value) for every leaf in a json object."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, f"{prefix}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flatten(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


# overlay.json: the published file carries the add_remittance_outflows.py
# enrichment (corridor `rout` values + ~47 backfilled destination centroids),
# which is deliberately NOT part of the offline build chain. A bare rebuild is
# valid but un-enriched; tolerate exactly that difference and nothing else.
import re  # noqa: E402

_OVERLAY_ENRICHMENT = re.compile(r"^\.corridor\[\d+\]\.(rout|cent)")


def _sanctioned_path(fn, path):
    return fn == "overlay.json" and bool(_OVERLAY_ENRICHMENT.match(path))


def json_delta(a_path, b_path, fn=None):
    """Characterize a JSON delta. Returns (msg, within_tolerance)."""
    a = json.load(open(a_path))
    b = json.load(open(b_path))
    fa = dict(_flatten(a))
    fb = dict(_flatten(b))
    keys = set(fa) | set(fb)
    only_a = [k for k in keys if k not in fb and not _sanctioned_path(fn, k)]
    only_b = [k for k in keys if k not in fa and not _sanctioned_path(fn, k)]
    num_diffs = []
    nonnum_diffs = 0
    for k in keys & set(fa) & set(fb):
        va, vb = fa[k], fb[k]
        if va == vb:
            continue
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            num_diffs.append(abs(va - vb))
        else:
            nonnum_diffs += 1
    msg = (f"leaves={len(keys)} only_staging={len(only_a)} only_base={len(only_b)} "
           f"value_diffs(num)={len(num_diffs)} value_diffs(nonnum)={nonnum_diffs}")
    if num_diffs:
        msg += f" max|num_delta|={max(num_diffs):.6g}"
    within = (not only_a and not only_b and nonnum_diffs == 0
              and (not num_diffs or max(num_diffs) <= NUM_TOLERANCE))
    return msg, within


# design decision: scores.json's flag lists were stale (imported from
# the LOCKED build) and contradicted its own E. They are now derived from the
# displayed build (domains.json, Product-1 domains). Permitted divergences from
# the frozen baseline: the PLW/AND not_scored leaves below, plus the sanctioned
# 2026-06 ADDITIONS (Monte-Carlo rank bands + result class + meta keys).
SCORES_EXPECTED_DIFF_LEAVES = {
    (".countries", "PLW", "not_scored_domains"),
    (".countries", "PLW", "domains_not_scored"),
    (".countries", "AND", "not_scored_domains"),
    (".countries", "AND", "domains_not_scored"),
}
SCORES_SANCTIONED_NEW_FIELDS = {
    "rank_p5", "rank_p50", "rank_p95", "tier_stability", "low_confidence",
}
SCORES_SANCTIONED_NEW_META = {
    "uncertainty", "tier_cuts", "version", "build_date", "citation",
    # provenance keys the current exporter writes; the frozen baseline predates them
    "column_composite", "column_R", "column_E", "source_csv",
}
# Deliberate value changes to pre-existing meta keys (citability decision,
# (no sanctioned meta value-changes at present).
SCORES_SANCTIONED_META_CHANGES = {}


def scores_intentional_check(stg_path, base_path):
    """Return (is_within_scope, summary). True iff every divergence from the
    baseline is either a sanctioned PLW/AND correction leaf or a sanctioned
    ADDITION (key present in staging, absent in baseline, name in the
    sanctioned sets). Any mutation of a pre-existing value is out of scope."""
    a = json.load(open(stg_path)); b = json.load(open(base_path))
    ai = {c["iso3"]: c for c in a["countries"]}
    bi = {c["iso3"]: c for c in b["countries"]}
    if set(ai) != set(bi):
        return False, "country-set changed (OUT OF SCOPE)"
    am = dict(a.get("meta") or {})
    bm = dict(b.get("meta") or {})
    meta_added = [k for k in am if k not in bm]
    bad_meta_adds = [k for k in meta_added if k not in SCORES_SANCTIONED_NEW_META]
    if bad_meta_adds:
        return False, f"UNSANCTIONED meta additions: {sorted(bad_meta_adds)}"
    # Enforce the sanctioned value-change pins: staging must carry the NEW
    # value, and the baseline may hold either side of the pin (old before the
    # change is published, new after).
    for k, (old, new) in SCORES_SANCTIONED_META_CHANGES.items():
        if am.get(k) != new or (k in bm and bm.get(k) not in (old, new)):
            return False, (f"meta.{k} pin violated: expected {old!r} -> {new!r}, "
                           f"staging={am.get(k, '<missing>')!r} "
                           f"baseline={bm.get(k, '<missing>')!r}")
    # Symmetric comparison of everything else. Sanctioned NEW keys are exempt
    # only while absent from the baseline; once the baseline carries them their
    # values must match — except build_date, which legitimately moves per build.
    a_rest = {k: v for k, v in am.items() if k not in SCORES_SANCTIONED_META_CHANGES}
    b_rest = {k: v for k, v in bm.items() if k not in SCORES_SANCTIONED_META_CHANGES}
    for k in SCORES_SANCTIONED_NEW_META:
        if k == "build_date" or k not in bm:
            a_rest.pop(k, None)
            b_rest.pop(k, None)
    if a_rest != b_rest:
        changed = sorted(k for k in set(a_rest) | set(b_rest)
                         if a_rest.get(k) != b_rest.get(k))
        return False, f"pre-existing meta changed (OUT OF SCOPE): {changed}"
    n_meta_changed = sum(1 for k in SCORES_SANCTIONED_META_CHANGES if k in bm)
    out_of_scope = []
    n_correction = 0
    n_added = 0
    for iso in ai:
        for k in set(ai[iso]) | set(bi[iso]):
            if ai[iso].get(k) == bi[iso].get(k):
                continue
            if k not in bi[iso] and k in SCORES_SANCTIONED_NEW_FIELDS:
                n_added += 1
                continue
            if (".countries", iso, k) in SCORES_EXPECTED_DIFF_LEAVES:
                n_correction += 1
                continue
            out_of_scope.append(f"{iso}.{k}")
    if out_of_scope:
        return False, f"UNEXPECTED diffs: {sorted(out_of_scope)}"
    return True, (f"sanctioned: {n_correction} PLW/AND correction leaves, "
                  f"{n_added} added band/class fields, "
                  f"{len(meta_added)} added meta keys; all else byte-equal")


def scores_invariants(stg_path):
    """Baseline-independent self-checks on scores.json. Returns (errors, warnings)."""
    errors, warnings = [], []
    d = json.load(open(stg_path))
    meta = d.get("meta") or {}
    cs = d.get("countries") or []
    scored = [c for c in cs if c.get("scored")]
    n = len(scored)
    if meta.get("n_universe") != len(cs):
        errors.append(f"meta.n_universe={meta.get('n_universe')} != {len(cs)} countries")
    if meta.get("n_scored") != n:
        errors.append(f"meta.n_scored={meta.get('n_scored')} != {n} scored")
    missing_rank = [c.get("iso3") for c in scored if c.get("rank") is None]
    if missing_rank:
        errors.append(f"scored but rank missing/None: {missing_rank}")
    ranks = sorted(c["rank"] for c in scored if c.get("rank") is not None)
    if ranks != list(range(1, n + 1)):
        errors.append("scored ranks are not exactly 1..n_scored")
    band_fields = ("rank_p5", "rank_p50", "rank_p95", "tier_stability",
                   "low_confidence")
    for c in cs:
        iso = c.get("iso3")
        if c.get("scored"):
            missing = [f for f in band_fields if f not in c]
            if missing:
                errors.append(f"{iso}: scored but missing {missing}")
                continue
            p5, p50, p95 = c["rank_p5"], c["rank_p50"], c["rank_p95"]
            if not (1 <= p5 <= p50 <= p95 <= n):
                errors.append(f"{iso}: band ordering broken ({p5},{p50},{p95})")
            if not (0.0 <= c["tier_stability"] <= 1.0):
                errors.append(f"{iso}: tier_stability out of [0,1]")
            if c["low_confidence"] != (int(c.get("domains_not_scored") or 0) >= 2):
                errors.append(f"{iso}: low_confidence does not match "
                              "domains_not_scored >= 2")
            if c.get("rank") is not None and not (p5 <= c["rank"] <= p95):
                warnings.append(f"{iso}: published rank {c['rank']} outside "
                                f"band [{p5},{p95}]")
        else:
            present = [f for f in band_fields if f in c]
            if present:
                errors.append(f"{iso}: unscored but carries {present}")
    if "uncertainty" not in meta:
        errors.append("meta.uncertainty missing")
    if meta.get("tier_cuts") not in ([0.281, 0.402],):
        warnings.append(f"meta.tier_cuts unexpected: {meta.get('tier_cuts')}")
    return errors, warnings


def build_summary():
    """Per-file shas + headline counts, for the regression reference."""
    summary = {"files": {}}
    for fn in FILES:
        p = os.path.join(P.STAGING, fn)
        if os.path.exists(p):
            summary["files"][fn] = {"sha256": sha256(p),
                                    "bytes": os.path.getsize(p)}
    sp = os.path.join(P.STAGING, "scores.json")
    if os.path.exists(sp):
        d = json.load(open(sp))
        cs = d.get("countries") or []
        summary["scores"] = {
            "n_universe": len(cs),
            "n_scored": sum(1 for c in cs if c.get("scored")),
            "n_low_confidence": sum(1 for c in cs if c.get("low_confidence")),
        }
    return summary


def regression_check(summary):
    """Compare against the committed reference. Returns (errors, notes)."""
    if not os.path.exists(REFERENCE_SUMMARY):
        return [], [f"no reference summary at {os.path.relpath(REFERENCE_SUMMARY, _HERE)} "
                    "(run --write-reference once a build is reviewed)"]
    ref = json.load(open(REFERENCE_SUMMARY))
    errors, notes = [], []
    rs, ss = ref.get("scores") or {}, summary.get("scores") or {}
    for key in ("n_universe", "n_scored"):
        if key in rs and key in ss and ss[key] < rs[key]:
            errors.append(f"scores.{key} dropped: {rs[key]} -> {ss[key]}")
    k = "n_low_confidence"
    if k in rs and k in ss and ss[k] > rs[k]:
        notes.append(f"scores.{k} rose: {rs[k]} -> {ss[k]} (review the badge rule)")
    for fn in FILES:
        if fn in (ref.get("files") or {}) and fn not in (summary.get("files") or {}):
            errors.append(f"{fn} present in reference but missing from staging")
    return errors, notes


def topojson_delta(a_path, b_path):
    """Structural comparison. Returns (msg, structurally_equal)."""
    a = json.load(open(a_path))
    b = json.load(open(b_path))
    ao = list(a.get("objects", {}).keys())
    bo = list(b.get("objects", {}).keys())
    name = bo[0] if bo else None
    ga = a["objects"].get(name, {}).get("geometries", []) if name in a.get("objects", {}) else []
    gb = b["objects"].get(name, {}).get("geometries", [])
    ida = set(g.get("properties", {}).get("id") for g in ga)
    idb = set(g.get("properties", {}).get("id") for g in gb)
    pa = set(ga[0]["properties"].keys()) if ga else set()
    pb = set(gb[0]["properties"].keys()) if gb else set()
    equal = (ao == bo and len(ga) == len(gb) and ida == idb and pa == pb)
    msg = (f"objects staging={ao} base={bo} | features staging={len(ga)} base={len(gb)} | "
           f"id_set_equal={ida == idb} | props_equal={pa == pb} | "
           f"size staging={os.path.getsize(a_path)} base={os.path.getsize(b_path)}")
    return msg, equal


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="fail on unsanctioned drift, out-of-tolerance deltas, "
                         "invariant breaches, and regression vs the reference")
    ap.add_argument("--no-baseline", action="store_true",
                    help="permit --strict to pass without a baseline directory "
                         "(invariants/regression layers only)")
    ap.add_argument("--write-reference", action="store_true",
                    help="write the current staging summary as the committed "
                         "regression reference (refused on a failing run)")
    args = ap.parse_args(argv)

    failures = []
    warnings = []

    print(f"{'FILE':<26} {'STATUS':<9} DETAIL")
    print("-" * 100)
    n_pass = 0
    for fn in FILES:
        stg = os.path.join(P.STAGING, fn)
        base = os.path.join(BASELINE, fn)
        if not os.path.exists(stg):
            print(f"{fn:<26} {'BLOCKED':<9} staging file not produced")
            failures.append(f"{fn}: staging file missing")
            continue
        if not os.path.exists(base):
            print(f"{fn:<26} {'?':<9} baseline missing")
            if args.strict and not args.no_baseline:
                failures.append(f"{fn}: baseline missing at {base} (set "
                                f"FLSRI_BASELINE_DATA, or pass --no-baseline "
                                f"to skip the diff layer)")
            else:
                warnings.append(f"{fn}: no baseline to diff against")
            continue
        if sha256(stg) == sha256(base):
            print(f"{fn:<26} {'PASS':<9} sha256 identical")
            n_pass += 1
            continue
        # scores.json carries sanctioned design-decision corrections + additions
        if fn == "scores.json":
            ok, summary = scores_intentional_check(stg, base)
            status = "CORRECTED" if ok else "FAIL"
            print(f"{fn:<26} {status:<9} {summary}")
            if not ok:
                failures.append(f"scores.json: {summary}")
            continue
        if fn.endswith(".topojson"):
            detail, ok = topojson_delta(stg, base)
            print(f"{fn:<26} {'DELTA':<9} {detail}")
            if not ok:
                failures.append(f"{fn}: structural topojson mismatch")
            elif args.strict:
                warnings.append(f"{fn}: geometry-only delta (mapshaper toolchain)")
        else:
            detail, within = json_delta(stg, base, fn=fn)
            print(f"{fn:<26} {'DELTA':<9} {detail}")
            if args.strict and not within:
                failures.append(f"{fn}: delta outside tolerance ({detail})")

    # baseline-independent invariants on scores.json
    stg_scores = os.path.join(P.STAGING, "scores.json")
    if os.path.exists(stg_scores):
        inv_errors, inv_warnings = scores_invariants(stg_scores)
        for e in inv_errors:
            print(f"{'scores.json':<26} {'INVARIANT':<9} {e}")
        failures.extend(f"invariant: {e}" for e in inv_errors)
        warnings.extend(inv_warnings)
        if not inv_errors:
            print(f"{'scores.json':<26} {'OK':<9} band/class invariants hold")

    # regression vs committed reference
    summary = build_summary()
    out_summary = os.path.join(P.STAGING, "_verify_summary.json")
    with open(out_summary, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    reg_errors, reg_notes = regression_check(summary)
    for e in reg_errors:
        print(f"{'(regression)':<26} {'FAIL':<9} {e}")
    failures.extend(f"regression: {e}" for e in reg_errors)
    warnings.extend(reg_notes)

    if args.write_reference:
        if failures:
            print("\nNOT writing reference summary: this run has failures; "
                  "fix them (or review and rerun on a clean build) first")
        else:
            os.makedirs(os.path.dirname(REFERENCE_SUMMARY), exist_ok=True)
            with open(REFERENCE_SUMMARY, "w") as f:
                json.dump(summary, f, indent=2, sort_keys=True)
            print(f"\nwrote reference summary -> {os.path.relpath(REFERENCE_SUMMARY, _HERE)}")

    print("-" * 100)
    print(f"{n_pass}/{len(FILES)} files sha256-identical to baseline")
    for w in warnings:
        print(f"WARN: {w}")
    if failures:
        print(f"\nVERIFY FAILED ({len(failures)}):")
        for e in failures:
            print(f"  - {e}")
        return 1
    print("\nVERIFY OK" + (" (strict)" if args.strict else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
