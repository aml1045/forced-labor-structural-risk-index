"""EPR (Ethnic Power Relations) connector -- Ascriptive Exclusion domain.

Source for the Recruitment domain *Ascriptive Exclusion* (see
docs/scoring-rules.md and docs/METHODS.md), which previously had no loaded
indicator. This is a fresh fetch, and it REUSES the shared plumbing exactly
like every other source module:
  - iso_utils.normalize_to_iso3 / load_sample  (195-country ISO3 stack)
  - standardize.AnchorSpec / anchor_scale       (0-1 absolute anchoring, rule 1)
  - register.upsert_rows                          (provenance + coverage fragment)

WHAT IT MEASURES (see docs/METHODS.md, recruitment/ascriptive-exclusion):
  The domain's one generating driver -- "ascription-based exclusion from
  protection" -- read as the size x depth of a country's ascriptively-excluded
  population. EPR Core codes every politically-relevant ethnic group with its
  population `size` (share) and `status` (access to executive power). We build
  a DEPTH-WEIGHTED EXCLUDED POPULATION SHARE: the population share of groups
  EXCLUDED from power, weighted by exclusion depth (DISCRIMINATED > POWERLESS
  > SELF-EXCLUSION). This is already a per-exposure quantity (share of
  population), so it is anchored ABSOLUTELY per scoring rule 1 (floor 0,
  ceiling 0.5), direction high_risk.

CONSTRUCT NOTES / CAVEATS:
  * SINGLE-SOURCE PROXY FOR THE WHOLE DRIVER. The methodology names four
    conceptual signals (S1 caste / S2 ethnic-racial / S3 religious / S4
    indigeneity). EPR does NOT separate these axes -- it is one unified
    "politically-relevant ethnic group" frame. So EPR operationalizes the
    COMBINED driver D, not four separable signals. The within-driver average
    collapses to this one measure. Caveat: the caste / religious / indigeneity
    axes are NOT independently resolved here.
  * POLITICAL-exclusion, not SOCIOECONOMIC-channeling. EPR measures access to
    *executive power*, which under-captures pure labor-market caste hierarchy
    (e.g. India's Scheduled Castes are coded JUNIOR PARTNER -> 0 here; South
    Africa's post-apartheid Black majority is in-power -> 0). The
    "labor-market channeling" CONDITION (c_A) is therefore NOT supplied by EPR
    and is left to a future data source -- this indicator is the UNCONDITIONED
    standing-exclusion signal. Caveat noted.
  * COVERAGE / structural-absence vs unmeasured (scoring rule 9).
    EPR's universe is countries with population >=250k and >=2 politically-
    relevant groups. ~41 sample countries are absent (small / ethnically
    homogeneous states). Per rule 9 these stay MISSING (drop-and-re-average),
    NOT set to 0 -- we cannot tell "no excluded stratum" (true low risk) from
    "EPR does not cover it" from the panel alone. Caveat noted.
  * LICENSE-VERIFY: EPR is academic/open and downloadable, but redistribution
    terms for the published index are UNCONFIRMED. Pending decision; not
    blocking now.

Config: config/api-config/EPR.yaml
Run:    python -m pipeline.sources.epr
"""

from pathlib import Path
import csv
import io
import ssl
import urllib.request

from pipeline import iso_utils, register
from pipeline.standardize import AnchorSpec, anchor_scale

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = _REPO_ROOT / "config" / "api-config" / "EPR.yaml"
OUT_PATH = _REPO_ROOT / "data" / "processed" / "epr.csv"
FRAGMENT_PATH = _REPO_ROOT / "config" / "data_register.d" / "epr.csv"
# local cache so re-runs work offline once fetched
CACHE_PATH = _REPO_ROOT / "data" / "aux" / "epr_core_raw.csv"

# EPR Core uses Correlates-of-War historical statenames with parentheticals
# that the shared normalizer cannot resolve. These are EPR-specific aliases;
# kept HERE (in this module) rather than edited into the shared iso_utils so
# this connector owns only its own files. All 18 verified to land in the 195
# sample (ingest check 2026-06).
_EPR_NAME_ALIASES = {
    "belarus (byelorussia)": "BLR",
    "burkina faso (upper volta)": "BFA",
    "cambodia (kampuchea)": "KHM",
    "german federal republic": "DEU",
    "iran (persia)": "IRN",
    "italy/sardinia": "ITA",
    "korea, people's republic of": "PRK",
    "macedonia (fyrom/north macedonia)": "MKD",
    "madagascar (malagasy)": "MDG",
    "myanmar (burma)": "MMR",
    "russia (soviet union)": "RUS",
    "sri lanka (ceylon)": "LKA",
    "swaziland (eswatini)": "SWZ",
    "tanzania (tanganyika)": "TZA",
    "turkey (ottoman empire)": "TUR",
    "vietnam, democratic republic of": "VNM",
    "yemen (arab republic of yemen)": "YEM",
    "zimbabwe (rhodesia)": "ZWE",
}


def _load_config():
    if not _HAS_YAML:
        raise RuntimeError(
            "PyYAML is required to read config/api-config/EPR.yaml "
            "(pip install pyyaml)."
        )
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _fetch_raw(url):
    """Return the EPR Core CSV text, caching to data/aux for offline re-runs."""
    if CACHE_PATH.exists():
        print(f"[epr] using cached raw {CACHE_PATH}")
        return CACHE_PATH.read_text(encoding="utf-8")
    print(f"[epr] downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "FLSRI-data/1.0"})
    # Verified context first; fall back to certifi, then to an UNVERIFIED
    # context (some macOS Python builds ship without the system CA bundle).
    # The cache (seeded via curl) is the normal path, so this is a backstop.
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            text = resp.read().decode("utf-8")
    except ssl.SSLError:
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                text = resp.read().decode("utf-8")
        except Exception:
            print("[epr] WARN: TLS verification failed; retrying UNVERIFIED "
                  "(seed data/aux/epr_core_raw.csv via curl to avoid this)")
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                text = resp.read().decode("utf-8")
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(text, encoding="utf-8")
    print(f"[epr] cached raw -> {CACHE_PATH}")
    return text


def _epr_iso3(statename):
    """Normalize an EPR statename to ISO3, applying EPR-specific aliases first."""
    key = (statename or "").strip().lower()
    if key in _EPR_NAME_ALIASES:
        return _EPR_NAME_ALIASES[key]
    return iso_utils.normalize_to_iso3(statename)


def _excluded_share_by_iso3(text, snapshot_year, status_weights):
    """Build {iso3: depth-weighted excluded population share} for snapshot_year.

    For each country, take the group rows whose [from,to] period contains the
    snapshot year; sum (status_weight * group_size) over groups in an excluded
    status. Groups with access to power contribute 0. Returns raw shares
    (NOT yet 0-1 scaled) plus a small report.
    """
    reader = csv.DictReader(io.StringIO(text))
    by_iso = {}
    seen_countries = set()
    unmatched_names = set()
    for row in reader:
        try:
            yfrom, yto = int(row["from"]), int(row["to"])
        except (ValueError, KeyError, TypeError):
            continue
        if not (yfrom <= snapshot_year <= yto):
            continue
        statename = row.get("statename", "")
        seen_countries.add(statename)
        iso3 = _epr_iso3(statename)
        if iso3 is None:
            unmatched_names.add(statename)
            continue
        status = (row.get("status") or "").strip().upper()
        w = status_weights.get(status, 0.0)
        try:
            size = float(row.get("size") or 0.0)
        except ValueError:
            size = 0.0
        # accumulate (in-sample countries with no excluded group stay at 0.0,
        # which is a TRUE low-risk reading -- distinct from absent-from-EPR,
        # which never enters by_iso and stays missing downstream)
        by_iso.setdefault(iso3, 0.0)
        if w > 0.0:
            by_iso[iso3] += w * size
    report = {
        "n_countries_in_panel": len(seen_countries),
        "n_iso3_resolved": len(by_iso),
        "unmatched_names": sorted(unmatched_names),
    }
    return by_iso, report


def run():
    cfg = _load_config()
    countries = iso_utils.load_sample()
    snapshot_year = int(cfg["snapshot_year"])
    status_weights = {k.upper(): float(v) for k, v in cfg["status_weights"].items()}

    text = _fetch_raw(cfg["download_url"])
    raw_by_iso3, report = _excluded_share_by_iso3(text, snapshot_year, status_weights)

    if report["unmatched_names"]:
        print(f"[epr] WARN unmatched EPR statenames (dropped): "
              f"{report['unmatched_names']}")

    idx = cfg["indicator"]
    spec = AnchorSpec(
        indicator=idx["output_col"],
        floor=float(idx["floor"]),
        ceiling=float(idx["ceiling"]),
        direction=idx["direction"],
        unit=idx["unit"],
        anchor_source=idx["anchor_source"],
    )
    result = anchor_scale(raw_by_iso3, spec, sample=countries)

    # Construct/coverage caveats carried into the register flag stream.
    flags = [
        "SINGLE-SIGNAL DRIVER (Ascriptive Exclusion) -- single EPR signal "
        "operationalizes the COMBINED driver D; the four conceptual axes (S1 "
        "caste / S2 ethnic / S3 religious / S4 indigeneity) are NOT separately "
        "resolved by EPR's unified ethnic-group frame",
        "POLITICAL-EXCLUSION not SOCIOECONOMIC-CHANNELING: EPR codes access to "
        "executive power, under-captures labor-market caste hierarchy (India SCs "
        "coded JUNIOR PARTNER -> 0; ZAF post-apartheid majority in-power -> 0); "
        "the labor-market-channeling CONDITION c_A is NOT supplied here -- this "
        "is the UNCONDITIONED standing-exclusion signal",
        "STRUCTURAL-ABSENCE vs UNMEASURED (rule 9): ~41 sample countries absent "
        "from EPR's universe (pop>=250k & >=2 relevant groups) stay MISSING "
        "(drop-and-re-average), NOT 0 -- cannot distinguish 'no excluded stratum' "
        "from 'not covered' from the panel alone; pending a coding rule",
        "LICENSE-VERIFY: EPR academic/open & downloadable but redistribution "
        "terms for the published index UNCONFIRMED -- confirm before public "
        "release; NOT blocking now",
        "GOVERNANCE-MODULATOR (Z1) NOT applied here: the shared general-governance "
        "dial is the score-once WGI/V-Dem backbone (already wired: "
        "wb_wgi_rule_of_law / v2x_rule) applied at domain-assembly, NOT in this "
        "source table -- de-dup at the cross-domain pass",
    ]

    register_rows = [
        result.register_row(
            source=cfg["source_title"],
            series_id=f"EPR-{cfg['vintage']} Core: status x size, snapshot {snapshot_year}",
            license=cfg["license"],
            extra_flags=flags,
        )
    ]
    register_rows[0]["year_min"] = snapshot_year
    register_rows[0]["year_max"] = snapshot_year

    print(
        f"[epr] {idx['output_col']}: dir={result.meta['direction']} "
        f"anchor={result.meta['anchor']} "
        f"coverage={result.meta['coverage_pct']:.1f}% "
        f"({result.meta['n_present']}/{result.meta['n_total']}) "
        f"below_floor={result.meta['below_floor']}"
    )

    # --- write data/processed/epr.csv (iso3 + the one indicator column) ------
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3", idx["output_col"]])
        for iso3 in countries:
            v = result.get(iso3)
            w.writerow([iso3, "" if v is None else round(v, 4)])

    # --- write per-source register fragment (NOT the master register) --------
    register.upsert_rows(register_rows, path=str(FRAGMENT_PATH))

    print(f"[epr] wrote {OUT_PATH}")
    print(f"[epr] wrote fragment {FRAGMENT_PATH} ({len(register_rows)} indicator row)")
    return result, register_rows


if __name__ == "__main__":
    run()
