"""Canonical labour-name resolution.

The same person is written three ways across the workbook -- NEERAJ, NEERAJ
KUMAR, NEERAJ . P -- and crucially 'NEERAJ' is a prefix of 'NEERAJP', so a
naive LIKE match silently attributes one person's wages to another. Exact
match always wins; only then is the longest prefix considered.
"""

from __future__ import annotations

import re


def norm(name: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


def resolve(raw: str | None, candidates: dict[str, object]) -> object | None:
    """``candidates`` maps a normalised canonical name to anything."""
    key = norm(raw)
    if not key:
        return None
    if key in candidates:
        return candidates[key]
    longer = [k for k in candidates if key.startswith(k)]
    if longer:
        return candidates[max(longer, key=len)]
    shorter = [k for k in candidates if k.startswith(key)]
    if shorter:
        return candidates[min(shorter, key=len)]
    return None
