"""Domain -> signal crosswalk for the BUILD stage.

Maps each of the 13 frozen-registry domains to the concrete
(processed_table, column) signal tuples that populate its signal layer.
Authority: the per-domain provenance notes (see docs/data-provenance.md) and the
`config/data_register.csv` `indicator` column. All referenced columns are
already standardized to the fixed 0-1 absolute-anchored RISK scale by the
upstream connectors (pipeline/standardize.py) -- this layer only *selects*
columns; it does not re-standardize.

Each domain entry carries:
  signals    : list of (table_slug, column) -- the generating/driver signals
               averaged equal-weight at the domain level (rules 2/5; see
               docs/scoring-rules.md).
  confidence : "ok" | "low_confidence" | "insufficient_data"
               carried straight from the data register flags
               (see docs/data-provenance.md); NOT recomputed here (the
               per-country coverage floor in aggregate.py applies on top of
               this domain-design flag).
  flags      : free-text design caveats flagged for review (circularity,
               unsourced spine, defeater-only sourcing, etc.).

Governance backbone (wb_wgi_rule_of_law) is NOT listed as a domain signal:
it enters ONCE as the domain-level attenuate-only modulator in aggregate.py
(rule 8; see docs/scoring-rules.md). Monetization domains are mapped for
completeness/Product-2 but are excluded from the Product-1 composite by
composite.py.

NOTE on directions: every column below is already RISK-aligned (higher = more
risk) by the connector that wrote it -- including columns whose *raw* source is
protective (e.g. ILOSTAT union/CB coverage, henley passport access): the
connector inverted them at standardize time. See the `direction` column of
config/data_register.csv. The one load-bearing exception is flagged inline
(foreclosed-exit), where the only sourced signals are DEFEATERS, not generating
signals -- flagged for review, not silently scored as generating risk.
"""

# Processed-table slugs -> filenames are resolved in aggregate.py via
# data/processed/<slug>.csv.

CROSSWALK = {
    # ---------------- RECRUITMENT (phase R) ----------------
    "economic-precarity": {
        "phase": "recruitment",
        "signals": [
            ("recruitment_econprecarity", "ep_poverty_headcount_685"),
            ("recruitment_econprecarity", "ep_informal_employment_share"),
            ("recruitment_econprecarity", "ep_agrarian_employment_share"),
            ("recruitment_econprecarity", "ep_income_volatility"),
            ("worldbank", "wb_gini"),
        ],
        "confidence": "ok",
        "flags": [
            "D3 agrarian rests on single agrarian-concentration signal (S3a "
            "landlessness unsourced); D5 volatility not yet residual-scoped.",
        ],
    },
    "debt-financialized-dependency": {
        "phase": "recruitment",
        "signals": [
            ("findex", "findex_account_exclusion"),
            ("findex", "findex_informal_borrowing"),
            ("findex", "findex_borrow_prevalence"),
        ],
        "confidence": "low_confidence",
        "flags": [
            "Lead driver R-D1 (recruitment-fee/migration debt) UNSOURCED; "
            "domain rests on R-D2 + R-D3 (Findex). R-D3 borrowing = penetration "
            "PROXY, not DSTI distress. 59% borrowing-module coverage.",
        ],
    },
    "constrained-mobility": {
        "phase": "recruitment",
        "signals": [
            ("vdem", "v2xcl_dmove"),
            ("static_indices", "henley_passport_access"),
            ("unhcr", "unhcr_refugees_by_coo"),
        ],
        "confidence": "low_confidence",
        "flags": [
            "D1 + D4 only (2 of 4 drivers); D2 kafala + D3 brokerage UNSOURCED. "
            "Henley RE-PUBLICATION-UNCONFIRMED (license unconfirmed).",
        ],
    },
    "ascriptive-exclusion": {
        "phase": "recruitment",
        "signals": [
            ("epr", "epr_excluded_pop_share"),
        ],
        "confidence": "low_confidence",
        "flags": [
            "Single EPR signal (political-exclusion only; under-captures "
            "labor-market caste/ethnic channeling). ~41 countries off EPR "
            "universe stay MISSING (not 0). EPR license-verify before publish.",
        ],
    },
    "legal-non-recognition": {
        "phase": "recruitment",
        "signals": [
            ("legal_non_recognition", "lnr_birth_registration_incompleteness"),
            ("legal_non_recognition", "lnr_statelessness_prevalence"),
        ],
        "confidence": "ok",
        "flags": [
            "26 single-driver rows are low-confidence (statelessness "
            "dropped/suppressed -> rests on birth-reg alone); never zeroed.",
        ],
    },
    "gender-structuring": {
        "phase": "recruitment",
        "signals": [
            ("gender_structuring", "gs_lfp_gender_gap"),
            ("gender_structuring", "gs_gender_inequality_index"),
            ("gender_structuring", "gs_mobility_constraint"),
            ("gender_structuring", "gs_sex_sector_channel_share"),
        ],
        "confidence": "ok",
        "flags": [
            "D3 single-signal (unpaid-care s3.1 + migration-channel s3.3 "
            "unsourced). GII license-verify. mobility-constraint c_mob "
            "non-monotonic gate applied downstream, not here.",
        ],
    },
    "age-childhood-structuring": {
        "phase": "recruitment",
        "signals": [
            ("age_childhood", "age_child_labor_prevalence"),
            ("age_childhood", "age_out_of_school_rate"),
            ("age_childhood", "age_child_cohort_share"),
            ("age_childhood", "age_child_marriage_rate"),
        ],
        "confidence": "low_confidence",
        "flags": [
            "D1 child-labour prevalence 47% coverage (below floor). D3 "
            "single-signal (orphanhood S3.1 unsourced -> child-marriage alone). "
            "child-marriage->FL mechanism review OPEN.",
        ],
    },
    "structural-disruption": {
        "phase": "recruitment",
        "signals": [
            ("aux_emdat", "aux_emdat_disaster_shock"),
            ("ndgain", "ndgain_climate_vulnerability"),
            ("aux_ucdp", "ucdp_conflict_intensity"),
            ("unhcr", "unhcr_idps_by_country"),
            ("unhcr", "unhcr_refugees_by_coo"),
        ],
        "confidence": "ok",
        "flags": [
            "EM-DAT PRE-PUBLICATION-REQUIREMENT (non-commercial/academic) -- confirm "
            "eligibility or swap to DesInventar before public release. D2 "
            "single-signal (ND-GAIN). UNHCR displacement double-count guard "
            "vs Constrained Mobility (pending data-correlation check).",
        ],
    },
    # ---------------- EXPLOITATION (phase E) ----------------
    "foreclosed-exit-structural": {
        "phase": "exploitation",
        # LOAD-BEARING FLAG: the ONLY sourced signals for this domain are the
        # ILOSTAT collective-voice / inspection series, which are DEFEATERS
        # (protective; risk-aligned by the connector as low->less attenuation).
        # The NAMED GENERATING SPINE (D1 monopsony / exit-cost) is UNSOURCEABLE
        # at 195 scale. Scoring on defeater-derived columns is a stand-in only.
        # Carried INSUFFICIENT-DATA and flagged for review; the
        # per-country floor will also flag most rows.
        "signals": [
            ("ilostat", "LAI_INDE_NOC_RT_A"),
            ("ilostat", "ILR_CBCT_NOC_RT_A"),
            ("ilostat", "ILR_TUMT_NOC_RT_A"),
        ],
        "confidence": "insufficient_data",
        "flags": [
            "GENERATING SPINE UNSOURCED: D1 monopsony/exit-cost (the named "
            "generating driver, scored only here) is UNSOURCEABLE at 195 scale. "
            "The only sourced signals are Z-DEFEATERS (ILOSTAT "
            "inspection/collective-voice, 42-69% coverage) -- protective, not "
            "generating. Domain score here is a low-confidence STAND-IN; "
            "standalone-low-confidence vs fold-in is a pending decision. "
            "Flagged for review.",
        ],
    },
    "economic-structure-demand": {
        "phase": "exploitation",
        "signals": [
            ("econ_structure_demand", "esd_d1_hazardous_sector_share"),
            ("econ_structure_demand", "esd_d2_informal_employment_share"),
            ("aux_unctad", "aux_unctad_export_concentration"),
        ],
        "confidence": "low_confidence",
        "flags": [
            "Business-cluster only (D1-D3). D4 criminal-market embedding = "
            "0 drivers sourced (GI-TOC excluded as outcome-circular). "
            "Option-B 50/50 business/crime combine cannot run until D4 lands. "
            "esd_d3 buyer-concentration proxied LOW-CONFIDENCE by UNCTAD "
            "export concentration.",
        ],
    },
    "state-production-of-unfreedom": {
        "phase": "exploitation",
        "signals": [
            # D4 SIFL -- v2xcl_slave, CIRCULARITY-FLAGGED (outcome/de-facto proxy)
            ("vdem", "v2xcl_slave"),
            # D5 active capture -- governance/corruption proxies
            ("vdem", "v2xnp_client"),
            ("static_indices", "trace_bribery_total"),
            ("worldbank", "wb_bribery_incidence"),
        ],
        "confidence": "insufficient_data",
        "flags": [
            "Only D4+D5 of 5 designed drivers (40% < driver floor) -> "
            "INSUFFICIENT-DATA. D4 input v2xcl_slave is CIRCULAR (V-Dem "
            "freedom-from-forced-labour = outcome/de-facto proxy, not de jure) "
            "-- CIRCULARITY_FLAG. D5 separability vs governance backbone "
            "UNCONFIRMED (pending data-correlation check). TRACE RE-PUBLICATION-UNCONFIRMED. "
            "D1/D2/D3 GAP.",
        ],
        "circularity_signals": [("vdem", "v2xcl_slave")],
    },
    # ---------------- MONETIZATION (phase M) -- PRODUCT-2 ONLY ----------------
    # Mapped for completeness + the Product-2 lens. EXCLUDED from the Product-1
    # composite by composite.py (rule 7). FATF-ME / FSI are the distinct
    # second FI component (Monetization only) and the Product-2 lens.
    "domain-a-transnational-concealment": {
        "phase": "monetization",
        "signals": [
            ("basel_fatf", "basel_fatf_me_effectiveness"),
            ("basel_fatf", "basel_tjn_fsi"),
            ("basel_fatf", "basel_fatf_listing_flag"),
        ],
        "confidence": "low_confidence",
        "flags": [
            "PRODUCT-2 LENS ONLY -- does NOT enter Product-1 composite. "
            "FATF-ME = distinct second FI component (Monetization only). "
            "FSI = Product-2 lens only. Both license RE-PUBLICATION-UNCONFIRMED (FSI).",
        ],
        "product2_only": True,
    },
    "domain-b-cash-informal-retention": {
        "phase": "monetization",
        "signals": [
            ("monetization_b", "monet_b_shadow_economy"),
            ("monetization_b", "monet_b_financial_exclusion"),
        ],
        "confidence": "low_confidence",
        "flags": [
            "PRODUCT-2 by default; the narrow Product-1 retention slice is a "
            "conditional Phase-2 entrant (three-condition test) NOT resolved "
            "here -- so EXCLUDED from Product-1 composite at build. "
            "financial_exclusion duplicates Recruitment-Debt Findex "
            "(pending data-correlation de-dup).",
        ],
        "product2_only": True,
    },
}

# Phase membership for the Product-1 composite. Monetization is intentionally
# absent -- it never enters Product-1 (rule 7; see docs/scoring-rules.md).
PRODUCT1_PHASES = {
    "recruitment": [
        "economic-precarity",
        "debt-financialized-dependency",
        "constrained-mobility",
        "ascriptive-exclusion",
        "legal-non-recognition",
        "gender-structuring",
        "age-childhood-structuring",
        "structural-disruption",
    ],
    "exploitation": [
        "foreclosed-exit-structural",
        "economic-structure-demand",
        "state-production-of-unfreedom",
    ],
}

MONETIZATION_DOMAINS = [
    "domain-a-transnational-concealment",
    "domain-b-cash-informal-retention",
]

# The single shared governance backbone (rule 8; see docs/scoring-rules.md) --
# scored ONCE, applied as the domain-level attenuate-only modulator.
GOVERNANCE_TABLE = "worldbank"
GOVERNANCE_COLUMN = "wb_wgi_rule_of_law"
