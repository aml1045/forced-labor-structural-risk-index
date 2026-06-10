# Forced Labor Structural Risk Index (FLSRI)

A country-level index, scored 0–1, of the **structural conditions** under which
forced labor becomes more likely. It is **not** a count or estimate of cases — it
measures the conditions that enable them. ~195 countries are organized as
**Phase → Domain → Indicator** across three phases (Recruitment, Exploitation,
Monetization) and combined into one composite per country.

The deliverable is the interactive site in [`public/`](public/), served via
GitHub Pages (or any static server). The full pipeline that produces it — every
transformation, the scoring, the uncertainty model — is in this repository.

> **Scope.** What this repository publishes — and what you should cite — is the
> framework, pipeline, and code. The scoring structure passed a pre-registered
> validation suite (Methods §8; machine-readable verdicts in
> [`docs/validation/`](docs/validation/)). The country scores are estimates of
> structural *conditions*, not measurements of *prevalence*: they ship with
> explicit uncertainty bands and are designed to be read in **tiers**, not as
> exact positions. Read the **Limitations** below before using any single rank.

## What the pipeline does

1. **Standardizes** each indicator to a common 0–1 *risk* scale, anchored to
   fixed, literature-based reference points (not the worst observed country),
   with direction set per signal — [`docs/scoring-rules.md`](docs/scoring-rules.md)
   (Rule 1).
2. **Aggregates** signals → domains → phases by equal-weight averaging of the
   inputs that are present (Rules 5–6), with a **coverage floor** that leaves a
   country *not scored* rather than guessing when data are too thin (Rule 9) —
   missing is never treated as zero.
3. **De-biases** governance-correlated signals where they would otherwise
   double-count the governance axis (Rule 8; Methods §6).
4. **Composes** the headline score as the **geometric mean of Recruitment ×
   Exploitation** (Rule 7), which penalizes imbalance. **Monetization is
   computed but excluded** from the composite (it is an intervention lens).
5. **Quantifies uncertainty**: every scored country carries a 90% rank band
   from 10,000 seeded Monte-Carlo re-scorings, and countries whose score rests
   on a reduced evidence base are flagged lower-confidence — the same rule
   that widens their noise model drives the badge on the site.

184 of ~195 countries are scored; the rest are reported as unscored rather than
assigned a misleading value.

## Layout

```
public/          the interactive site — the deliverable (pages, assets, data)
codebook/        the domain registry + codebook (registry.json / registry.md)
config/          country list, indicator config, and the data register
pipeline/        sources → standardize → crosswalk → aggregate → composite
  6_site_data/   the published scorer + site-data builders (public/data/*)
data/            processed 0–1 indicator tables (one CSV per source connector,
                 standardized against fixed anchors) + cached aux inputs
external/        pinned experiment tables the published scorer reads as inputs
outputs/         validation + Monte-Carlo code (score CSVs are regenerated per run)
docs/            METHODS.md, scoring-rules.md, data-provenance.md, REPRODUCING.md
```

## Reproduce the build

```
python build_all.py all
```

One command, offline by default: it rebuilds the indicator layer from the
pinned inputs on disk, scores, regenerates every site data file, and verifies
the result against the published baseline — aborting on any unsanctioned
drift. [`docs/REPRODUCING.md`](docs/REPRODUCING.md) is the full protocol
(environment pins, determinism notes, and the checklist for the gated inputs
that cannot be redistributed). `python run.py` alone runs a lighter
reference build (a 13-domain reproducibility harness kept separate from the
11-domain index the site displays; Methods §10).

## Methodology & data

- **Methods:** [`docs/METHODS.md`](docs/METHODS.md) — framework, standardization,
  weighting, aggregation, governance handling, missing-data rules, validation,
  and limitations. The locked scoring rules are in
  [`docs/scoring-rules.md`](docs/scoring-rules.md).
- **Codebook:** [`codebook/registry.md`](codebook/registry.md) — the domains and
  the real signals behind each, generated from the pipeline crosswalk.
- **Data provenance:** [`docs/data-provenance.md`](docs/data-provenance.md) — the
  per-indicator source, vintage, coverage, licence, and citation. The required
  attributions and the two redistribution-restricted sources (EM-DAT,
  IPUMS-International) are recorded there, per source.

## Limitations (read before using any single rank)

- **It reads origin-side structural risk, and under-reads destination/sponsorship
  systems.** The *kafala*-style tied-status, recruitment-debt, and brokerage
  mechanisms are named but **not yet sourced at country scale**, so several wealthy
  migrant-*destination* states (incl. the Gulf) score low despite well-documented
  risk. A low score for a known destination means "this index does not yet capture
  that pathway," not a clean bill of health.
- **It is correlated with weak governance by design** (≈⅓ of variation is not
  governance); the de-biasing applied to governance-correlated signals is
  documented and stress-tested in Methods §6.
- **The validation covers the structure, not the individual scores.** The
  criteria and failure thresholds were fixed in advance and re-run on the
  displayed build (verdict artifacts in [`docs/validation/`](docs/validation/));
  per-country values remain estimates with published uncertainty bands.
- **There is no clean external prevalence benchmark** to validate against.

The full statement is on the site's
[Limitations page](public/pages/limitations.html).