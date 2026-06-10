# FLSRI Scoring Rules (v1)

This is the canonical specification of the FLSRI scoring rules as **implemented in
the shipped pipeline**. The connector modules (`pipeline/sources/*.py`) and the
aggregation/composite modules (`pipeline/standardize.py`, `pipeline/aggregate.py`,
`pipeline/composite.py`) cite this document by rule number. This file is the
source of record for what those rule numbers mean; where a rule below differs
from a docstring, the **code is authoritative** and the docstring should be read
against this file.

Scope note: FLSRI publishes a **framework and pipeline**; this document
specifies the rules they implement. The per-country values produced by running
the pipeline are estimates of structural conditions with published uncertainty
bands (see the README scope note). The scoring *structure* passed a pre-registered validation suite,
re-checked on the displayed build (`docs/validation/`); a passing structure
does not promote the scores to findings. The rules below describe how those
values are computed. They should be read **as tiers, not exact ranks**, and
within the scope and caveats stated throughout this document.

A few rules describe choices that remain **open questions**. These are called
out explicitly in each rule and collected in the
[Open questions](#open-questions) section. The **governance treatment
(Rule 8)** is **not** among them: it was stress-tested and is robust. The published build handles governance
by **de-biasing** — down-weighting signals empirically too correlated with rule
of law and dropping the circular `v2xcl_slave` signal — and that result holds
across the de-biasing scope and the choice of governance reference signal (see
Rule 8). The older domain-level `(1 - f_gov)` attenuator described below is the
mechanism of the **`run.py` reproduction build**, not the published deliverable.

The pipeline stages map to the rules as follows:

| Stage | Module | Rules |
|---|---|---|
| Standardize each signal to 0–1 risk | `pipeline/standardize.py` | Rule 1, Rule 9 (signal-level) |
| De-correlation / collinearity screen | (data-stage; flagged, not auto-applied) | Rule 4 |
| Signals → domain (equal weight) | `pipeline/aggregate.py` | Rule 2 / Rule 5 |
| Governance handling — published: signal de-biasing in `build_v0_4.py`; reproduction: domain-level `(1 - f_gov)` attenuation in `pipeline/aggregate.py` | `pipeline/6_site_data/rebuild-v0.4/build_v0_4.py`, `pipeline/aggregate.py` | Rule 8 |
| Domains → phase (equal weight) | `pipeline/aggregate.py` | Rule 6 |
| Coverage floor / missing-data discipline | `pipeline/standardize.py`, `pipeline/aggregate.py`, `pipeline/composite.py` | Rule 9 |
| Phases → Product-1 composite (geometric mean) | `pipeline/composite.py` | Rule 7 |

A note on rule numbering: rules **1, 2/5, 4, 6, 7, 8, 9** are referenced by number
throughout the code and are specified below. There is **no Rule 3** surfaced in
the shipped pipeline; the number is intentionally left unassigned here rather
than invented. Rules 2 and 5 are documented together because the build collapses
the registry's per-domain indicator structure into a flat signal set, so both
reduce to the same equal-weight domain average (see Rule 2 / Rule 5).

---

## Rule 1 — Signal standardization to a common 0–1 risk scale

**Implemented in:** `pipeline/standardize.py` (`AnchorSpec`, `anchor_scale`,
`relative_scale`, `_resolve_direction`).

Every signal is mapped to a `[0, 1]` **risk** scale where **0 = lowest risk** and
**1 = highest risk**.

**Per-exposure base.** Where country size matters, the raw quantity is first put
on a per-exposure basis (per 100k, share of population, share of GDP, gendered
gap, etc.) *before* scaling, so that a 0–1 score is comparable across countries
of different sizes. This conversion happens in each connector before it calls the
scaler.

**Absolute anchoring (primary method).** The per-exposure value is mapped to 0–1
against **fixed, literature- or standards-anchored reference points** — a `floor`
(raw value mapping to 0.0) and a `ceiling` (raw value mapping to 1.0) — and then
**clamped to `[0, 1]`**. Formally, for a `high_risk` signal:

```
s = clamp01( (raw - floor) / (ceiling - floor) )
```

The `floor`/`ceiling` and their justification are carried on the `AnchorSpec`
(`floor`, `ceiling`, `unit`, `anchor_source`) and recorded in the data register
(see `docs/data-provenance.md`). Absolute anchors are the default because they
make scores stable across data refreshes and independent of the particular sample
of countries present.

**Relative fallback (justified exception only).** A robust **relative** mapping is
permitted **only** where a signal has no defensible absolute anchor, and it must
be recorded as such. `relative_scale()` supports:

- `winsor_minmax` — winsorize at the 5th/95th percentiles (default `winsor=0.05`),
  then min-max within the winsorized range; and
- `percentile` — percentile rank across present values.

Any relative-scaled signal is automatically tagged with a
`RELATIVE-FALLBACK (<method>) -- no absolute anchor; not comparable across
refreshes` flag in its metadata. This flag is a genuine methodological caveat:
relative-scaled signals are **not comparable across refreshes** and should be
read with that limitation in mind.

**Direction.** Each signal's direction is set explicitly, never inferred:

- `direction="high_risk"` (default): higher raw value = more risk, no inversion.
- `direction="low_risk"`: higher raw value = *less* risk, so the score is inverted
  (`s = 1 - s`) — used for protective signals (e.g. trade-union/collective-voice
  density, rule-of-law strength, passport visa-free access).

An explicit `invert=True/False` overrides `direction`. By the time signals reach
`pipeline/crosswalk.py` and `pipeline/aggregate.py`, **every** column is already
risk-aligned (higher = more risk), including columns whose raw source is
protective — the connector inverted them at standardize time. The `direction`
column of the data register records the original polarity per signal.

> `minmax()` and `standardize()` in `pipeline/standardize.py` are legacy
> helpers from an earlier scaffold and are unused by the shipped build. Real
> indicators use `anchor_scale` (absolute) or `relative_scale` (justified
> fallback), not these.

---

## Rule 2 / Rule 5 — Equal-weight aggregation of signals into a domain

**Implemented in:** `pipeline/aggregate.py` (`aggregate_country`, the
`rule 2/5 mean`).

Within each domain, the present 0–1 risk signals are combined by a **simple
equal-weight average**:

```
domain_raw = mean(present signal scores for the domain)
```

The frozen registry organizes each domain into a per-domain indicator/driver
structure; `pipeline/crosswalk.py` collapses that structure into a flat signal
set per domain. For v1, **every domain is a single equal-weight average of its
mapped 0–1 risk signals** — there is no within-domain weighting and no nested
sub-averaging. This is why Rule 2 (signal → indicator) and Rule 5 (indicator →
domain) reduce to the same operation in the build and are documented together.

The set of signals mapped to each domain is defined in
`pipeline/crosswalk.py` (`CROSSWALK`); the anchors and provenance for each signal
are in `docs/data-provenance.md`. Missing signals are dropped, not zero-filled
(Rule 9).

---

## Rule 4 — De-correlation / collinearity screen (flagged, not auto-applied)

**Referenced in:** `pipeline/sources/basel_fatf.py`,
`config/api-config/static_indices.yaml`.

Where two or more candidate signals are plausibly measuring the same underlying
construct (e.g. the financial-integrity / AML measures vs. the general-governance
backbone, or two corruption indices against each other), they are kept as
**distinct rows** and **flagged for a correlation / collinearity screen** at the
data stage. The surviving component count after that screen is a methods-review
call.

This is deliberately a **flag, not an automatic transformation**: no connector
merges or drops correlated signals on its own. The screen and the decision about
which components survive remain an **open question**, so as shipped the
pipeline carries the candidates through and surfaces the flag rather than
pre-resolving it.

---

## Rule 6 — Equal-weight aggregation of domains into a phase

**Implemented in:** `pipeline/aggregate.py` (the `rule 6` phase loop;
`pipeline/crosswalk.py` `PRODUCT1_PHASES`).

Within each Product-1 phase, the present **scored** domain scores are combined by
a **simple equal-weight average**:

```
phase_score = mean(scored domain scores in the phase)
```

The two Product-1 phases and their member domains are defined in
`pipeline/crosswalk.py` (`PRODUCT1_PHASES`):

- **Recruitment (R):** economic-precarity, debt-financialized-dependency,
  constrained-mobility, ascriptive-exclusion, legal-non-recognition,
  gender-structuring, age-childhood-structuring, structural-disruption.
- **Exploitation (E):** foreclosed-exit-structural, economic-structure-demand,
  state-production-of-unfreedom.

Domains that are **not scored** for a country (Rule 9) are excluded from the phase
average rather than entered as zero. The phase average is itself subject to the
Rule 9 coverage floor (see below).

Monetization domains are intentionally **not** part of any Product-1 phase (Rule
7).

---

## Rule 7 — Product-1 composite = geometric mean of Recruitment × Exploitation

**Implemented in:** `pipeline/composite.py` (`geometric_mean`,
`composite_scores`).

The Product-1 risk score for a country is the **equal-weight geometric mean** of
its Recruitment (R) and Exploitation (E) phase scores:

```
composite = sqrt( R_score * E_score )
```

The geometric mean is chosen as a **soft-conjunctive spine**: R and E are each
separately necessary for the modeled risk, so a low score in one phase pulls the
composite down more than an arithmetic mean would, without being a hard `min()`.

**Monetization is excluded (this is the core of Rule 7).** Monetization is a
**Product-2-only** lens and **never enters the Product-1 composite**. The two
Monetization domains are mapped in `pipeline/crosswalk.py` for completeness and
the Product-2 lens, but are excluded from Product-1 both at the phase layer (Rule
6) and here.

**No annihilation guard / baseline floor in v1.** There is intentionally no
baseline floor added to phase scores before the geometric mean. The rule is to
add an annihilation guard **only if** the index shows a phase score
zeroing out otherwise-informative composites — until that is demonstrated, no
guard is applied. This is a choice held as open questions.

**Missing-data behavior (Rule 9).** If **either** R or E is not-scored for a
country, the composite is **not-scored (`None`)** — there is no zero-substitution
and no annihilation. `geometric_mean()` returns `None` if any input is missing.
Phase scores that are legitimately `0.0` are kept and produce a `0.0` composite;
only *missing* phases produce `None`.

`rank()` orders only the countries with a non-`None` composite, by descending
risk; not-scored countries are dropped from the ranking (not ranked last).

---

## Rule 8 — Governance handling (de-biasing in the published build; `(1 - f_gov)` attenuation in the `run.py` reproduction)

There are **two builds and two mechanisms**. The published deliverable handles
governance by **de-biasing the signal set**; the `run.py` reproduction build uses
an older **domain-level `(1 - f_gov)` attenuator**. These are different
mechanisms in different code paths, and the rest of this rule keeps them
distinct.

### 8a — Published build: signal de-biasing (the deliverable)

**Implemented in:** `pipeline/6_site_data/rebuild-v0.4/build_v0_4.py`. This is the
build shown on the site.

The published build does **not** use a multiplicative `(1 - f_gov)` modulator at
all. Instead it **de-biases** the signal set so that the composite is not a
governance relabel:

- **Down-weight over-correlated signals.** Any signal whose per-signal governance
  R² against rule of law is **≥ 0.55** is treated as empirically too entangled
  with governance and is **down-weighted at `w = 0.5`**, applied within the
  **state-production-of-unfreedom** domain.
- **Drop the circular signal.** The V-Dem `v2xcl_slave` signal is **dropped
  entirely** as outcome-circular (a freedom-from-forced-labor proxy used to
  predict forced-labor risk).

After de-biasing, **governance still explains ~0.63 of the composite's
variance.** This residual is **expected and openly disclosed**, not an artifact:
weak governance is a genuine structural driver of forced-labor risk, so a
substantial — but bounded — governance association is *correct*. The de-biasing
removes the portion attributable to relabeling and circularity, not the genuine
structural signal.

### 8b — `run.py` reproduction build: domain-level `(1 - f_gov)` attenuator

**Implemented in:** `pipeline/aggregate.py` (`_governance_f`, applied in
`aggregate_country`); backbone defined in `pipeline/crosswalk.py`
(`GOVERNANCE_TABLE`, `GOVERNANCE_COLUMN` = `worldbank` /
`wb_wgi_rule_of_law`).

This mechanism belongs to the **`run.py` reproduction artifact**, a
separate and simpler build — **not** the published deliverable. There, governance
enters **exactly once**, as a **domain-level, attenuate-only** modulator — never
per signal and never per driver. For each domain that is scored for a country:

```
domain_score = (1 - f_gov) * domain_raw
```

where `f_gov` is a governance dial in `[0, 1]` derived from the single shared
World Bank WGI rule-of-law backbone (`wb_wgi_rule_of_law`). Because the factor is
`(1 - f_gov)` with `f_gov ∈ [0, 1]`, the modulator can **only attenuate** a raw
domain score toward 0 — it can never increase it. It is applied **once per
domain**, after the within-domain average (Rule 2/5) and before the phase average
(Rule 6).

**Missing governance → no attenuation.** If the rule-of-law value is missing for
a country, `f_gov` is treated as absent (`None`); no attenuation is applied
(`f_gov = 0` in effect), and this is recorded via `governance_applied = False`.
Missing governance never fabricates an attenuation. The dial follows the
framework's **protective reading**: strong rule of law attenuates risk, weak
governance attenuates little. (Historically there was a *polarity collision* here
— the literal as-locked formula and the risk-aligned data column disagreed once
the column polarity was pinned down — but that concerns the reproduction build's
attenuator, not the published index, and it is no longer the live framing.)

### 8c — Resolution: the governance treatment is robust

Governance polarity and magnitude are **not open questions**: the published
build's governance handling was stress-tested and is **robust** across the
design choices that critique would touch:

- **What was varied.** De-biasing **scope** (state-production-of-unfreedom only
  vs. all-domain de-biasing)
  was crossed with the **governance reference signal** used for flagging: World
  Bank WGI rule of law, V-Dem `v2x_rule`, or a 50–50 WGI+V-Dem blend.
- **What held.** The published index is robust to **all** of these: Kendall
  **τ ≥ 0.96** against the published build, governance **R² ≈ 0.63** throughout,
  **identical top-5 and bottom-5**, and the **Gulf/kafala result unchanged**.
- **Why this answers the critique.** Robustness is the strongest available reply
  to the "single-source WGI / governance relabel" objection: because V-Dem-only
  and the WGI+V-Dem blend reproduce the same index, the result does **not** depend
  on the particular governance measure or on the de-biasing scope.
- **Reproducible.** `build_v0_4.py` reads an `FLSRI_GOV_REF` environment variable
  (`wgi` default / `vdem` / `blend`) that selects the governance reference used
  for flagging, so each arm of the robustness check is re-runnable.

The honest bound stands: governance share is **~0.63**, bounded and disclosed —
a genuine structural driver, not an artifact.

---

## Rule 9 — Missing-data discipline and coverage floor

**Implemented in:** `pipeline/standardize.py` (`drop_and_average`, `_coverage`,
the `BELOW-COVERAGE-FLOOR` flags) and `pipeline/aggregate.py`
(`_coverage_floor_met`, the domain and phase coverage gates) and
`pipeline/composite.py` (composite not-scored if a phase is missing).

The governing principle: **missing data is never silently turned into a zero.**
Missing inputs stay missing (`None`); they are dropped from averages, and below a
coverage floor the result is marked low-confidence or not-scored — never
fabricated and never defaulted to 0.

**Drop-and-re-average.** Aggregates are computed over **present** inputs only. A
missing signal/domain is dropped from the average, not entered as 0.

**Coverage floor.** A combination is only treated as adequately covered if **at
least 50% of the mapped inputs are present AND never fewer than 2** — i.e. the
floor is `max(2, ceil(0.50 × n_total))`. In code this is
`COVERAGE_FRACTION_FLOOR = 0.50` and `COVERAGE_MIN_SIGNALS = 2`
(`pipeline/aggregate.py`), mirrored by `coverage_floor=0.5` / `min_present=2` in
`pipeline/standardize.py`.

- **At the signal-scaling layer** (`anchor_scale` / `relative_scale`): coverage is
  computed against the country universe; below-floor indicators get a
  `BELOW-COVERAGE-FLOOR (... ) -- low-confidence` flag but the present values are
  still returned.

- **At the domain layer** (`aggregate_country`): a domain is **scored** only if it
  has a value and the coverage floor is met. Below the floor, the domain is
  **not-scored** for that country (`score = None`, `coverage_flag =
  "not_scored_coverage"`) — flagged, never zeroed.

- **At the phase layer**: the same `>= 50% / >= 2` floor applies to the count of
  scored domains; below it the phase is not-scored.

- **At the composite layer** (`pipeline/composite.py`): if either phase is
  not-scored, the composite is not-scored (`None`).

**Single-signal-by-design domains.** A domain whose design has only **one** mapped
signal cannot mechanically clear the `>= 2` floor. These are handled as a named
exception: if that single signal is present, the domain **is** scored, but it is
always carried **low-confidence** (`coverage_flag = "low_confidence"`), consistent
with its crosswalk confidence flag. This is the `single_by_design` branch in
`aggregate_country`. It is a deliberate, flagged choice — not a silent override of
the floor.

**Design-level confidence flags are preserved.** Independent of per-country
coverage, each domain carries a design-confidence flag from the crosswalk —
`"ok"`, `"low_confidence"`, or `"insufficient_data"` — set from the accepted
data-maps/register and surfaced on every scored row. A domain flagged
`insufficient_data` or `low_confidence` by design carries that flag regardless of
how complete its per-country coverage happens to be. These flags, and the
free-text design caveats in the crosswalk (unsourced generating spines,
defeater-only sourcing, circularity, etc.), are genuine methodological caveats and
are intentionally retained.

**Circularity flags.** Signals identified as outcome-circular (e.g. a de-facto
"freedom-from-forced-labor" proxy used as an input to a forced-labor risk index)
are flagged with a `circularity_flag` on the domain and surfaced rather than
silently scored. These flags are preserved as honest limitations.

---

## Open questions

These are the choices that are **not settled** and are held as open questions wherever the pipeline applies them. **Governance handling (Rule 8)
is no longer on this list:** it was stress-tested and is resolved and
robust — see Rule 8c.

1. **Rule 4 — de-correlation / collinearity screen.** Which correlated components
   survive (financial-integrity vs. governance backbone; corruption indices vs.
   each other) is flagged but not auto-resolved; the surviving component count is
   a methods-review call.
2. **Rule 7 — annihilation guard / baseline floor.** No baseline floor is applied
   in v1. Whether one is needed depends on whether the index shows a
   phase zeroing otherwise-informative composites. An open question.
3. **Foreclosed-exit (Exploitation).** The only sourced signals for this domain
   are protective defeaters (collective-voice / inspection series); the named
   generating spine (monopsony / exit-cost) is unsourceable at full country scale.
   The domain is carried `insufficient_data` as a stand-in; whether it should be
   scored standalone-low-confidence or folded in is unresolved.

---

## Related documents

- `docs/METHODS.md` — overall methodology and source-of-record.
- `docs/data-provenance.md` — per-source provenance, anchors, licenses, and
  coverage for each signal (the data register).
- `pipeline/standardize.py`, `pipeline/aggregate.py`, `pipeline/composite.py`,
  `pipeline/crosswalk.py` — the implementing code, which is authoritative where
  it differs from prose.
