# FLSRI — Methods

Technical reference for the Forced Labor Structural Risk Index. This
note integrates the framework, the standardization and aggregation rules, the
validation, and the limitations into one document. The numbered scoring rules it
refers to are specified in [`scoring-rules.md`](scoring-rules.md); the per-indicator
sources are in [`data-provenance.md`](data-provenance.md); the domains and their
real signals are in [`../codebook/registry.md`](../codebook/registry.md).

## 1. Construct and theoretical framework

FLSRI operationalizes the **structural risk of forced labor** — the standing
conditions under which forced labor becomes more likely — as distinct from its
*prevalence* (how many people are actually exploited). The framework reads
exploitation through a routine-activity lens: harm becomes likely when a motivated
actor, a vulnerable target, and the absence of a capable guardian coincide. This
maps onto three phases:

- **Recruitment** — the vulnerability that exposes populations (economic precarity,
  debt dependence, constrained mobility, ascriptive exclusion, legal non-recognition,
  gender and age structuring, structural disruption).
- **Exploitation** — the conditions under which exploitation runs unchecked
  (foreclosed exit, demand-side economic structure, state production of unfreedom).
- **Monetization** — the financial conditions under which proceeds are hidden.

The domain set is broadly aligned with the vulnerability dimensions of the Walk Free
Global Slavery Index and the ILO indicators of forced labor, and the design is
deliberately disciplined against circularity (outcome-proxy and trafficking-prevalence
inputs were excluded where they would predict forced labor with a measure of forced
labor). Each domain resolves into concrete signals drawn from real datasets; see the
codebook.

## 2. Architecture

13 domains across the three phases (8 Recruitment, 3 Exploitation, 2 Monetization).
The **published composite is computed over two of the three phases** — Recruitment
and Exploitation. **Monetization is computed but excluded** from the composite: it
answers a different (intervention / "where do proceeds flow") question and scores
high for wealthy, financially-opaque demand-side economies in a way that would muddy
a structural-risk read.

## 3. Standardization (Rule 1)

Indicators arrive in incompatible units. Each is rescaled to a common 0–1 **risk**
scale by absolute anchoring — `(value − floor) / (ceiling − floor)`, clamped to
[0,1] — with floors/ceilings set from theory or native scale endpoints rather than
the observed sample, which keeps the scale stable as coverage changes. Direction is
set per signal (`high_risk` / `low_risk`, the latter inverted), so every signal points
the same way (higher = more risk). A winsorized relative fallback exists for signals
with no defensible absolute anchor and is flagged as not-comparable-across-refreshes;
it is effectively unused in the shipped build.

*Caveat:* several anchors are distribution-derived
(e.g. a ceiling near the 95th percentile), which smuggles some sample-relativity into
a nominally absolute scale and inherits min-max's outlier sensitivity through the hard
clamp. An anchor-shift sensitivity pass is a known refinement for these distribution-derived anchors.

## 4. Weighting

Equal weights are used at every level. This is a **value judgment, not a neutral
default**: it encodes an a-priori claim of equal construct relevance. Because
domains nest unequal numbers of signals and phases nest unequal numbers of domains,
equal-at-each-level produces unequal *implicit* per-signal weights (a signal in a
one-signal domain carries more composite weight than one in a five-signal domain;
any Exploitation signal carries more than a Recruitment signal because E has fewer
domains). Phase-balance sensitivity (60/40 either way) barely moves ranks; signal-
and domain-level weight perturbation is not yet reported and is a known gap.

## 5. Aggregation (Rules 5–7)

Signals are combined to domains, and domains to phases, by **arithmetic** mean of
the inputs present (fully compensatory within a level). The two phases are combined
into the composite by a **geometric mean of Recruitment × Exploitation** (Rule 7),
which is partially non-compensatory: a country scores high only when *both* an
exposed population and an unchecked-exploitation environment are present, rather than
trading one off against the other. The operator boundary — compensatory within/among
domains, conjunctive between phases — reflects the claim that the two phases are
separately necessary while domains within a phase are substitutable manifestations of
the same condition. There is no annihilation floor in this build; a missing phase
yields a not-scored composite (never a zero substitution).

## 6. Governance handling (Rule 8)

**Two builds, two mechanisms.** The published build and the `run.py` reproduction
handle governance differently, and the distinction matters.

The **published build** (built in
`pipeline/6_site_data/rebuild-v0.4/build_v0_4.py`) handles governance by
**de-biasing the signal set**, not by a multiplicative modulator. Signals whose
per-signal governance R² against rule of law is **≥ 0.55** are down-weighted at
`w = 0.5` within the state-production-of-unfreedom domain, and the circular V-Dem
`v2xcl_slave` signal is dropped entirely. There is **no `(1 − f_gov)` term** in
the published build. After de-biasing, governance still explains **~63%** of score
variance — a residual that is **expected and openly disclosed**: weak governance
is a genuine structural driver of forced-labor risk, so a bounded governance
association is correct rather than an artifact.

The older single domain-level governance term — `domain_score = (1 − f_gov) × raw`,
with `f_gov` derived from the WGI rule-of-law signal (the "corruption / capture
gate") — is the mechanism of the **`run.py` reproduction build**
(`pipeline/aggregate.py`), a separate and simpler reproducibility artifact, **not**
the published deliverable.

The governance treatment was previously flagged as an
unresolved open question (polarity and magnitude); it has since been stress-tested
and is **robust**. De-biasing scope (state-production-only vs. all-domain) was crossed with the
governance reference signal (WGI rule of law, V-Dem `v2x_rule`, or a 50–50 blend),
and the published index is robust to all of these: Kendall **τ ≥ 0.96** vs. the
published build, governance **R² ≈ 0.63** throughout, identical top-5 and bottom-5,
and the Gulf/kafala result unchanged. Because V-Dem-only and the blend reproduce
the same index, the result does not depend on the choice of governance measure or
de-biasing scope — the strongest available answer to the single-source-WGI /
governance-relabel critique. The check is reproducible via the `FLSRI_GOV_REF`
environment variable (`wgi` / `vdem` / `blend`) read by `build_v0_4.py`.

## 7. Missing data and coverage (Rule 9)

No imputation and no missing-to-zero. A domain is scored only if coverage clears a
floor (`max(2, 50% of mapped signals`); below it the domain is *not scored*. The same
floor gates phases; a missing phase yields a not-scored composite. 184 of ~195
countries are scored. **The missingness is not random:** the 11 unscored countries are
all micro-states / small-island states for which the labor and governance series are
not collected (MNAR), and within the scored set thin coverage tends to depress a score
rather than inflate it — so very-low scores for data-sparse states warrant extra
caution. Per-domain low-confidence and not-scored flags are published per country.
The reduced-evidence cases now carry through to a published uncertainty
statement: every scored country ships a Monte-Carlo 90% rank band
(p5/p50/p95, 10,000 seeded draws; `pipeline/6_site_data/uncertainty.py`), and
countries whose composite rests on **2 or more unscored domains** (42 of 184)
get both a wider noise sd in that model and a "lower confidence" badge on the
site — one rule driving both statements. The flags still do not re-weight the
domain average itself (deliberate; weights remain equal and disclosed).
The tier cuts used everywhere (0.281 / 0.402) are **frozen at the
registration-stage terciles** and not re-derived per build — fixed banding
thresholds cannot be tuned after seeing a build's results; the displayed
build's own terciles would be 0.274 / 0.397, and nine boundary countries
would shift tier under re-derivation (the registration verdict is archived
in [`validation/`](validation/)).

One coverage gap deserves naming: the child-labor prevalence indicator
(WDI `SL.TLF.0714.ZS`, survey vintages 2005–2016) covers only 92 of 195
countries, below the 50% floor. Consistent with the no-imputation rule the gap
is disclosed, not filled.

## 8. Validation

A pre-registered suite (criteria fixed in advance) was run and returned an overall
pass: discriminant validity (the composite is governance-
*associated* but not a governance relabel), incremental forced-labor structure (a
governance-residualized child-labor signal remains significant), internal structure
(R and E related but not redundant), and rank stability (top/bottom deciles stable;
mid-table not — hence tier reporting).

The suite has been re-run on the build the site **displays**
(`outputs/validate_v2.py --build v0_4_spu_w05`), and the pre-registered criteria
pass on the displayed index, not merely on a closely-related structure:
governance R² = **0.628** (r = 0.793; PASS ≤ 0.80), recruitment–exploitation
correlation **0.659 Pearson / 0.694 Spearman** (PASS, within 0.30–0.90),
incremental forced-labor structure PASS (IPUMS child-labour residualized
Spearman 0.362, BH q = 0.016 at spatial effective-df), and top-decile retention
**0.865** under mild noise (PASS ≥ 0.70). The machine-readable verdicts are
archived in [`validation/`](validation/) for both the registration
structure and the displayed build. (Note: a separate 13-domain reproducibility
build emitted by `run.py` is more governance-dominated than the published
index; it is a reproducibility artifact, not the published build.)

**Governance-handling robustness.** The displayed build's governance
de-biasing was stress-tested across its design choices: de-biasing scope (state-production-only
vs. all-domain) crossed with the governance reference signal (WGI rule of law,
V-Dem `v2x_rule`, or a 50–50 WGI+V-Dem blend). The published index is robust to all
of these — Kendall **τ ≥ 0.96** against the published build, governance **R² ≈ 0.63**
throughout, identical top-5 and bottom-5, and the Gulf/kafala result unchanged. This
directly answers the single-source-WGI / governance-relabel concern: V-Dem-only and
the blend give the same index, so the result does not depend on the governance measure
or the de-biasing scope. The arms are reproducible via the `FLSRI_GOV_REF` environment
variable read by `build_v0_4.py`.

**The most important caveat:** there is no clean, governance-independent external
measure of forced-labor *prevalence* to validate against. Tested against an external
prevalence estimate (the Walk Free Global Slavery Index 2023, per-1,000 prevalence)
net of governance on both sides, no significant association was demonstrated; because
the benchmark is itself heavily governance-entangled, that null is uninformative
rather than disconfirming. (The GSI workbook is license-gated and not stored in the
repo, so archived re-runs report this check as NOT RUN; the original run, with the
workbook present, returned the not-demonstrated result described here.) That absence
is the gap a structural index exists to fill.

## 9. Construct-validity limitations

- **Origin-side scope / the Gulf result.** The index is built largely from indicators
  describing resident populations and structural conditions. The defining mechanisms
  of *destination*-side forced labor — *kafala* tied legal status, recruitment-fee
  debt, brokerage — are named but **unsourced at country scale**, and the available
  proxies describe citizens, not the migrant workforce. As a result several wealthy
  destination states (the UAE ranks 147/184; the Gulf bloc sits with it) score low
  despite well-documented risk. The current index reads origin-side structural
  vulnerability, not destination sponsorship capture; sourcing those drivers is the
  next priority.
- **Thin exploitation phase.** Foreclosed-exit has no direct measure of its core
  mechanism (it is carried by labor-enforcement proxies / defeaters), and
  state-production leans partly on a de-facto governance/outcome proxy flagged for
  circularity; the phase rests heavily on one well-measured domain.
- **Cross-phase double-counting.** Informality and sector-share enter both R and E;
  the promised collinearity screen should be run and reported.

## 10. Builds in this repository

One structure, two build artifacts: the domain **structure** is frozen in the
registry; `run.py` emits a 13-domain reproducibility build; and the **site
displays** the validated 11-domain index, on which the pre-registered suite
passes. The published figures, the validated structure, and the `run.py` build
are reconciled above rather than presented as one number.

## 11. Reproducibility and provenance

**One-command rebuild:** `python build_all.py all` (offline, from the pinned
inputs; verification gate included). The full reproduction protocol — pins,
seeds, the drift gate, cache write-back, and the refresh workflow — is in
[`REPRODUCING.md`](REPRODUCING.md).

The connectors in `pipeline/sources/` and the standardization in
`pipeline/standardize.py` are tracked; roughly two-thirds of sources re-pull from
public APIs. Several file-based sources (V-Dem, Findex, EM-DAT, Basel, and the static
TRACE/Henley parses) depend on inputs that are registration- or licence-gated and are
**not** bundled; obtain them and set `FLSRI_EXTERNAL_DATA` to a local directory to
rebuild the site data. Per-source acquisition, vintage, coverage, licence, and
citation are in [`data-provenance.md`](data-provenance.md); the
redistribution-restricted sources (EM-DAT, IPUMS-International) are not bundled
and are recorded, with their terms, in [`data-provenance.md`](data-provenance.md).

## 12. Open questions

1. Whether foreclosed-exit is best reported standalone, folded into a
   neighboring domain, or held as a diagnostic.
2. Whether uncertainty should propagate into the aggregation *weights*
   themselves. The published bands quantify input noise and the R-vs-E
   balance (§7); weight-level uncertainty is deliberately not modeled.
3. Locking the distribution-derived normalization anchors and adding an
   anchor-shift sensitivity pass.
4. Sourcing the destination-side (kafala / tied-status / brokerage) drivers, or
   formally scoping the construct label to origin-side structural vulnerability.
5. Whether any governance-independent component of the Monetization lens could
   justify inclusion in the composite. A residualization analysis (regressing
   the lens on the governance backbone and testing the orthogonal remainder
   against the pre-registered criteria) found the residual carries no
   forced-labor-specific signal and dilutes the composite's incremental
   child-labour association; the lens therefore remains display-only.
   Re-opening this question would require new monetization-side data, not
   re-weighting of the existing signals.
