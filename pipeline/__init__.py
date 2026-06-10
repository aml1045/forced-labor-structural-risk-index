"""FLSRI pipeline: ingest -> standardize -> aggregate -> composite.

Stdlib-only so the repo runs end to end with no external dependencies.
Scoring is a transparent placeholder (equal-weight mean) pending the v1
scoring rules (docs/scoring-rules.md); see aggregate.py / composite.py.
"""
