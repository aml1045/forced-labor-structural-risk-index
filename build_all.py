#!/usr/bin/env python3
"""FLSRI one-command build orchestrator.

Default `all` is OFFLINE-REPRODUCIBLE: it rebuilds everything from the tracked
caches, raw files, and processed tables already on disk — no network. Freshness
is opt-in via the `refresh` stage / --refresh flag.

Stages:
    check        env doctor + gated-input checklist (read-only)
    refresh      live re-pull of API sources (network; updates caches)
    indicators   connectors -> data/processed/*.csv + register fragments,
                 then merge_register (offline-capable connectors only)
    score        run.py reproduction build -> outputs/scores.csv etc.
    site         build_site_data.py -> outputs/site_data_staging/*
    verify       site_data_verify.py --strict (non-zero exit aborts `all`)
    report       input manifest (sha256 of every consumed input) + vintage report
    all          check -> [indicators] -> score -> site -> verify -> report

Usage:
    python build_all.py all                      # offline, full chain
    python build_all.py all --skip-indicators    # trust tracked data/processed/
    python build_all.py all --refresh            # live re-pull first
    python build_all.py <stage>                  # any single stage

Environment:
    FLSRI_BASELINE_DATA  baseline for verify (default: <repo>/public/data)
    FLSRI_EXTERNAL_DATA  external-inputs root (default: <repo>/external)
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TODAY = datetime.date.today().isoformat()
MANIFEST_PATH = os.path.join(HERE, "outputs", f"build_manifest-{TODAY}.json")

# Connector execution table. mode:
#   offline         VERIFIED to rebuild its processed table byte-stable from
#                   caches/raw files with no network (args = offline args)
#   cache-required  offline iff the named file(s) exist (skipped with notice otherwise)
#   live-only       network required -> run only in `refresh`; the tracked
#                   data/processed CSV is the pinned offline input
#   credentialed    live-only + needs credentials (UNCTAD)
#
# Empirical note: age_childhood / legal_non_recognition /
# recruitment_econprecarity ship a --cache flag but the shared
# data/aux/worldbank_cache.csv does NOT carry their series — a --cache run
# writes EMPTY output over the good tracked layer. They are live-only until a
# refresh write-back populates the cache with their series. monetization_b's
# --no-network path likewise drops the data360 component. The drift gate below
# is the backstop for any regression of this kind.
CONNECTORS = [
    ("worldbank",                 ["--cache"], "offline"),
    ("vdem",                      [],          "offline"),
    ("findex",                    [],          "offline"),
    ("basel_fatf",                [],          "offline"),
    ("aux_emdat",                 [],          "offline"),
    ("ndgain",                    [],          "offline"),
    ("aux_ucdp",                  [],          "offline"),
    ("epr",                       [],          "offline"),
    ("state_production",          [],          "offline"),
    ("static_indices",            [],          "cache-required"),
    ("unhcr",                     [],          "cache-required"),
    ("age_childhood",             [],          "live-only"),
    ("legal_non_recognition",     [],          "live-only"),
    ("recruitment_econprecarity", [],          "live-only"),
    ("monetization_b",            [],          "live-only"),
    ("ilostat",                   [],          "live-only"),
    ("gender_structuring",        [],          "live-only"),
    ("econ_structure_demand",     [],          "live-only"),
    ("aux_unctad",                [],          "credentialed"),
]
UNHCR_CACHE = os.path.join(HERE, "data", "raw", "unhcr_cache.csv")
# files each cache-required connector needs before it may run offline
CACHE_REQUIRED_FILES = {
    "unhcr": [UNHCR_CACHE],
    "static_indices": [
        os.path.join(HERE, "data", "raw", "trace_bribery_risk_matrix_2024.csv"),
        os.path.join(HERE, "data", "raw", "basel_aml_index_2025.csv"),
        os.path.join(HERE, "data", "raw", "henley_passport_index_count_2026-05-07.csv"),
    ],
}

_manifest = {"date": TODAY, "stages": {}}


def _record(stage, status, detail=""):
    _manifest["stages"][stage] = {"status": status, "detail": detail,
                                  "at": datetime.datetime.now().isoformat(timespec="seconds")}
    os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
    # merge with any earlier same-day runs so single-stage invocations
    # accumulate instead of clobbering the daily manifest
    merged = dict(_manifest)
    if os.path.exists(MANIFEST_PATH):
        try:
            prior = json.load(open(MANIFEST_PATH)).get("stages", {})
            merged["stages"] = {**prior, **_manifest["stages"]}
        except (json.JSONDecodeError, OSError):
            pass
    with open(MANIFEST_PATH, "w") as f:
        json.dump(merged, f, indent=2)


def _run(cmd, cwd=None, env=None, label=None):
    print(f"\n>>> {label or ' '.join(map(str, cmd))}", flush=True)
    return subprocess.run(cmd, cwd=cwd or HERE, env=env).returncode


def _connector_cmd(name, args):
    return [PY, "-m", f"pipeline.sources.{name}", *args]


def stage_check(stages_needed):
    rc = 0
    print("== env doctor ==")
    v = sys.version_info
    print(f"python: {sys.version.split()[0]} ({'OK' if (3, 11) <= v[:2] <= (3, 13) else 'WARN: pins target 3.11-3.13'})")
    for mod in ("numpy", "pandas", "yaml"):
        try:
            __import__(mod)
            print(f"import {mod}: OK")
        except ImportError:
            print(f"import {mod}: MISSING")
            rc = 1
    if "site" in stages_needed:
        for mod in ("geopandas", "libpysal", "esda"):
            try:
                __import__(mod)
                print(f"import {mod}: OK")
            except ImportError:
                print(f"import {mod}: MISSING (site stage needs the geo stack — use .venv311)")
                rc = 1
        try:
            ms = subprocess.run(["npx", "--no-install", "mapshaper", "-v"],
                                capture_output=True, text=True, cwd=HERE)
            ms_ok = ms.returncode == 0
        except FileNotFoundError:
            ms_ok = False
        if ms_ok:
            print(f"mapshaper: {ms.stdout.strip()} OK")
        else:
            print("mapshaper: MISSING (npm ci, or npm i -g mapshaper; needs Node/npx on PATH)")
            rc = 1
    print("\n== gated inputs ==", flush=True)
    cm = [PY, os.path.join(HERE, "script", "check_manual.py")]
    for s in sorted(stages_needed & {"indicators", "site"}):
        cm += ["--stage", s]
    rc = max(rc, subprocess.run(cm, cwd=HERE).returncode)
    return rc


def stage_indicators(offline=True, baseline="git"):
    """baseline: 'git' compares the data layer against HEAD (pure offline
    rebuild must reproduce the committed pin); 'snapshot' compares against the
    on-disk state at entry (used after a refresh, whose changes are
    legitimate — the gate then only checks the offline re-run is stable)."""
    failures = []
    pre_hashes = None
    if offline and baseline == "snapshot":
        pre_hashes = {}
        proc_dir = os.path.join(HERE, "data", "processed")
        for p in sorted(glob.glob(os.path.join(proc_dir, "*.csv"))):
            pre_hashes[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    for name, off_args, mode in CONNECTORS:
        if offline:
            if mode in ("live-only", "credentialed"):
                print(f"SKIP {name}: {mode} — tracked data/processed/{name}.csv is the pinned input")
                continue
            if mode == "cache-required":
                missing = [p for p in CACHE_REQUIRED_FILES.get(name, []) if not os.path.exists(p)]
                if missing:
                    print(f"SKIP {name}: needed file(s) missing "
                          f"({', '.join(os.path.relpath(p, HERE) for p in missing)}) — "
                          f"tracked data/processed/{name}.csv is the pinned input")
                    continue
            args = off_args
        else:
            args = ["--refresh"] if name == "unhcr" else []
        if _run(_connector_cmd(name, args), label=f"python -m pipeline.sources.{name} {' '.join(args)}"):
            failures.append(name)
    if _run([PY, "-m", "pipeline.merge_register"], label="python -m pipeline.merge_register"):
        failures.append("merge_register")
    # Drift gate: an offline run over pinned inputs must reproduce the tracked
    # DATA layer byte-for-byte. Data drift is a hard failure (a connector just
    # regressed the pinned layer — `git checkout -- data/processed
    # config/data_register.d config/data_register.csv` to restore). Fragment
    # text drift alone (register wording newer than the committed artifact) is
    # surfaced as a warning to review-and-commit.
    if offline:
        def _git_out(args):
            try:
                p = subprocess.run(["git", *args], capture_output=True,
                                   text=True, cwd=HERE)
            except FileNotFoundError:
                return None
            return p.stdout.strip() if p.returncode == 0 else None

        if pre_hashes is not None:
            # post-refresh mode: the refreshed layer is the new baseline; only
            # instability of THIS offline re-run counts as drift
            data_drift = "\n".join(
                os.path.relpath(p, HERE) for p in sorted(pre_hashes)
                if not os.path.exists(p)
                or hashlib.sha256(open(p, "rb").read()).hexdigest() != pre_hashes[p])
            frag_drift = ""
        else:
            # --porcelain also surfaces NEW untracked files in data/processed
            data_drift = _git_out(["status", "--porcelain", "--", "data/processed"])
            frag_drift = _git_out(["diff", "--stat", "--",
                                   "config/data_register.d", "config/data_register.csv"])
        if data_drift is None or frag_drift is None:
            print("\nFAIL: drift gate unavailable (git missing or not a work tree) — "
                  "cannot confirm the offline run reproduced the pinned data layer.")
            failures.append("drift:gate-unavailable")
        elif data_drift:
            print("\nFAIL: offline connector run CHANGED the pinned data layer:")
            print(data_drift)
            if pre_hashes is None:
                print("Restore with: git checkout -- data/processed "
                      "config/data_register.d config/data_register.csv")
            failures.append("drift:data/processed")
        elif frag_drift:
            print("\nWARN: register fragments drifted (connector wording newer than "
                  "the committed fragments) — review and commit if intended:")
            print(frag_drift)
    return 1 if failures else 0


def _snapshot_fragments():
    """{fragment-file: {indicator: (countries, year_min, year_max, coverage_pct)}}"""
    import csv as _csv
    snap = {}
    frag_dir = os.path.join(HERE, "config", "data_register.d")
    for fn in sorted(os.listdir(frag_dir)):
        if fn.endswith(".csv"):
            with open(os.path.join(frag_dir, fn), newline="", encoding="utf-8") as fh:
                snap[fn] = {r["indicator"]: (r.get("countries"), r.get("year_min"),
                                             r.get("year_max"), r.get("coverage_pct"))
                            for r in _csv.DictReader(fh)}
    return snap


def stage_refresh():
    print("== refresh: live re-pull (network) ==")
    before = _snapshot_fragments()
    _run([PY, "-m", "pipeline.build_population"], label="python -m pipeline.build_population")
    rc = stage_indicators(offline=False)
    after = _snapshot_fragments()
    # refresh report: per-indicator movement in countries / vintage / coverage
    lines = [f"# FLSRI refresh report — {TODAY}\n",
             "| Fragment | Indicator | Countries | Years | Coverage % |",
             "|---|---|---|---|---|"]
    n_changed = 0
    for fn in sorted(set(before) | set(after)):
        b, a = before.get(fn, {}), after.get(fn, {})
        for ind in sorted(set(b) | set(a)):
            if b.get(ind) != a.get(ind):
                n_changed += 1
                bb, aa = b.get(ind) or ("—",) * 4, a.get(ind) or ("—",) * 4
                lines.append(f"| {fn} | {ind} | {bb[0]} → {aa[0]} "
                             f"| {bb[1]}–{bb[2]} → {aa[1]}–{aa[2]} | {bb[3]} → {aa[3]} |")
    lines.append(f"\n{n_changed} indicator row(s) moved. Caches were written back "
                 f"(data/aux/cache_manifest.json); commit data/processed + "
                 f"config/data_register.d together with this report if the refresh "
                 f"is being adopted.")
    out = os.path.join(HERE, "outputs", f"refresh-report-{TODAY}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {os.path.relpath(out, HERE)} ({n_changed} changed rows)")
    return rc


def stage_score():
    return _run([PY, os.path.join(HERE, "run.py")])


def stage_site():
    return _run([PY, os.path.join(HERE, "build_site_data.py")])


def stage_verify():
    env = dict(os.environ)
    env.setdefault("FLSRI_BASELINE_DATA", os.path.join(HERE, "public", "data"))
    return _run([PY, os.path.join(HERE, "site_data_verify.py"), "--strict"], env=env)


def stage_report():
    # input manifest: sha256 of every gitignored/manual input the build consumed
    roots = [
        ("data/raw", "**/*"), ("data/aux", "**/*"), ("data/processed", "*.csv"),
        ("external/experiments", "**/*.csv"), ("external/geo", "**/*"),
    ]
    inputs = {}
    for root, pat in roots:
        for p in sorted(glob.glob(os.path.join(HERE, root, pat), recursive=True)):
            if os.path.isfile(p):
                h = hashlib.sha256(open(p, "rb").read()).hexdigest()
                inputs[os.path.relpath(p, HERE)] = {
                    "sha256": h, "bytes": os.path.getsize(p),
                    "mtime": datetime.date.fromtimestamp(os.path.getmtime(p)).isoformat(),
                }
    out = os.path.join(HERE, "outputs", f"input-manifest-{TODAY}.json")
    with open(out, "w") as f:
        json.dump({"date": TODAY, "n_inputs": len(inputs), "inputs": inputs}, f, indent=2)
    print(f"wrote {os.path.relpath(out, HERE)} ({len(inputs)} inputs)")
    # vintage report (C5)
    rc = 0
    vr = os.path.join(HERE, "script", "vintage_report.py")
    if os.path.exists(vr):
        rc = _run([PY, vr], label="python script/vintage_report.py")
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("stage", choices=["check", "refresh", "indicators", "score",
                                      "site", "verify", "report", "all"])
    ap.add_argument("--refresh", action="store_true",
                    help="with `all`: live re-pull before building")
    ap.add_argument("--skip-indicators", action="store_true",
                    help="with `all`: trust the tracked data/processed/ layer")
    args = ap.parse_args()

    single = {
        "check": lambda: stage_check({"indicators", "site"}),
        "refresh": stage_refresh,
        "indicators": stage_indicators,
        "score": stage_score,
        "site": stage_site,
        "verify": stage_verify,
        "report": stage_report,
    }
    if args.stage != "all":
        rc = single[args.stage]()
        _record(args.stage, "ok" if rc == 0 else "FAIL")
        sys.exit(rc)

    chain = [("check", lambda: stage_check(
        {"site"} | (set() if args.skip_indicators else {"indicators"})))]
    if args.refresh:
        chain.append(("refresh", stage_refresh))
    if not args.skip_indicators:
        # after a refresh, the refreshed layer is the legitimate new baseline:
        # gate only on the offline re-run being stable, not on git-vs-HEAD
        chain.append(("indicators",
                      (lambda: stage_indicators(baseline="snapshot"))
                      if args.refresh else stage_indicators))
    chain += [("score", stage_score), ("site", stage_site),
              ("verify", stage_verify), ("report", stage_report)]

    for name, fn in chain:
        print(f"\n========== STAGE: {name} ==========")
        rc = fn()
        _record(name, "ok" if rc == 0 else "FAIL")
        if rc != 0:
            print(f"\nBUILD ABORTED at stage `{name}` (exit {rc}). "
                  f"Manifest: {os.path.relpath(MANIFEST_PATH, HERE)}")
            sys.exit(rc)
    print(f"\nBUILD OK. Manifest: {os.path.relpath(MANIFEST_PATH, HERE)}")
    print("Staging dir is outputs/site_data_staging/ — publication to public/data/ "
          "and the rsync to flsri-demo remain manual, deliberate steps.")


if __name__ == "__main__":
    main()
