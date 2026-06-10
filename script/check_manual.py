#!/usr/bin/env python3
"""Check the gated / manually-acquired inputs listed in config/manual_inputs.yaml.

Usage:
    python script/check_manual.py [--stage indicators] [--stage site] [--quiet]

Prints OK / MISSING / OPTIONAL per entry (with size, mtime, sha256 prefix, and
the filename-derived vintage where the manifest defines one), then a
copy-pasteable download checklist for anything missing. Exits non-zero iff a
file required by one of the requested stages is missing. With no --stage, all
stages are required.
"""
import argparse
import glob
import hashlib
import os
import re
import sys
from datetime import datetime

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MANIFEST = os.path.join(REPO, "config", "manual_inputs.yaml")


def sha256(path, limit_mb=200):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve(entry):
    """Return list of matching absolute paths (glob-aware)."""
    pat = os.path.join(REPO, entry["path"])
    if any(ch in pat for ch in "*?["):
        return sorted(glob.glob(pat))
    return [pat] if os.path.exists(pat) else []


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", action="append", default=None,
                    help="stage(s) whose required inputs must be present "
                         "(repeatable); default: all stages")
    ap.add_argument("--quiet", action="store_true", help="only print problems")
    args = ap.parse_args(argv)
    stages = set(args.stage) if args.stage else None

    with open(MANIFEST) as f:
        man = yaml.safe_load(f)

    missing_required = []
    missing_optional = []
    print(f"{'STATUS':<9} {'PATH':<62} DETAIL")
    print("-" * 110)
    for e in man.get("inputs", []):
        req_stages = e.get("required_for") or []
        required = bool(req_stages) and (stages is None or bool(stages & set(req_stages)))
        hits = resolve(e)
        if hits:
            p = hits[-1]  # newest match for globs (sorted)
            st = os.stat(p)
            vint = ""
            if e.get("vintage_regex"):
                m = re.search(e["vintage_regex"], os.path.basename(p))
                if m:
                    vint = f" vintage={m.group(1)}"
            if not args.quiet:
                print(f"{'OK':<9} {e['path']:<62} "
                      f"{st.st_size:>11,}B  {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d}  "
                      f"sha={sha256(p)[:12]}{vint}")
        else:
            status = "MISSING" if required else "OPTIONAL"
            print(f"{status:<9} {e['path']:<62} needed by: {', '.join(map(str, e.get('used_by', [])))}")
            (missing_required if required else missing_optional).append(e)

    if missing_required or missing_optional:
        print("\n# Download checklist")
        for e in missing_required + missing_optional:
            tag = "REQUIRED" if e in missing_required else "optional"
            print(f"\n[{tag}] {e['path']}")
            print(f"  provider: {e.get('provider', '?')}")
            print(f"  get it:   {e.get('url', '?')}")
            print(f"  license:  {e.get('license', '?')}")
            if e.get("recreate"):
                print(f"  or run:   {e['recreate']}")

    for v in man.get("env", []):
        if not args.quiet:
            print(f"\nENV {v['name']}: {v['note']}")

    if missing_required:
        print(f"\nCHECK FAILED: {len(missing_required)} required input(s) missing "
              f"for stage(s) {sorted(stages) if stages else 'ALL'}")
        return 1
    print("\nCHECK OK" + (f" for stages {sorted(stages)}" if stages else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
