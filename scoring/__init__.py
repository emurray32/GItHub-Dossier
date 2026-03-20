"""Minimal scoring compatibility surface for legacy callers."""
from __future__ import annotations


def get_scoring_fingerprint() -> str:
    """Return a stable fingerprint for the bundled scoring compatibility layer."""
    return 'scoring-compat-v1'


__all__ = ['get_scoring_fingerprint']
