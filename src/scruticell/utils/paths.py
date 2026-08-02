"""Cross-platform path normalization utilities."""

from __future__ import annotations


def normalize_ancestry(ancestry: str) -> str:
    """Convert backslashes to forward slashes in an ancestry path."""
    return ancestry.replace("\\", "/")


def ancestry_filename(ancestry: str) -> str:
    """Extract the leaf filename from an ancestry path.

    Handles both forward and backslash separators.
    """
    normalized = normalize_ancestry(ancestry)
    return normalized.split("/")[-1] if "/" in normalized else normalized
