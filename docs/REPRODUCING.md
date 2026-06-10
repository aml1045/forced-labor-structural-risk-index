# Reproducing the FLSRI build

One command rebuilds everything from the pinned inputs on disk — no network:

```
python build_all.py all                  # indicators -> score -> site -> verify -> report
python build_all.py all --skip-indicators   # trust the tracked data/processed/ layer (fast)
```

`all` aborts on the first failing stage; `verify` runs `site_data_verify.py
--strict` against `FLSRI_BASELINE_DATA` (default: the published `public/data/`)
and exits non-zero on any unsanctioned drift.

## Environment

- **Python 3.11** (`.python-version`; the verified env is `.venv311`). Install
  `requirements.txt` — the pins are the exact versions the
  byte-identical reproduction was verified under.
- **Node ≥ 18** with **mapshaper pinned by `package.json`** (`npm ci`).
  mapshaper's simplification output is version-dependent; the pin (0.7.22) is
  what makes `admin1_risk.topojson` / `lisa_admin1.topojson` reproducible.
- Gated inputs: `python build_all.py check` (or `python script/check_manual.py`)
  prints exactly what is present/missing with a download checklist. The
  manifest is `config/manual_inputs.yaml`. The IPUMS-derived aggregate tables
  (`external/experiments/ipums-signals/*.csv` and the IPUMS codebook) are
  local-only under IPUMS conditions of use and are not redistributed; without
  them the IPUMS sensitivity arm and the validation suite's FL-proximate
  benchmark are skipped, and the shipped scores are unaffected.

## Determinism

- All stochastic steps are seeded: site-data Monte-Carlo rank bands seed
  `20260602` (`pipeline/6_site_data/uncertainty.py`), `outputs/validate.py`
  `20260601`, `outputs/validate_v2.py` `20260602`, LISA permutations seed `42`
  (`compute_lisa.py`).
- `scores.json` carries a `build_date`; set `FLSRI_BUILD_DATE` to pin it when
  byte-comparing builds across days.
- Verified: two consecutive runs of the scores chain produce
  sha256-identical staged `scores.json`; the full site chain reproduces every
  staged file byte-identical to the published baseline (`site_data_verify.py`
  output: 7/9 sha-identical, the other 2 within documented tolerance — the
  overlay enrichment and a key-order-only delta).

## The pinned-input model

The offline build's inputs are pinned three ways:

1. **Tracked layer** — `data/processed/*.csv` + `config/data_register.d/*.csv`
   are committed; `build_all.py indicators` re-runs only connectors verified to
   reproduce them byte-for-byte and HARD-FAILS if any connector changes the
   data layer (the drift gate).
2. **Caches** — `data/aux/*` / `data/raw/unhcr_cache.csv` are the raw pins for
   the API connectors. Live pulls now **write back** (`pipeline/raw_cache.py`),
   stamping `data/aux/cache_manifest.json`, so cache and processed layer move
   together on a refresh and `--cache` runs reproduce the refreshed data
   exactly (round-trip verified for the World Bank family).
3. **Input manifest** — `build_all.py report` writes
   `outputs/input-manifest-<date>.json` with the sha256 of every consumed
   gitignored input; commit it alongside a release so a reproducer can prove
   they hold the same snapshot.

## Refreshing data (network, deliberate)

```
python build_all.py refresh      # or: all --refresh
```

re-pulls every connector live (UNHCR via `--refresh`; UNCTAD needs
credentials), writes the caches back, and emits
`outputs/refresh-report-<date>.md` listing every indicator whose countries /
vintage / coverage moved. Adopting a refresh = committing `data/processed/`,
`config/data_register.d/`, and the report together. Until then, the offline
build keeps reproducing the previous pinned state.

Connectors with no offline path yet (`ilostat`, `gender_structuring`,
`econ_structure_demand`, `monetization_b`'s data360 component, `aux_unctad`)
are pinned solely by their tracked processed CSVs; adding per-connector caches
for them is known future work.

## Publication (manual, deliberate — not automated)

The build chain ends at `outputs/site_data_staging/`. Publishing remains a
human decision: copy the reviewed staging files into `public/data/`, regenerate
`public/bundle.html` (`python public/build-bundle.py`), then sync the mirror
per `FLSRI-project/README.md` (rsync to `flsri-demo`, leak-check, commit,
push). The license gates recorded in `docs/data-provenance.md` must be
cleared before any public release.
