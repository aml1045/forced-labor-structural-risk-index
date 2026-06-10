"""Build the machine-readable codebook registry for the FLSRI index.

The registry documents the REAL indicator layer. It is *derived*, not
hand-maintained: the frozen Phase -> Domain structure (registry) is held
here, but each domain's concrete signals are pulled live from

  * pipeline/crosswalk.py   -- domain -> (processed_table, column) signal tuples,
                               per-domain confidence and design flags, the
                               circularity-flagged signals, the Product-1 phase
                               membership, the Monetization (Product-2) domains,
                               and the shared governance backbone; and
  * config/data_register.csv -- per-signal source, series/column id, license,
                               direction, absolute anchor, and coverage.

Running this module re-derives codebook/registry.json and codebook/registry.md
from those two sources, so the codebook can never drift from the pipeline it
documents. Methodology source of record: docs/METHODS.md; scoring rules:
docs/scoring-rules.md; per-source provenance: docs/data-provenance.md.

The 13-domain, 3-phase structure (registry) is frozen. The
indicator layer below is whatever crosswalk.py currently selects: it is
deliberately uneven (several domains rest on partial or single-signal coverage,
some on defeater-only or circularity-flagged inputs). Those caveats are carried
through from the crosswalk verbatim rather than smoothed over.

Monetization (Domain A, Domain B) is a Product-2 lens only and is EXCLUDED from
the published Product-1 composite (see crosswalk.PRODUCT1_PHASES, which omits
Monetization; enforced in composite.py).
"""

import csv
import json
import os
import re

REGISTRY_VERSION = "1.0"
REGISTRY_FROZEN = True
SOURCE = "docs/METHODS.md"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CROSSWALK_PATH = os.path.join(REPO, "pipeline")
DATA_REGISTER = os.path.join(REPO, "config", "data_register.csv")

import sys
sys.path.insert(0, CROSSWALK_PATH)
import crosswalk as cw  # noqa: E402  (path set above)

# Phase short codes and labels. The Product-1 composite is built over
# Recruitment + Exploitation only; Monetization is the Product-2 lens.
PHASES = ["recruitment", "exploitation", "monetization"]
PHASE_LABELS = {
    "recruitment": "Recruitment",
    "exploitation": "Exploitation",
    "monetization": "Monetization",
}
PHASE_CODES = {"recruitment": "R", "exploitation": "E", "monetization": "M"}

# Domain display names + frozen design notes, keyed by phase. Names and notes
# are the frozen registry labels; slugs are the crosswalk keys.
DOMAIN_META = {
    "recruitment": [
        ("economic-precarity", "Economic Precarity", ""),
        ("debt-financialized-dependency", "Debt & Financialized Dependency",
         "connecting-flagged"),
        ("constrained-mobility", "Constrained Mobility", "connecting-flagged"),
        ("ascriptive-exclusion", "Ascriptive Exclusion",
         "legal cluster split out"),
        ("legal-non-recognition", "Legal Non-Recognition",
         "CRVS / birth-registration backbone"),
        ("gender-structuring", "Gender Structuring",
         "generating face; modulating face routed to modifiers"),
        ("age-childhood-structuring", "Age/Childhood Structuring",
         "child-labour-as-pickability"),
        ("structural-disruption", "Structural Disruption",
         "gated, not additive; connecting-flagged"),
    ],
    "exploitation": [
        ("foreclosed-exit-structural", "Foreclosed Exit (Structural)",
         "standing exit-cost / monopsony capacity"),
        ("economic-structure-demand", "Economic Structure & Demand", ""),
        ("state-production-of-unfreedom", "State Production of Unfreedom", ""),
    ],
    "monetization": [
        ("domain-a-transnational-concealment",
         "Domain A - Transnational concealment & laundering infrastructure",
         "Product-2 lens; does NOT score into the Product-1 composite"),
        ("domain-b-cash-informal-retention",
         "Domain B - Cash & informal-economy retention",
         "Product-2 lens; carries the only narrow Product-1 candidate slice "
         "(conditional, Phase 2)"),
    ],
}

# The shared corruption/capture gate. Folded as a modifier/defeater-flagged
# gate inside the domains it protects -- NOT a standalone scored domain. The
# general-governance backbone (worldbank/wb_wgi_rule_of_law) is scored ONCE as
# the domain-level attenuate-only modulator; the financial-integrity / AML
# signal gets a distinct second entry in Monetization.
SHARED_GATE = {
    "slug": "corruption-capture-gate",
    "name": "Corruption / capture & bought impunity",
    "note": (
        "Shared modifier/defeater-flagged gate folded into the domains it "
        "protects across all three phases; not a standalone domain. The "
        "general-governance backbone signal "
        f"({cw.GOVERNANCE_TABLE}/{cw.GOVERNANCE_COLUMN}) is scored once as the "
        "domain-level attenuate-only modulator; the financial-integrity / AML "
        "signal gets a distinct second entry in Monetization. See "
        "docs/scoring-rules.md."
    ),
    "governance_backbone": {
        "table": cw.GOVERNANCE_TABLE,
        "column": cw.GOVERNANCE_COLUMN,
    },
}

# Crosswalk columns that are emitted under a standardized name differing from
# the source-indicator key used in config/data_register.csv. Maps the crosswalk
# column -> the register `indicator` row that documents its source/coverage.
COLUMN_TO_REGISTER_KEY = {
    "aux_emdat_disaster_shock": "disaster_affected_intensity",
    "aux_unctad_export_concentration": "unctad_export_concentration",
}


# --- carried-text sanitization ------------------------------------------------
# The per-signal `flags` (crosswalk.py) and `register_note` (data_register.csv)
# are reproduced here verbatim as the codebook's design caveats -- they are
# genuine methodology (coverage floors, circularity flags, license-pending
# notes, de-duplication checks) and are KEPT. This pass only (a) repoints
# citations to files that are not part of this repo onto the canonical docs,
# and (b) rephrases a handful of internal-process markers into neutral research
# phrasing. It preserves the substance of every caveat and is idempotent (a
# no-op on already-clean text), so it is robust to upstream cleanup of the
# source files. Genuine methodology terms (e.g. correlation/collinearity
# screens, re-publication terms unconfirmed license caveats, coverage floors) are left intact.

# Dangling "Request filed: <path>" / "Request: <path>" pointers reference an
# internal request tracker that is not part of this repo. Drop the pointer but
# keep the fact that the gap was flagged for follow-up.
_REQUEST_RE = re.compile(
    r"Request(?:\s+\w+)?:?\s*(?:_requests/)?[\w./-]+\.md", re.IGNORECASE)
# "(_requests/<file>.md ...)" parenthetical pointers.
_REQUEST_PAREN_RE = re.compile(r"\((?:_requests/)?[\w./-]*\.md[^)]*\)")
# Any remaining bare reference to the internal request tracker directory.
_REQUEST_DIR_RE = re.compile(r"\bopen _requests/ item\b", re.IGNORECASE)
# Cross-references into non-shipping working documents -> the methods doc.
# Consumes a trailing section/flag locator (incl. dotted numbers like "9.2").
_FINDINGS_RE = re.compile(
    r"findings(?:\.md)?"
    r"(?:\s+(?:section|sec\.?|flag|flags|§)?\s*[\w&-]+(?:\.[\w-]+)*"
    r"(?:\s*[&,]\s*[\w-]+)*)?",
    re.IGNORECASE)
# Internal-process vocabulary -> neutral research phrasing.
_PROCESS_SUBS = [
    (re.compile(r"\bescalated to owner\b", re.IGNORECASE), "flagged for review"),
    (re.compile(r"\bescalate(d)?\b", re.IGNORECASE), "flagged"),
    (re.compile(r"\bowner must rule\b", re.IGNORECASE),
     "pending a design decision on"),
    (re.compile(r"\bOWNER call\b", re.IGNORECASE), "open decision"),
    (re.compile(r"\bowner ruling\b", re.IGNORECASE), "design decision"),
    (re.compile(r"\bowner-(ruled|gated)\b", re.IGNORECASE), "decided"),
    (re.compile(r"\bowner-preferred\b", re.IGNORECASE), "preferred"),
    (re.compile(r"\bRAISED\b"), "flagged for review"),
    (re.compile(r"\braised, not decided\b", re.IGNORECASE),
     "flagged, pending decision"),
    (re.compile(r"\bNOT decided here\b", re.IGNORECASE), "pending decision"),
    (re.compile(r"\bnot decided now\b", re.IGNORECASE), "pending decision"),
    (re.compile(r"\bHUMAN methods reviewer\b", re.IGNORECASE), "review"),
    (re.compile(r"\bhuman methods reviewer\b", re.IGNORECASE), "review"),
    (re.compile(r"\bto the owner\b", re.IGNORECASE), "for methods review"),
    (re.compile(r"\bsurfaced to the owner\b", re.IGNORECASE),
     "surfaced for review"),
]


def _collapse_ws(text):
    text = re.sub(r"\s+([.;,)])", r"\1", text)
    text = re.sub(r"\(\s*[.;,]?\s*\)", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def sanitize_note(text):
    """Repoint dangling citations and neutralize internal-process vocabulary.

    Substance-preserving and idempotent. See module-level note above.
    """
    if not text:
        return text
    out = _REQUEST_RE.sub("(source gap flagged for review)", text)
    out = _REQUEST_PAREN_RE.sub("", out)
    out = _REQUEST_DIR_RE.sub("open source gap", out)
    out = _FINDINGS_RE.sub("docs/METHODS.md", out)
    for pat, repl in _PROCESS_SUBS:
        out = pat.sub(repl, out)
    return _collapse_ws(out)


def load_register(path):
    """Index config/data_register.csv by its `indicator` key."""
    register = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            register[row["indicator"]] = row
    return register


def _clean(value):
    """Normalize a register cell: blank/'n/a'-style markers -> None."""
    if value is None:
        return None
    v = value.strip()
    if not v or v.lower().startswith("n/a"):
        return None
    return v


def resolve_signal(table, column, register):
    """Build one indicator record for a (table, column) crosswalk signal.

    Source/series/license/direction/anchor/coverage are read from
    config/data_register.csv, matched on the register `indicator` key (which is
    the standardized column name, with a small alias map for the two columns
    emitted under a different name than their source-indicator key).
    """
    key = COLUMN_TO_REGISTER_KEY.get(column, column)
    row = register.get(key)
    record = {
        "table": table,
        "column": column,
        "register_key": key,
    }
    if row is None:
        # No register row for this column (e.g. a standardized output whose
        # provenance lives only in the connector). Keep the signal; document
        # what we know and flag the missing provenance row.
        record.update({
            "source": None,
            "series_id": None,
            "license": None,
            "direction": None,
            "anchor": None,
            "coverage_pct": None,
            "register_note": (
                "No matching row in config/data_register.csv for this "
                "standardized column; provenance is documented in the "
                "connector. See docs/data-provenance.md."
            ),
        })
        return record
    coverage = _clean(row.get("coverage_pct"))
    record.update({
        "source": _clean(row.get("source")),
        "series_id": _clean(row.get("series_id")),
        "license": _clean(row.get("license")),
        "direction": _clean(row.get("direction")),
        "anchor": _clean(row.get("anchor")),
        "coverage_pct": float(coverage) if coverage is not None else None,
        "register_note": sanitize_note(_clean(row.get("flags"))),
    })
    return record


def signal_role(domain_slug, table, column, domain_info):
    """Classify a signal as generating, defeater, or circular-flagged.

    Roles are read off the crosswalk, not invented here:
      * any signal listed in the domain's `circularity_signals` is flagged
        circular (it is an outcome/de-facto proxy, carried under a
        circularity caveat -- see the domain flags);
      * Foreclosed Exit is the load-bearing exception called out in the
        crosswalk: its only sourced signals are DEFEATERS (protective /
        attenuate-only), standing in for an unsourceable generating spine;
      * everything else is a generating (driver) signal. Note that several
        generating signals have a protective raw direction (low_risk) but are
        risk-aligned by the connector at standardize time -- direction is
        reported per-signal from the register.
    """
    circ = {tuple(s) for s in domain_info.get("circularity_signals", [])}
    if (table, column) in circ:
        return "circular-flagged"
    if domain_slug == "foreclosed-exit-structural":
        return "defeater"
    return "generating"


def build_domain(phase_slug, slug, name, note, register):
    info = cw.CROSSWALK[slug]
    if info["phase"] != phase_slug:
        raise ValueError(
            f"crosswalk phase mismatch for {slug}: "
            f"{info['phase']} != {phase_slug}")
    indicators = []
    for table, column in info["signals"]:
        rec = resolve_signal(table, column, register)
        rec["slug"] = column
        rec["path"] = f"{phase_slug}/{slug}/{column}"
        rec["role"] = signal_role(slug, table, column, info)
        indicators.append(rec)
    is_p1 = slug in cw.PRODUCT1_PHASES.get(phase_slug, [])
    return {
        "slug": slug,
        "path": f"{phase_slug}/{slug}",
        "name": name,
        "note": note,
        "confidence": info.get("confidence"),
        "product1_composite": is_p1,
        "product2_only": bool(info.get("product2_only", False)),
        "flags": [sanitize_note(fl) for fl in info.get("flags", [])],
        "indicators": indicators,
    }


def build_registry(register):
    phases = []
    domain_count = 0
    for p in PHASES:
        domains = []
        for slug, name, note in DOMAIN_META[p]:
            domains.append(build_domain(p, slug, name, note, register))
            domain_count += 1
        phases.append({
            "slug": p,
            "code": PHASE_CODES[p],
            "label": PHASE_LABELS[p],
            "product1_composite": p in cw.PRODUCT1_PHASES,
            "domains": domains,
        })
    return {
        "frozen": REGISTRY_FROZEN,
        "source": SOURCE,
        "indicator_layer": (
            "derived from pipeline/crosswalk.py + config/data_register.csv"
        ),
        "product1_composite": ["recruitment", "exploitation"],
        "product2_only": ["monetization"],
        "domain_count": domain_count,
        "phases": phases,
        "shared_gate": SHARED_GATE,
    }


def write_json(registry, path):
    with open(path, "w") as f:
        json.dump(registry, f, indent=2)
        f.write("\n")


def _fmt_coverage(cov):
    return f"{cov:g}%" if cov is not None else "n/a"


def write_md(registry, path):
    lines = []
    lines.append("# FLSRI codebook - domain & indicator registry\n")
    lines.append(
        f"The Phase \u2192 Domain structure is frozen. "
        f"Methodology source of record: "
        f"`{registry['source']}`; scoring rules: `docs/scoring-rules.md`; "
        f"per-source provenance: `docs/data-provenance.md`.\n")
    lines.append(
        "The Phase -> Domain structure is frozen. The indicator layer "
        "below is **derived** from `pipeline/crosswalk.py` (domain -> signal "
        "tuples) and `config/data_register.csv` (per-signal source, series, "
        "license, direction, anchor, coverage). Regenerate it by running "
        "`python3 codebook/build_registry.py`; it cannot drift from the "
        "pipeline it documents.\n")
    lines.append(
        f"Structure: 3 phases, {registry['domain_count']} domains, plus one "
        "shared corruption/capture gate that is folded into the domains it "
        "conditions (not a standalone domain).\n")
    lines.append(
        "**Product scope.** The published Product-1 composite is built over "
        "the Recruitment and Exploitation phases only. The Monetization phase "
        "(Domain A, Domain B) is a **Product-2 lens** and is excluded from the "
        "Product-1 composite.\n")
    lines.append(
        "Coverage is uneven by design. Several domains rest on partial, "
        "single-signal, defeater-only, or circularity-flagged inputs; these "
        "caveats are carried through from the crosswalk and the data register "
        "rather than smoothed over. Each domain reports its design `confidence` "
        "(`ok` / `low_confidence` / `insufficient_data`).\n")
    for phase in registry["phases"]:
        scope = ("Product-1 composite" if phase["product1_composite"]
                 else "Product-2 lens only (excluded from Product-1 composite)")
        lines.append(f"## {phase['label']} ({phase['code']})\n")
        lines.append(f"_{scope}._\n")
        for d in phase["domains"]:
            note = f" - _{d['note']}_" if d["note"] else ""
            lines.append(f"### {d['name']}{note}")
            lines.append(f"Path: `{d['path']}`  ")
            conf = d["confidence"] or "n/a"
            scope_d = ("Product-1" if d["product1_composite"]
                       else "Product-2 only")
            lines.append(
                f"Confidence: **{conf}** | Scope: {scope_d}\n")
            lines.append("Signals:\n")
            lines.append(
                "| Signal (table / column) | Role | Source | Series / column | "
                "Direction | Anchor | Coverage | License |")
            lines.append(
                "| --- | --- | --- | --- | --- | --- | --- | --- |")
            for ind in d["indicators"]:
                src = ind["source"] or "(see connector)"
                series = ind["series_id"] or "-"
                direction = ind["direction"] or "-"
                anchor = ind["anchor"] or "-"
                lic = ind["license"] or "-"
                cov = _fmt_coverage(ind["coverage_pct"])
                lines.append(
                    f"| `{ind['table']}/{ind['column']}` | {ind['role']} "
                    f"| {src} | `{series}` | {direction} | {anchor} | {cov} "
                    f"| {lic} |")
            lines.append("")
            if d["flags"]:
                lines.append("Design caveats:")
                for fl in d["flags"]:
                    lines.append(f"- {fl}")
                lines.append("")
    sg = registry["shared_gate"]
    lines.append("## Shared gate (all phases)\n")
    lines.append(f"### {sg['name']}")
    lines.append(f"Path: `{sg['slug']}`\n")
    lines.append(sg["note"] + "\n")
    gb = sg["governance_backbone"]
    lines.append(
        f"Governance backbone signal (scored once): "
        f"`{gb['table']}/{gb['column']}`.\n")
    with open(path, "w") as f:
        f.write("\n".join(lines))


def main():
    register = load_register(DATA_REGISTER)
    registry = build_registry(register)
    write_json(registry, os.path.join(HERE, "registry.json"))
    write_md(registry, os.path.join(HERE, "registry.md"))
    n_signals = sum(
        len(d["indicators"])
        for ph in registry["phases"] for d in ph["domains"])
    print(
        f"Wrote registry.json and registry.md "
        f"({registry['domain_count']} domains, "
        f"{len(registry['phases'])} phases, {n_signals} signals).")


if __name__ == "__main__":
    main()
