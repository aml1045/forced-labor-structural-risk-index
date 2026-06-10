# FLSRI codebook - domain & indicator registry

The Phase → Domain structure is frozen. Methodology source of record: `docs/METHODS.md`; scoring rules: `docs/scoring-rules.md`; per-source provenance: `docs/data-provenance.md`.

The Phase -> Domain structure is frozen. The indicator layer below is **derived** from `pipeline/crosswalk.py` (domain -> signal tuples) and `config/data_register.csv` (per-signal source, series, license, direction, anchor, coverage). Regenerate it by running `python3 codebook/build_registry.py`; it cannot drift from the pipeline it documents.

Structure: 3 phases, 13 domains, plus one shared corruption/capture gate that is folded into the domains it conditions (not a standalone domain).

**Product scope.** The published Product-1 composite is built over the Recruitment and Exploitation phases only. The Monetization phase (Domain A, Domain B) is a **Product-2 lens** and is excluded from the Product-1 composite.

Coverage is uneven by design. Several domains rest on partial, single-signal, defeater-only, or circularity-flagged inputs; these caveats are carried through from the crosswalk and the data register rather than smoothed over. Each domain reports its design `confidence` (`ok` / `low_confidence` / `insufficient_data`).

## Recruitment (R)

_Product-1 composite._

### Economic Precarity
Path: `recruitment/economic-precarity`  
Confidence: **ok** | Scope: Product-1

Signals:

| Signal (table / column) | Role | Source | Series / column | Direction | Anchor | Coverage | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `recruitment_econprecarity/ep_poverty_headcount_685` | generating | World Bank WDI | `SI.POV.UMIC` | high_risk | [0.0, 100.0] % of population below $6.85/day (2017 PPP) | 87.2% | CC BY 4.0 |
| `recruitment_econprecarity/ep_informal_employment_share` | generating | ILOSTAT (ILO, rplumber API) | `SDG_0831_SEX_ECO_RT_A (TOTAL/SEX_T)` | high_risk | [0.0, 100.0] % of total employment that is informal (SDG 8.3.1) | 73.3% | CC BY 4.0 (ILO open data) |
| `recruitment_econprecarity/ep_agrarian_employment_share` | generating | World Bank WDI | `SL.AGR.EMPL.ZS` | high_risk | [0.0, 80.0] % of total employment in agriculture | 91.8% | CC BY 4.0 |
| `recruitment_econprecarity/ep_income_volatility` | generating | World Bank WDI | `NY.GDP.MKTP.KD.ZG` | high_risk | [0.0, 8.0] std-dev of annual real GDP growth (pct points), recent ~15-yr window | 99% | CC BY 4.0 |
| `worldbank/wb_gini` | generating | World Bank WDI + WGI | `SI.POV.GINI` | high_risk | [20.0, 65.0] Gini index (0-100) | 87.2% | CC BY 4.0 |

Design caveats:
- D3 agrarian rests on single agrarian-concentration signal (S3a landlessness unsourced); D5 volatility not yet residual-scoped.

### Debt & Financialized Dependency - _connecting-flagged_
Path: `recruitment/debt-financialized-dependency`  
Confidence: **low_confidence** | Scope: Product-1

Signals:

| Signal (table / column) | Role | Source | Series / column | Direction | Anchor | Coverage | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `findex/findex_account_exclusion` | generating | World Bank Global Findex Database 2025 | `GlobalFindexDatabase2025.csv:account_t_d(1-x)` | high_risk | [0.0, 1.0] share of adults with no financial account (0-1) | 81% | CC BY 3.0 IGO (World Bank open data; attribution required) |
| `findex/findex_informal_borrowing` | generating | World Bank Global Findex Database 2025 | `GlobalFindexDatabase2025.csv:fin22b+fin22c` | high_risk | [0.0, 1.0] share of adults borrowing from informal sources (0-1) | 59% | CC BY 3.0 IGO (World Bank open data; attribution required) |
| `findex/findex_borrow_prevalence` | generating | World Bank Global Findex Database 2025 | `GlobalFindexDatabase2025.csv:borrow_any_t_d` | high_risk | [0.0, 1.0] share of adults who borrowed any money in the past year (0-1) | 59% | CC BY 3.0 IGO (World Bank open data; attribution required) |

Design caveats:
- Lead driver R-D1 (recruitment-fee/migration debt) UNSOURCED; domain rests on R-D2 + R-D3 (Findex). R-D3 borrowing = penetration PROXY, not DSTI distress. 59% borrowing-module coverage.

### Constrained Mobility - _connecting-flagged_
Path: `recruitment/constrained-mobility`  
Confidence: **low_confidence** | Scope: Product-1

Signals:

| Signal (table / column) | Role | Source | Series / column | Direction | Anchor | Coverage | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `vdem/v2xcl_dmove` | generating | V-Dem Country-Year Core v16 | `v2xcl_dmove` | low_risk | [0.0, 1.0] interval 0-1 (higher = freer) | 88.7% | CC BY 4.0 |
| `static_indices/henley_passport_access` | generating | Henley Passport Index (May 2026 vintage) | `henley_passport_index_count_2026-05-07.csv:Visa Free` | low_risk | [0.0, 227.0] visa-free destination count | 100% | UNCONFIRMED for public release -- RE-PUBLICATION-UNCONFIRMED (Henley proprietary, attribution required, re-publication rights unclear; confirm before release) |
| `unhcr/unhcr_refugees_by_coo` | generating | UNHCR Population Statistics API | `unhcr_refugees_by_coo` | high_risk | [0.0, 5000.0] persons per 100k population | 99% | Open / CC BY (attribution required) |

Design caveats:
- D1 + D4 only (2 of 4 drivers); D2 kafala + D3 brokerage UNSOURCED. Henley RE-PUBLICATION-UNCONFIRMED (license unconfirmed).

### Ascriptive Exclusion - _legal cluster split out_
Path: `recruitment/ascriptive-exclusion`  
Confidence: **low_confidence** | Scope: Product-1

Signals:

| Signal (table / column) | Role | Source | Series / column | Direction | Anchor | Coverage | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `epr/epr_excluded_pop_share` | generating | ETH-Zurich Ethnic Power Relations (EPR) Core 2023 | `EPR-2023 Core: status x size, snapshot 2023` | high_risk | [0.0, 0.5] depth-weighted share of population in politically-excluded ethnic groups | 88.2% | Academic / open; downloadable at icr.ethz.ch -- redistribution terms under confirmation (see docs/data-provenance.md) |

Design caveats:
- Single EPR signal (political-exclusion only; under-captures labor-market caste/ethnic channeling). ~41 countries off EPR universe stay MISSING (not 0). EPR license-verify before publish.

### Legal Non-Recognition - _CRVS / birth-registration backbone_
Path: `recruitment/legal-non-recognition`  
Confidence: **ok** | Scope: Product-1

Signals:

| Signal (table / column) | Role | Source | Series / column | Direction | Anchor | Coverage | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `legal_non_recognition/lnr_birth_registration_incompleteness` | generating | World Bank WDI (UNICEF/UNSD CRVS / SDG 16.9.1 birth-registration completeness) | `SP.REG.BRTH.ZS` | low_risk | [0.0, 100.0] % of births registered (completeness) | 91.3% | CC BY 4.0 |
| `legal_non_recognition/lnr_statelessness_prevalence` | generating | UNHCR Population Statistics API (re-used via data/processed/unhcr.csv) | `unhcr_stateless_by_country` | high_risk | [0.0, 5000.0] persons per 100k population (re-used from UNHCR connector) | 93.3% | Open / CC BY (attribution required) |

Design caveats:
- 26 single-driver rows are low-confidence (statelessness dropped/suppressed -> rests on birth-reg alone); never zeroed.

### Gender Structuring - _generating face; modulating face routed to modifiers_
Path: `recruitment/gender-structuring`  
Confidence: **ok** | Scope: Product-1

Signals:

| Signal (table / column) | Role | Source | Series / column | Direction | Anchor | Coverage | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gender_structuring/gs_lfp_gender_gap` | generating | World Bank (Gender Statistics / WDI / Women Business and the Law) | `SL.TLF.CACT.MA.ZS - SL.TLF.CACT.FE.ZS (male-minus-female LFP)` | high_risk | [0.0, 50.0] male-minus-female labour-force-participation gap (pct points, pop 15+) | 91.8% | CC BY 4.0 |
| `gender_structuring/gs_gender_inequality_index` | generating | UNDP Human Development Report -- Gender Inequality Index (via Our World in Data republication) | `UNDP HDR Gender Inequality Index (OWID full CSV)` | high_risk | [0.0, 1.0] UNDP Gender Inequality Index (0 = equal, 1 = maximally unequal) | 88.2% | UNDP HDR (publish with attribution); OWID data CC BY 4.0 -- confirm UNDP HDR re-publication terms before public release |
| `gender_structuring/gs_mobility_constraint` | generating | World Bank (Gender Statistics / WDI / Women Business and the Law) | `GD_WBL_MOB_LAW_T (WBL Legal Framework, Mobility score)` | low_risk | [0.0, 100.0] WBL Legal Framework, Mobility score (0-100) | 95.4% | CC BY 4.0 |
| `gender_structuring/gs_sex_sector_channel_share` | generating | ILOSTAT (ILO, rplumber API) | `EMP_TEMP_SEX_ECO_NB_A (sex x economic activity)` | high_risk | [0.0, 0.7] max(sex-specific exploitation-exposed-sector employment share), 0-1 | 96.4% | CC BY 4.0 (ILO open data) |

Design caveats:
- D3 single-signal (unpaid-care s3.1 + migration-channel s3.3 unsourced). GII license-verify. mobility-constraint c_mob non-monotonic gate applied downstream, not here.

### Age/Childhood Structuring - _child-labour-as-pickability_
Path: `recruitment/age-childhood-structuring`  
Confidence: **low_confidence** | Scope: Product-1

Signals:

| Signal (table / column) | Role | Source | Series / column | Direction | Anchor | Coverage | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `age_childhood/age_child_labor_prevalence` | generating | World Bank WDI (mirrors ILO/UNICEF SDG series) | `SL.TLF.0714.ZS` | high_risk | [0.0, 40.0] % of children ages 7-14 in employment | 47.2% | CC BY 4.0 |
| `age_childhood/age_out_of_school_rate` | generating | World Bank WDI (mirrors ILO/UNICEF SDG series) | `SE.SEC.UNER.LO.ZS` | high_risk | [0.0, 60.0] % of lower-secondary-age adolescents out of school | 91.8% | CC BY 4.0 |
| `age_childhood/age_child_cohort_share` | generating | World Bank WDI (mirrors ILO/UNICEF SDG series) | `SP.POP.0014.TO.ZS` | high_risk | [10.0, 50.0] % of total population ages 0-14 | 99.5% | CC BY 4.0 |
| `age_childhood/age_child_marriage_rate` | generating | World Bank WDI (mirrors ILO/UNICEF SDG series) | `SP.M18.2024.FE.ZS` | high_risk | [0.0, 60.0] % of women ages 20-24 first married by age 18 | 71.3% | CC BY 4.0 |

Design caveats:
- D1 child-labour prevalence 47% coverage (below floor). D3 single-signal (orphanhood S3.1 unsourced -> child-marriage alone). child-marriage->FL mechanism review OPEN.

### Structural Disruption - _gated, not additive; connecting-flagged_
Path: `recruitment/structural-disruption`  
Confidence: **ok** | Scope: Product-1

Signals:

| Signal (table / column) | Role | Source | Series / column | Direction | Anchor | Coverage | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `aux_emdat/aux_emdat_disaster_shock` | generating | EM-DAT (CRED/UCLouvain), public custom request 2026-05-28 | `EM-DAT Total Deaths / Total Affected (Natural)` | high_risk | [0.0, 1.0] persons affected as share of population (5-yr window 2020-2024) | 99.5% | Non-commercial / academic; registration required (CRED/UCLouvain) -- PUBLICATION-ELIGIBILITY PENDING: confirm publication eligibility before public release |
| `ndgain/ndgain_climate_vulnerability` | generating | ND-GAIN Country Index 2026 (vulnerability score) -- Notre Dame Global Adaptation Initiative | `ND-GAIN vulnerability, snapshot 2023` | high_risk | [0.0, 1.0] ND-GAIN vulnerability score (0-1 composite, native bounds) | 95.9% | Creative Commons (free/open, publish-safe with attribution) -- gain.nd.edu |
| `aux_ucdp/ucdp_conflict_intensity` | generating | UCDP Georeferenced Event Dataset (GED) v24.1 -- Uppsala Conflict Data Program | `UCDP GED v24.1 best-deaths, types [1, 2, 3], window 2019-2023` | high_risk | [0.0, 100.0] conflict (best-estimate) deaths per 100k (5-yr window 2019-2023) | 99.5% | CC BY 4.0 (fully open, publish-safe with attribution) -- ucdp.uu.se |
| `unhcr/unhcr_idps_by_country` | generating | UNHCR Population Statistics API | `unhcr_idps_by_country` | high_risk | [0.0, 5000.0] persons per 100k population | 93.3% | Open / CC BY (attribution required) |
| `unhcr/unhcr_refugees_by_coo` | generating | UNHCR Population Statistics API | `unhcr_refugees_by_coo` | high_risk | [0.0, 5000.0] persons per 100k population | 99% | Open / CC BY (attribution required) |

Design caveats:
- EM-DAT PRE-PUBLICATION-REQUIREMENT (non-commercial/academic) -- confirm eligibility or swap to DesInventar before public release. D2 single-signal (ND-GAIN). UNHCR displacement double-count guard vs Constrained Mobility (pending data-correlation check).

## Exploitation (E)

_Product-1 composite._

### Foreclosed Exit (Structural) - _standing exit-cost / monopsony capacity_
Path: `exploitation/foreclosed-exit-structural`  
Confidence: **insufficient_data** | Scope: Product-1

Signals:

| Signal (table / column) | Role | Source | Series / column | Direction | Anchor | Coverage | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ilostat/LAI_INDE_NOC_RT_A` | defeater | ILOSTAT (ILO, rplumber API) | `LAI_INDE_NOC_RT_A` | low_risk | [0.0, 3.0] labour inspectors per 10,000 employed persons | 42.6% | CC BY 4.0 (ILO open data) |
| `ilostat/ILR_CBCT_NOC_RT_A` | defeater | ILOSTAT (ILO, rplumber API) | `ILR_CBCT_NOC_RT_A` | low_risk | [0.0, 100.0] % of employees covered by collective agreements | 50.8% | CC BY 4.0 (ILO open data) |
| `ilostat/ILR_TUMT_NOC_RT_A` | defeater | ILOSTAT (ILO, rplumber API) | `ILR_TUMT_NOC_RT_A` | low_risk | [0.0, 100.0] % of employees who are trade union members | 69.2% | CC BY 4.0 (ILO open data) |

Design caveats:
- GENERATING SPINE UNSOURCED: D1 monopsony/exit-cost (the named generating driver, scored only here) is UNSOURCEABLE at 195 scale. The only sourced signals are Z-DEFEATERS (ILOSTAT inspection/collective-voice, 42-69% coverage) -- protective, not generating. Domain score here is a low-confidence STAND-IN; standalone-low-confidence vs fold-in is a pending decision. Flagged for review.

### Economic Structure & Demand
Path: `exploitation/economic-structure-demand`  
Confidence: **low_confidence** | Scope: Product-1

Signals:

| Signal (table / column) | Role | Source | Series / column | Direction | Anchor | Coverage | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `econ_structure_demand/esd_d1_hazardous_sector_share` | generating | World Bank WDI (employment by sector, modelled ILO estimate) | `SL.AGR.EMPL.ZS` | high_risk | [0.0, 60.0] employment in agriculture, % of total employment | 91.8% | CC BY 4.0 |
| `econ_structure_demand/esd_d2_informal_employment_share` | generating | ILOSTAT (ILO, rplumber API) | `SDG_0831_SEX_ECO_RT_A` | high_risk | [0.0, 100.0] informal employment, % of total employment (SDG 8.3.1) | 73.3% | CC BY 4.0 (ILO open data) |
| `aux_unctad/aux_unctad_export_concentration` | generating | UNCTADSTAT (authenticated OData API) | `unctad_concent_div_exports` | high_risk | [0.0, 1.0] UNCTAD merchandise export concentration index (HHI-style, 0-1) | 97.9% | UNCTAD data publicly available; cite UNCTADSTAT |

Design caveats:
- Business-cluster only (D1-D3). D4 criminal-market embedding = 0 drivers sourced (GI-TOC excluded as outcome-circular). Option-B 50/50 business/crime combine cannot run until D4 lands. esd_d3 buyer-concentration proxied LOW-CONFIDENCE by UNCTAD export concentration.

### State Production of Unfreedom
Path: `exploitation/state-production-of-unfreedom`  
Confidence: **insufficient_data** | Scope: Product-1

Signals:

| Signal (table / column) | Role | Source | Series / column | Direction | Anchor | Coverage | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `vdem/v2xcl_slave` | circular-flagged | V-Dem Country-Year Core v16 | `v2xcl_slave` | low_risk | [0.0, 1.0] interval 0-1 (higher = freer) | 88.7% | CC BY 4.0 |
| `vdem/v2xnp_client` | generating | V-Dem Country-Year Core v16 | `v2xnp_client` | high_risk | [0.0, 1.0] interval 0-1 (REVERSED: higher = MORE clientelism = worse) | 88.7% | CC BY 4.0 |
| `static_indices/trace_bribery_total` | generating | TRACE International Bribery Risk Matrix 2024 | `trace_bribery_risk_matrix_2024.csv:Total Score` | high_risk | [0.0, 100.0] TRACE bribery risk score (0-100) | 97.4% | UNCONFIRMED for public release -- RE-PUBLICATION-UNCONFIRMED (TRACE published publicly, no explicit open license; confirm re-publication rights before release) |
| `worldbank/wb_bribery_incidence` | generating | World Bank WDI + WGI | `IC.FRM.BRIB.ZS` | high_risk | [0.0, 60.0] % of firms experiencing at least one bribe payment request | 89.7% | CC BY 4.0 |

Design caveats:
- Only D4+D5 of 5 designed drivers (40% < driver floor) -> INSUFFICIENT-DATA. D4 input v2xcl_slave is CIRCULAR (V-Dem freedom-from-forced-labour = outcome/de-facto proxy, not de jure) -- CIRCULARITY_FLAG. D5 separability vs governance backbone UNCONFIRMED (pending data-correlation check). TRACE RE-PUBLICATION-UNCONFIRMED. D1/D2/D3 GAP.

## Monetization (M)

_Product-2 lens only (excluded from Product-1 composite)._

### Domain A - Transnational concealment & laundering infrastructure - _Product-2 lens; does NOT score into the Product-1 composite_
Path: `monetization/domain-a-transnational-concealment`  
Confidence: **low_confidence** | Scope: Product-2 only

Signals:

| Signal (table / column) | Role | Source | Series / column | Direction | Anchor | Coverage | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `basel_fatf/basel_fatf_me_effectiveness` | generating | Basel AML Index Expert Edition 2026 (Basel Institute on Governance), FATF Mutual Evaluation sub-component | `basel-aml-index-expertedition_2026-03-31.xlsx:FATF Mutual Evaluation Reports (col 9)` | high_risk | [0.0, 10.0] Basel risk scale 0-10 (FATF Mutual Evaluation effectiveness; higher = weaker AML effectiveness = more risk) | 94.4% | Open with citation (Basel Institute on Governance) |
| `basel_fatf/basel_tjn_fsi` | generating | Tax Justice Network Financial Secrecy Index 2025 (via Basel AML Index Expert Edition 2026) | `basel-aml-index-expertedition_2026-03-31.xlsx:Tax Justice Network Financial Secrecy Index (col 20)` | high_risk | [0.0, 10.0] Basel risk scale 0-10 (TJN Financial Secrecy Index; higher = more secrecy-jurisdiction exposure = more risk) | 61.5% | Basel workbook open with citation; standalone TJN FSI re-publication license UNCONFIRMED -- RE-PUBLICATION-UNCONFIRMED |
| `basel_fatf/basel_fatf_listing_flag` | generating | Basel AML Index Expert Edition 2026 (Basel Institute on Governance), FATF Mutual Evaluation sub-component | `basel-aml-index-expertedition_2026-03-31.xlsx:FATF grey list (col 38) | FATF black list (col 39)` | high_risk | [0.0, 1.0] binary 0/1 (FATF grey- or black-list standing) | 97.9% | Open with citation (Basel Institute on Governance) |

Design caveats:
- PRODUCT-2 LENS ONLY -- does NOT enter Product-1 composite. FATF-ME = distinct second FI component (Monetization only). FSI = Product-2 lens only. Both license RE-PUBLICATION-UNCONFIRMED (FSI).

### Domain B - Cash & informal-economy retention - _Product-2 lens; carries the only narrow Product-1 candidate slice (conditional, Phase 2)_
Path: `monetization/domain-b-cash-informal-retention`  
Confidence: **low_confidence** | Scope: Product-2 only

Signals:

| Signal (table / column) | Role | Source | Series / column | Direction | Anchor | Coverage | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `monetization_b/monet_b_shadow_economy` | generating | World Bank Informal Economy Database (Elgin, Kose, Ohnsorge & Yu) [WB_INFECDB_DGE_P] | `WB_INFECDB_DGE_P` | high_risk | [8.0, 50.0] shadow economy, % of official GDP (DGE estimate) | 80% | World Bank open data (CC BY 4.0); cite Elgin et al. 2021 "Understanding Informality" |
| `monetization_b/monet_b_financial_exclusion` | generating | World Bank Global Findex Database 2025 | `account_t_d` | high_risk | [0.0, 1.0] share of adults with no financial account (0-1) | 81% | CC BY 3.0 IGO (World Bank open data; attribution required) |

Design caveats:
- PRODUCT-2 by default; the narrow Product-1 retention slice is a conditional Phase-2 entrant (three-condition test) NOT resolved here -- so EXCLUDED from Product-1 composite at build. financial_exclusion duplicates Recruitment-Debt Findex (pending data-correlation de-dup).

## Shared gate (all phases)

### Corruption / capture & bought impunity
Path: `corruption-capture-gate`

Shared modifier/defeater-flagged gate folded into the domains it protects across all three phases; not a standalone domain. The general-governance backbone signal (worldbank/wb_wgi_rule_of_law) is scored once as the domain-level attenuate-only modulator; the financial-integrity / AML signal gets a distinct second entry in Monetization. See docs/scoring-rules.md.

Governance backbone signal (scored once): `worldbank/wb_wgi_rule_of_law`.
