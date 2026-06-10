# Data provenance

Per-source, per-indicator provenance for every signal in the FLSRI data layer.
This file is the human-readable companion to the machine-readable register at
`config/data_register.csv` (and the per-source fragments in
`config/data_register.d/`). It is the attribution record that makes the data use
defensible: for each indicator it gives the source, dataset/series id, vintage
(`year_min`–`year_max`), country coverage, license, scored direction, and the
citation the provider requires.

Scope and reading notes:

- Entries are grouped by source. The **id** column is the register's `series_id`
  (the provider's variable/series code or workbook column).
- **Coverage** is `coverage_pct` from the register: the share of the
  195-country sample with a non-missing value. Values below the 50% rule-9
  coverage floor are flagged low-confidence in the register and are noted here.
- **Dir.** is the scored direction: `high` = higher raw value means more risk;
  `low` = higher raw value means less risk (the connector inverts it). `n/a` =
  denominator or note row, not scored.
- Vintage is the data vintage carried in the standardized table (most-recent
  non-missing country-year, or the snapshot year), not the run date.
- Citations: where a standard academic citation exists it is given; otherwise the
  provider is named. Citations are not fabricated — confirm exact editions
  against the provider before publication.
- License and redistribution caveats are reproduced from the register and
  carried per-source below; several sources carry publish-time license flags,
  and the two redistribution-restricted sources (EM-DAT, IPUMS-International)
  are not bundled — their terms are recorded in their sections below.

---

## V-Dem — Varieties of Democracy

- **Source:** V-Dem Country-Year Core, v16
- **License:** CC BY 4.0
- **Vintage:** 2015–2025 (most-recent non-null country-year per variable)
- **Citation:** Coppedge, Michael, et al. *V-Dem Country-Year Dataset v16.*
  Varieties of Democracy (V-Dem) Project. (Cite the v16 dataset and methodology
  papers per the V-Dem citation guidance.)
- **Coverage note:** 173 countries; codes unmatched to ISO3 are dropped
  (PSG Palestine/Gaza, SML Somaliland).

| Indicator | id | Vintage | Cov. % | Dir. | Anchor |
|---|---|---|---|---|---|
| Rule of law | `v2x_rule` | 2015–2025 | 88.7 | low | [0,1] interval (higher = better) |
| Freedom of movement | `v2xcl_dmove` | 2015–2025 | 88.7 | low | [0,1] interval (higher = freer) |
| Freedom from forced labor | `v2xcl_slave` | 2015–2025 | 88.7 | low | [0,1] interval (higher = freer) |
| Clientelism | `v2xnp_client` | 2015–2025 | 88.7 | high | [0,1] interval (V-Dem-reversed: higher = more clientelism = worse) |

---

## World Bank — WDI / WGI

- **Source:** World Bank World Development Indicators and Worldwide Governance
  Indicators (several series are World Bank mirrors of ILO-modelled or
  UNICEF/UNSD SDG estimates, noted per row).
- **License:** CC BY 4.0
- **Citation:** World Bank, *World Development Indicators* / *Worldwide
  Governance Indicators*. Washington, DC: World Bank. (Series-level provenance —
  e.g. ILO modelled estimates, UNICEF/UNSD CRVS — as noted below.)

| Indicator | id | Vintage | Cov. % | Dir. | Anchor / note |
|---|---|---|---|---|---|
| Firm bribery incidence | `IC.FRM.BRIB.ZS` | 2007–2025 | 89.7 | high | [0,60] % of firms asked for a bribe |
| Gini index | `SI.POV.GINI` | 1992–2025 | 87.2 | high | [20,65] Gini (0–100) |
| Labor productivity | `SL.GDP.PCAP.EM.KD` | 2021–2024 | 88.7 | low | [785,119601] GDP per person employed (2017 PPP $) |
| Lower-secondary completion | `SE.SEC.CMPT.LO.ZS` | 1986–2025 | 94.4 | low | [0,100] % of age group |
| Population (denominator) | `SP.POP.TOTL` | 2024 | 99.5 | n/a | per-capita denominator; not a risk indicator, not scaled |
| Remittances, % GDP | `BX.TRF.PWKR.DT.GD.ZS` | 1983–2024 | 95.9 | high | [0,35] personal remittances received, % GDP |
| WGI Rule of Law | `GOV_WGI_RL.EST` | 2024 | 99.5 | low | [-2.5,2.5] WGI rule-of-law estimate |
| Poverty headcount ($6.85/day) | `SI.POV.UMIC` | 1992–2025 | 87.2 | high | [0,100] % below $6.85/day (2017 PPP); chosen UMIC line |
| Income volatility | `NY.GDP.MKTP.KD.ZG` | 1993–2024 | 99.0 | high | [0,8] std-dev of annual real GDP growth, ~15-yr window |
| Agrarian employment share | `SL.AGR.EMPL.ZS` | 2021–2025 | 91.8 | high | [0,80] % employment in agriculture (ILO-modelled) |
| Informal employment share | `SDG_0831_SEX_ECO_RT_A`* | 2009–2025 | 73.3 | high | [0,100] informal employment, % of total (SDG 8.3.1) |
| Hazardous-sector share | `SL.AGR.EMPL.ZS` | 2021–2025 | 91.8 | high | [0,60] % employment in agriculture (ESD D1 reading) |
| LFP gender gap | `SL.TLF.CACT.MA.ZS − SL.TLF.CACT.FE.ZS` | 2021–2025 | 91.8 | high | [0,50] male-minus-female LFP gap (pp, 15+) |
| Mobility legal constraint | `GD_WBL_MOB_LAW_T` | 2025 | 95.4 | low | [0,100] Women, Business and the Law mobility score |
| Birth-registration completeness | `SP.REG.BRTH.ZS` | 2006–2022 | 91.3 | low | [0,100] % of births registered (UNICEF/UNSD CRVS, SDG 16.9.1) |
| Child cohort share | `SP.POP.0014.TO.ZS` | 2024 | 99.5 | high | [10,50] % of population ages 0–14 (proxy) |
| Child-labor prevalence | `SL.TLF.0714.ZS` | 2005–2016 | 47.2 | high | [0,40] % of children 7–14 in employment — **below 50% floor, low-confidence** |
| Child-marriage rate | `SP.M18.2024.FE.ZS` | 2006–2023 | 71.3 | high | [0,60] % of women 20–24 married by 18 |
| Out-of-school rate | `SE.SEC.UNER.LO.ZS` | 2005–2024 | 91.8 | high | [0,60] % of lower-secondary-age out of school |

*The informal-employment series is the ILOSTAT SDG 8.3.1 indicator
(`SDG_0831_SEX_ECO_RT_A`), entered through both the precarity and economic-
structure connectors; the register also lists it under ILOSTAT below.

Two alternate poverty lines — `SI.POV.DDAY` ($2.15/day) and `SI.POV.LMIC`
($3.65/day) — are carried in the register as note rows so the chosen $6.85 line
can be swapped, but are **not** scored into the risk table.

---

## ILOSTAT — International Labour Organization

- **Source:** ILOSTAT (ILO), pulled from the rplumber API
- **License:** CC BY 4.0 (ILO open data)
- **Citation:** International Labour Organization, *ILOSTAT database.* Geneva: ILO.

| Indicator | id | Vintage | Cov. % | Dir. | Anchor / note |
|---|---|---|---|---|---|
| Collective-bargaining coverage | `ILR_CBCT_NOC_RT_A` | 2008–2020 | 50.8 | low | [0,100] % of employees covered by collective agreements (just above floor) |
| Trade-union density | `ILR_TUMT_NOC_RT_A` | 2007–2020 | 69.2 | low | [0,100] % of employees who are union members |
| Labor-inspector density | `LAI_INDE_NOC_RT_A` | 2010–2024 | 42.6 | low | [0,3] inspectors per 10,000 employed — **below 50% floor, low-confidence** |
| Fatal occupational injuries | `SDG_F881_SEX_MIG_RT_A` | 2000–2024 | 47.2 | high | [0,20] fatal occupational injuries per 100,000 — **below 50% floor, low-confidence** |
| Informal employment (SDG 8.3.1) | `SDG_0831_SEX_ECO_RT_A` | 2009–2025 | 73.3 | high | [0,100] informal employment, % of total |
| Sex × sector channel share | `EMP_TEMP_SEX_ECO_NB_A` | 1983–2025 | 96.4 | high | [0,0.7] max sex-specific exploitation-exposed-sector employment share. Mechanism grounding: GEMS 2021 (ILO/Walk Free/IOM 2022) grounds the mechanism only and is **not** scored — the scored value is the structural employment share, never a prevalence/victim count |

---

## UNHCR — Population Statistics

- **Source:** UNHCR Population Statistics API
- **License:** Open / CC BY (attribution required)
- **Vintage:** 2010–2025
- **Citation:** UNHCR, *Refugee Data Finder / Population Statistics.* United
  Nations High Commissioner for Refugees.
- **Anchor (all rows):** [0,5000] persons per 100k population

| Indicator | id | Cov. % | Dir. |
|---|---|---|---|
| Asylum seekers by country of asylum | `unhcr_asylum_seekers_by_coa` | 93.3 | high |
| IDPs by country | `unhcr_idps_by_country` | 93.3 | high |
| Refugees by country of asylum | `unhcr_refugees_by_coa` | 93.3 | high |
| Refugees by country of origin | `unhcr_refugees_by_coo` | 99.0 | high |
| Stateless persons by country | `unhcr_stateless_by_country` | 93.3 | high |

The statelessness column is re-used (not re-pulled) by the Legal Non-recognition
domain (`lnr_statelessness_prevalence`) reading the same standardized column.
Suppressed/absent statelessness figures are dropped, never set to 0.

---

## EM-DAT — disaster shocks (gated restricted source)

- **Source:** EM-DAT, CRED / UCLouvain (public custom request, 2026-05-28)
- **License:** Non-commercial / academic; registration required. Raw file is not committed.
- **Vintage:** 2020–2024 (five complete calendar years; 2025 partial excluded)
- **Exclusion:** heat-wave entries (Disaster Subtype "Heat wave") are excluded
  at the subtype level, all years (documented design decision): their
  excess-mortality accounting exists only where statistical systems run
  attribution studies, so it tracks measurement capacity rather than
  displacement-producing shock. Cold-wave entries stay (flagged for watch).
- **Citation:** EM-DAT, CRED / UCLouvain, Brussels, Belgium —
  *The International Disaster Database* (www.emdat.be).
- **Coverage:** 194 countries (99.5%). Missing-data rule: in-sample countries
  absent from EM-DAT treated as 0 events (event-stock assumption); countries
  with no population denominator stay missing.

| Indicator | id | Vintage | Cov. % | Dir. | Anchor |
|---|---|---|---|---|---|
| Disaster-affected intensity | EM-DAT Total Affected (Natural) | 2020–2024 | 99.5 | high | [0,1.0] persons affected as share of population |
| Disaster mortality intensity | EM-DAT Total Deaths (Natural) | 2020–2024 | 99.5 | high | [0,100] disaster deaths per 100k |

---

## UCDP — conflict intensity

- **Source:** UCDP Georeferenced Event Dataset (GED), v24.1, Uppsala Conflict
  Data Program
- **License:** CC BY 4.0 (open, publish-safe with attribution)
- **Vintage:** 2019–2023 (five complete years; GED v24.1 ends 2023)
- **Citation:** Sundberg, Ralph, and Erik Melander. 2013. "Introducing the UCDP
  Georeferenced Event Dataset." *Journal of Peace Research* 50(4); Davies,
  Pettersson & Öberg, UCDP GED codebook v24.1.

| Indicator | id | Vintage | Cov. % | Dir. | Anchor / note |
|---|---|---|---|---|---|
| Conflict intensity | UCDP GED best-estimate deaths, types [1,2,3] | 2019–2023 | 99.5 | high | [0,100] conflict deaths per 100k. Direct intensity signal only; displacement (IDPs/originating refugees) is the separate UNHCR series, not re-counted |

---

## ETH Zurich — Ethnic Power Relations (EPR)

- **Source:** ETH Zurich Ethnic Power Relations (EPR) Core 2023
- **License:** Academic / open; downloadable at icr.ethz.ch.
- **Vintage:** 2023 snapshot
- **Citation:** Vogt, Manuel, et al. 2015. "Integrating Data on Ethnicity,
  Geography, and Conflict: The Ethnic Power Relations Data Set Family."
  *Journal of Conflict Resolution* 59(7): 1327–1342.

| Indicator | id | Vintage | Cov. % | Dir. | Anchor / note |
|---|---|---|---|---|---|
| Excluded-population share | EPR-2023 Core: status × size | 2023 | 88.2 | high | [0,0.5] depth-weighted share of population in politically-excluded ethnic groups. Codes political exclusion (executive-power access), not socioeconomic channeling; ~41 sample countries outside EPR's universe stay missing (not 0) |

---

## ND-GAIN — climate vulnerability

- **Source:** Notre Dame Global Adaptation Initiative (ND-GAIN) Country Index 2026
- **License:** Creative Commons (free/open, publish-safe with attribution);
  gain.nd.edu
- **Vintage:** 2023 snapshot (most-recent fully-populated column in the 2026 edition)
- **Citation:** Chen, Chen, et al. *University of Notre Dame Global Adaptation
  Initiative (ND-GAIN) Country Index.* Notre Dame, IN.

| Indicator | id | Vintage | Cov. % | Dir. | Anchor / note |
|---|---|---|---|---|---|
| Climate vulnerability | ND-GAIN vulnerability score | 2023 | 95.9 | high | [0,1] vulnerability composite (native bounds). Vulnerability axis only — governance is held in ND-GAIN's separate readiness axis and not double-counted |

---

## UNCTADSTAT — export concentration

- **Source:** UNCTADSTAT (authenticated OData API)
- **License:** UNCTAD data publicly available; cite UNCTADSTAT
- **Vintage:** 2024
- **Citation:** United Nations Conference on Trade and Development, *UNCTADSTAT.*

| Indicator | id | Vintage | Cov. % | Dir. | Anchor / note |
|---|---|---|---|---|---|
| Export concentration | `unctad_concent_div_exports` | 2024 | 97.9 | high | [0,1] merchandise export concentration index (HHI-style). Export flow (Flow/Code '01') |

---

## World Bank — Global Findex

- **Source:** World Bank Global Findex Database 2025
- **License:** CC BY 3.0 IGO (World Bank open data; attribution required)
- **Vintage:** 2011–2024 (latest available wave per country; vintage-mix flagged)
- **Citation:** Demirgüç-Kunt, Asli, et al. *The Global Findex Database 2025.*
  Washington, DC: World Bank.

| Indicator | id | Vintage | Cov. % | Dir. | Anchor / note |
|---|---|---|---|---|---|
| Account exclusion | `account_t_d` (1−x) | 2011–2024 | 81.0 | high | [0,1] share of adults with no financial account |
| Borrowing prevalence | `borrow_any_t_d` | 2011–2024 | 59.0 | high | [0,1] share who borrowed in past year. Proxy, not a debt-distress measure |
| Informal borrowing | `fin22b+fin22c` | 2011–2024 | 59.0 | high | [0,1] share borrowing from informal sources (summed, clamped) |
| Financial exclusion (Monetization B) | `account_t_d` | 2011–2024 | 81.0 | high | [0,1] share with no financial account. Same variable as account exclusion — de-duplicated across domains |

---

## World Bank — Informal Economy Database

- **Source:** World Bank Informal Economy Database (`WB_INFECDB_DGE_P`)
- **License:** World Bank open data (CC BY 4.0)
- **Vintage:** 2020
- **Citation:** Elgin, Ceyhun, M. Ayhan Kose, Franziska Ohnsorge, and Shu Yu.
  2021. "Understanding Informality." World Bank.

| Indicator | id | Vintage | Cov. % | Dir. | Anchor |
|---|---|---|---|---|---|
| Shadow economy | `WB_INFECDB_DGE_P` | 2020 | 80.0 | high | [8,50] shadow economy, % of official GDP (DGE estimate) |

---

## UNDP — Gender Inequality Index

- **Source:** UNDP Human Development Report — Gender Inequality Index, via Our
  World in Data republication
- **License:** UNDP HDR (publish with attribution); OWID layer CC BY 4.0.
  **License-verify:** confirm UNDP HDR re-publication terms before public release.
- **Vintage:** 2023
- **Citation:** United Nations Development Programme, *Human Development Report —
  Gender Inequality Index.* New York: UNDP (OWID republication).

| Indicator | id | Vintage | Cov. % | Dir. | Anchor |
|---|---|---|---|---|---|
| Gender Inequality Index | UNDP HDR GII (OWID full CSV) | 2023 | 88.2 | high | [0,1] GII (0 = equal, 1 = maximally unequal) |

---

## Basel Institute on Governance — AML Index (Expert Edition)

- **Source:** Basel AML Index Expert Edition 2026, Basel Institute on Governance
  (the Expert Edition workbook also carries a Tax Justice Network FSI column)
- **License:** Open with citation (Basel Institute on Governance). 
- **Vintage:** 2025–2026 (per sub-component)
- **Citation:** Basel Institute on Governance, *Basel AML Index 2026 Expert
  Edition.* For the FSI column: Tax Justice Network, *Financial Secrecy Index 2025.*
- **Scope note:** these signals feed the Monetization intervention lens
  (Product-2), not the main risk composite. The FATF-ME and FSI slices are
  disaggregated sub-components, deliberately **not** the Basel composite (which
  embeds the TIP Report and would be outcome-circular). The Basel overall and
  AML/CFT-framework composites are dropped as circular (decision 1.11).

| Indicator | id | Vintage | Cov. % | Dir. | Anchor / note |
|---|---|---|---|---|---|
| FATF listing flag | FATF grey list (col 38) / black list (col 39) | 2026 | 97.9 | high | [0,1] binary FATF grey/black-list standing |
| FATF Mutual-Evaluation effectiveness | FATF Mutual Evaluation Reports (col 9) | 2026 | 94.4 | high | [0,10] Basel scale (higher = weaker AML effectiveness) |
| TJN Financial Secrecy Index | TJN FSI (col 20) | 2025 | 61.5 | high | [0,10] Basel scale (higher = more secrecy-jurisdiction exposure) |

---

## Static indices

### Henley Passport Index

- **Source:** Henley Passport Index, May 2026 vintage
- **Citation:** Henley & Partners, *Henley Passport Index.*

| Indicator | id | Vintage | Cov. % | Dir. | Anchor |
|---|---|---|---|---|---|
| Passport access | `Visa Free` count | 2026 | 100.0 | low | [0,227] visa-free destination count |

### TRACE Bribery Risk Matrix

- **Source:** TRACE International Bribery Risk Matrix 2024
- **Citation:** TRACE International, *Bribery Risk Matrix.*

| Indicator | id | Vintage | Cov. % | Dir. | Anchor |
|---|---|---|---|---|---|
| Bribery risk | `Total Score` | 2024 | 97.4 | high | [0,100] TRACE bribery risk score |

---

## Subnational risk surface — GDIS + IPUMS-International

These two sources feed only the **admin-1 subnational risk surface** (the map's
sub-national layer), not the national composite. Recorded here so their
attribution and redistribution terms are carried with the rest of the provenance.

- **Geocoded Disasters (GDIS):** geocoded disaster locations, 1960–2018 (39,953
  points; 28,002 placed into GEOLEV1 polygons for the shock axis). **License:**
  CC BY 4.0. **Citation:** Rosvold, E. L., and H. Buhaug. 2021. "GDIS, a global
  dataset of geocoded disaster locations." *Scientific Data* 8: 61. Distributed
  via NASA SEDAC (Palisades, NY).
- **IPUMS-International:** census microdata (the precarity axis) and the GeoLev1
  first-level administrative boundaries (the surface geometry). **License:**
  restricted — IPUMS conditions of use; **redistribution-gated and not bundled**
  (obtained under IPUMS terms; set `FLSRI_EXTERNAL_DATA` to a local copy to
  rebuild). **Citation:** Minnesota Population Center, *Integrated Public Use
  Microdata Series, International.* Minneapolis, MN: IPUMS

---

## Derived domain aggregate

| Indicator | id / definition | Vintage | Cov. % | Dir. | Note |
|---|---|---|---|---|---|
| Legal non-recognition domain (raw) | `lnr_domain_raw` = equal-weight mean of D1 (birth-registration incompleteness) + D2 (statelessness) | 2006–2025 | 99.0 | high | Derived; pre-governance, rule-9 drop-and-re-average. 26 single-driver rows are low-confidence |

---

## Documented gaps and shared-signal note rows

The register also carries **note rows** that are not scored indicators but are
part of the provenance scholarship: hard gaps where no defensible cross-national
source exists, and shared-signal placement notes. They are recorded so the
absence is explicit rather than silently imputed (scoring rule 9: missing is
never set to 0). These remain genuine methodological caveats and are kept in
full in `config/data_register.csv`. In summary:

- **Unsourced / hard-gap drivers (not scored, no proxy fabricated):** monopsony
  exit-cost spine (Foreclosed Exit D1); kafala/sponsorship and brokerage
  recruitment terms (Constrained Mobility D2/D3); tied-status, immigration-
  architecture and protective-floor legal-coding (State Production D1–D3);
  buyer concentration and criminal-market embedding (Economic Structure & Demand
  D3/D4); gendered unpaid-care time-use (S3.1); all-cause orphanhood prevalence
  (S3.1); foundational-ID coverage (Legal Non-recognition D1 second signal).
  Several are flagged for further source review; affected domains rest on fewer
  drivers and are marked low-confidence.
- **Shared governance backbone (scored once):** the general-governance dial
  (`wb_wgi_rule_of_law` / `v2x_rule`) is applied once at domain assembly and is
  **not** re-pulled by the domain connectors that reference it
  (Gender Structuring Z1, Legal Non-recognition M1, Foreclosed Exit Z4).
  Collinearity de-duplication is flagged for review.
- **Excluded as circular:** US TIP Report, GI-TOC org-crime outcome measures,
  the Basel AML composite, and other governance-backbone duplicates are excluded
  per decision 1.11 to avoid outcome-circularity.

See `docs/METHODS.md` for the standardization and aggregation rules and
`docs/scoring-rules.md` for the locked scoring rules referenced above.