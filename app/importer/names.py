"""Canonical labour-name resolution.

The same person is written three ways across the workbook -- NEERAJ, NEERAJ
KUMAR, NEERAJ . P -- and crucially 'NEERAJ' is a prefix of 'NEERAJP', so a
naive LIKE match silently attributes one person's wages to another. Exact
match always wins; then a spelling that differs only by a doubled letter --
October writes IYYAPPAN on Sheet2 and IYAPPAN on Sheet15 -- and only then is
the longest prefix considered.
"""

from __future__ import annotations

import re


def norm(name: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


def _squeeze(key: str) -> str:
    return re.sub(r"(.)\1+", r"\1", key)


def resolve(raw: str | None, candidates: dict[str, object]) -> object | None:
    """``candidates`` maps a normalised canonical name to anything."""
    key = norm(raw)
    if not key:
        return None
    if key in candidates:
        return candidates[key]
    same = [k for k in candidates if _squeeze(k) == _squeeze(key)]
    if len(same) == 1:
        return candidates[same[0]]
    longer = [k for k in candidates if key.startswith(k)]
    if longer:
        return candidates[max(longer, key=len)]
    shorter = [k for k in candidates if k.startswith(key)]
    if shorter:
        return candidates[min(shorter, key=len)]
    return None
