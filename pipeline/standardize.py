"""Standardize: put every indicator on a common 0-1 risk scale.

Implements the v1 scoring rules (see docs/scoring-rules.md):

  Rule 1 (signal scale): every signal maps to 0-1 (0 = lowest risk, 1 = highest).
    Where country size matters, put the raw unit on a PER-EXPOSURE basis first
    (per 100k, share of population, share of GDP), then map to 0-1 against
    FIXED, literature-/standards-anchored reference points, clamping to [0, 1].
    A robust RELATIVE fallback (winsorized min-max / percentile rank) is allowed
    ONLY where a signal has no defensible absolute anchor, and must be recorded.

  Rule 9 (missing data): drop-and-re-average with a coverage floor -- combine
    only if >= 50% of inputs are present AND never fewer than 2; below the floor,
    mark low-confidence / insufficient-data. NEVER missing -> 0.

  Direction: each indicator's direction (higher = more risk) is set explicitly
    via the `direction` argument / `invert` flag.

This module is the shared interface every connector module
(pipeline/sources/*.py) calls so all source tables come out on the same scale.

----------------------------------------------------------------------------
CONNECTOR INTERFACE (what the WB / UNHCR / ILOSTAT / V-Dem / STATIC / AUX
connectors call) -- two functions plus a result object:

    from pipeline.standardize import anchor_scale, AnchorSpec

    spec = AnchorSpec(
        indicator="1.1.1_disaster_mortality",
        floor=0.0, ceiling=50.0,        # absolute anchors in the per-exposure unit
        direction="high_risk",          # or "low_risk" (inverts: low raw = high risk)
        unit="deaths per 100k",
        anchor_source="lit/standards citation",
    )
    scored = anchor_scale(raw_by_iso3, spec)   # {iso3: 0-1 score}

`anchor_scale` returns a `ScaleResult` (dict-like) carrying the 0-1 values plus
the coverage metadata the data register needs (coverage_pct, n_present, flags).

For a signal with NO defensible absolute anchor, use:

    scored = relative_scale(raw_by_iso3, spec, method="winsor_minmax")

which records the relative-fallback flag automatically.
"""

import math


# --- legacy helpers (earlier scaffold; unused by the shipped build) --------

def minmax(values, invert=False):
    """Min-max scale a list of numbers to [0, 1]. `invert` flips direction.

    Legacy helper from an earlier scaffold. Real indicators should use
    anchor_scale (absolute) or relative_scale (justified fallback).
    """
    present = [v for v in values if v is not None]
    if not present:
        return [None for _ in values]
    lo, hi = min(present), max(present)
    span = (hi - lo) or 1.0
    out = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        s = (v - lo) / span
        out.append(1.0 - s if invert else s)
    return out


def standardize(country_indicators):
    """Ensure all indicator values sit in [0, 1] (pass-through for synthetic)."""
    for country, inds in country_indicators.items():
        for k, v in inds.items():
            if v is not None:
                inds[k] = max(0.0, min(1.0, v))
    return country_indicators


# --- direction handling ----------------------------------------------------

# "high_risk": higher raw value = more risk (no inversion).
# "low_risk":  higher raw value = LESS risk -> invert so that low raw = high score
#              (e.g. trade-union density, rule-of-law, passport visa-free count).
_DIRECTIONS = {"high_risk", "low_risk"}


def _resolve_direction(direction=None, invert=None):
    """Return True if the 0-1 score should be inverted (low raw -> high risk)."""
    if invert is not None:
        return bool(invert)
    if direction is None:
        return False
    if direction not in _DIRECTIONS:
        raise ValueError(
            f"direction must be one of {_DIRECTIONS} (or pass invert=bool); got {direction!r}"
        )
    return direction == "low_risk"


# --- the spec a connector hands in -----------------------------------------

class AnchorSpec:
    """Describes how one indicator's raw per-exposure values map to 0-1.

    Attributes (also what the data register row records):
      indicator      : indicator id / slug (str)
      floor          : raw value mapping to 0.0 (lowest risk)  [absolute anchor]
      ceiling        : raw value mapping to 1.0 (highest risk) [absolute anchor]
      direction      : "high_risk" (default) or "low_risk"
      invert         : optional explicit override of direction (bool)
      unit           : human-readable per-exposure unit (e.g. "deaths per 100k")
      anchor_source  : citation/justification for the floor & ceiling (str)
      coverage_floor : min share of sample required to clear the floor (0.5 default)
      min_present    : never fewer than this many present (2 default)
    """

    def __init__(self, indicator, floor=None, ceiling=None, direction="high_risk",
                 invert=None, unit="", anchor_source="", coverage_floor=0.5,
                 min_present=2):
        self.indicator = indicator
        self.floor = floor
        self.ceiling = ceiling
        self.direction = direction
        self.invert = invert
        self.unit = unit
        self.anchor_source = anchor_source
        self.coverage_floor = coverage_floor
        self.min_present = min_present

    def anchor_str(self):
        """Compact anchor description for the data register `anchor` column."""
        if self.floor is None or self.ceiling is None:
            return f"relative-fallback ({self.anchor_source})" if self.anchor_source \
                else "relative-fallback"
        return f"[{self.floor}, {self.ceiling}] {self.unit}".strip()


class ScaleResult(dict):
    """The 0-1 scores plus the coverage/provenance metadata.

    Behaves like {iso3: score_or_None} but carries `.meta`, a dict the data
    register consumes:
      indicator, direction, anchor, coverage_pct, n_present, n_total,
      below_floor (bool), flags (list[str]), method ("absolute"/"relative").
    """

    def __init__(self, scores, meta):
        super().__init__(scores)
        self.meta = meta

    def register_row(self, source, series_id="", license="", extra_flags=None):
        """Produce a config/data_register.csv row dict for this indicator.

        Caller supplies the source-level fields (source, series_id, license,
        countries/years they pulled). This fills indicator/direction/anchor/
        coverage_pct/flags from the scaling result.
        """
        flags = list(self.meta.get("flags", []))
        if extra_flags:
            flags.extend(extra_flags)
        return {
            "indicator": self.meta["indicator"],
            "source": source,
            "series_id": series_id,
            "countries": self.meta["n_present"],
            "year_min": "",
            "year_max": "",
            "license": license,
            "direction": self.meta["direction"],
            "anchor": self.meta["anchor"],
            "coverage_pct": round(self.meta["coverage_pct"], 1),
            "flags": "; ".join(flags),
        }


# --- the two scaling entry points ------------------------------------------

def _coverage(n_present, n_total, spec):
    cov_pct = (100.0 * n_present / n_total) if n_total else 0.0
    below = (n_total > 0) and (
        (n_present / n_total) < spec.coverage_floor or n_present < spec.min_present
    )
    return cov_pct, below


def anchor_scale(raw_by_iso3, spec, sample=None):
    """Map raw PER-EXPOSURE values to 0-1 against fixed absolute anchors.

    raw_by_iso3 : {iso3: raw_value_or_None}  (already on a per-exposure basis)
    spec        : AnchorSpec with floor & ceiling set (the absolute anchors)
    sample      : optional iterable of the full ISO3 universe for coverage_pct;
                  defaults to the keys of raw_by_iso3.

    Returns a ScaleResult. Values are clamped to [0, 1]; direction applied;
    missing inputs stay None (never -> 0). Coverage is computed against `sample`.
    """
    if spec.floor is None or spec.ceiling is None:
        raise ValueError(
            f"{spec.indicator}: anchor_scale needs floor and ceiling. "
            "Use relative_scale() for a justified no-anchor fallback."
        )
    if spec.ceiling == spec.floor:
        raise ValueError(f"{spec.indicator}: floor == ceiling, span is zero.")

    invert = _resolve_direction(spec.direction, spec.invert)
    span = spec.ceiling - spec.floor

    universe = list(sample) if sample is not None else list(raw_by_iso3.keys())
    scores = {}
    n_present = 0
    for iso3 in universe:
        v = raw_by_iso3.get(iso3)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            scores[iso3] = None
            continue
        s = (v - spec.floor) / span
        s = max(0.0, min(1.0, s))
        if invert:
            s = 1.0 - s
        scores[iso3] = s
        n_present += 1

    cov_pct, below = _coverage(n_present, len(universe), spec)
    flags = []
    if below:
        flags.append(
            f"BELOW-COVERAGE-FLOOR ({n_present}/{len(universe)} = {cov_pct:.0f}%; "
            f"floor {int(spec.coverage_floor*100)}%/min {spec.min_present}) -- low-confidence"
        )
    meta = {
        "indicator": spec.indicator,
        "direction": spec.direction if spec.invert is None else
                     ("low_risk" if spec.invert else "high_risk"),
        "anchor": spec.anchor_str(),
        "method": "absolute",
        "coverage_pct": cov_pct,
        "n_present": n_present,
        "n_total": len(universe),
        "below_floor": below,
        "flags": flags,
    }
    return ScaleResult(scores, meta)


def relative_scale(raw_by_iso3, spec, method="winsor_minmax", winsor=0.05,
                   sample=None):
    """Justified RELATIVE fallback for a signal with no defensible anchor.

    method:
      "winsor_minmax" : winsorize at the `winsor`/1-`winsor` quantiles, then min-max.
      "percentile"    : percentile rank across present values.

    Records a relative-fallback flag automatically (per Rule 1). Direction
    applied via spec. Coverage computed against `sample` (defaults to keys).
    """
    invert = _resolve_direction(spec.direction, spec.invert)
    universe = list(sample) if sample is not None else list(raw_by_iso3.keys())

    present_items = [
        (iso3, raw_by_iso3.get(iso3)) for iso3 in universe
        if raw_by_iso3.get(iso3) is not None
        and not (isinstance(raw_by_iso3.get(iso3), float) and math.isnan(raw_by_iso3.get(iso3)))
    ]
    present_vals = sorted(v for _, v in present_items)
    scores = {iso3: None for iso3 in universe}

    if present_vals:
        if method == "percentile":
            n = len(present_vals)
            rank = {}
            for i, v in enumerate(present_vals):
                rank.setdefault(v, i)
            for iso3, v in present_items:
                # fraction of values strictly below + ties handled by position
                below = sum(1 for x in present_vals if x < v)
                s = below / (n - 1) if n > 1 else 0.0
                scores[iso3] = 1.0 - s if invert else s
        else:  # winsor_minmax
            lo = _quantile(present_vals, winsor)
            hi = _quantile(present_vals, 1 - winsor)
            span = (hi - lo) or 1.0
            for iso3, v in present_items:
                s = max(0.0, min(1.0, (v - lo) / span))
                scores[iso3] = 1.0 - s if invert else s

    n_present = len(present_items)
    cov_pct, below = _coverage(n_present, len(universe), spec)
    flags = [f"RELATIVE-FALLBACK ({method}) -- no absolute anchor; not comparable across refreshes"]
    if below:
        flags.append(
            f"BELOW-COVERAGE-FLOOR ({n_present}/{len(universe)} = {cov_pct:.0f}%) -- low-confidence"
        )
    meta = {
        "indicator": spec.indicator,
        "direction": spec.direction if spec.invert is None else
                     ("low_risk" if spec.invert else "high_risk"),
        "anchor": spec.anchor_str(),
        "method": f"relative:{method}",
        "coverage_pct": cov_pct,
        "n_present": n_present,
        "n_total": len(universe),
        "below_floor": below,
        "flags": flags,
    }
    return ScaleResult(scores, meta)


def _quantile(sorted_vals, q):
    """Linear-interpolation quantile of a pre-sorted list."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


# --- drop-and-re-average (Rule 9) ------------------------------------------

def drop_and_average(value_lists, coverage_floor=0.5, min_present=2):
    """Average present inputs per the missing-data rule, with a coverage floor.

    value_lists : list of per-input value-lists OR a single list of values.
                  Pass the parallel signals for ONE unit (e.g. one country's
                  several signal scores) as a flat list.

    Returns (mean_or_None, coverage_pct, below_floor_bool). Below the floor the
    mean is still computed but `below_floor` is True so the caller can mark it
    low-confidence -- never silently fabricated, never defaulted to 0.
    """
    present = [v for v in value_lists if v is not None
               and not (isinstance(v, float) and math.isnan(v))]
    n_total = len(value_lists)
    n_present = len(present)
    cov_pct = (100.0 * n_present / n_total) if n_total else 0.0
    below = (n_total > 0) and (
        (n_present / n_total) < coverage_floor or n_present < min_present
    )
    mean = (sum(present) / n_present) if n_present else None
    return mean, cov_pct, below
