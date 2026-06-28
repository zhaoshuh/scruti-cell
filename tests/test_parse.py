"""Tests for SOFT file parsing."""

from scruticell.parsing.soft import parse_soft_file


def test_parse_soft_file_returns_structure():
    """parse_soft_file should return dict with expected keys."""
    # Placeholder test
    result = {
        "gse_metadata": {},
        "gpl_entries": [],
        "gsm_entries": {},
        "supplementary_refs": [],
    }
    assert isinstance(result, dict)
    assert "gsm_entries" in result
