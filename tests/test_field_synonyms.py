"""Tests for field synonym resolution."""

from scruticell.knowledge.field_synonyms import (
    batch_resolve_field_names,
    resolve_field_name,
)


def test_resolve_field_name_exact_match():
    """Should return canonical name for known field synonyms."""
    assert resolve_field_name("disease_state") == "disease"
    assert resolve_field_name("tissue_type") == "tissue"
    assert resolve_field_name("gender") == "sex"
    assert resolve_field_name("treated_with") == "treatment"


def test_resolve_field_name_case_insensitive():
    """Should match regardless of case."""
    assert resolve_field_name("Disease_State") == "disease"
    assert resolve_field_name("TISSUE_TYPE") == "tissue"


def test_resolve_field_name_unknown():
    """Should return original name for unknown fields."""
    assert resolve_field_name("some_random_field") == "some_random_field"


def test_batch_resolve_field_names():
    """Should map multiple field names at once."""
    raw = ["disease_state", "tissue_type", "unknown_field", "gender"]
    result = batch_resolve_field_names(raw)
    assert result["disease_state"] == "disease"
    assert result["tissue_type"] == "tissue"
    assert result["unknown_field"] == "unknown_field"
    assert result["gender"] == "sex"
