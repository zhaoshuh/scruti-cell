"""Tests for metadata extraction."""

from scruticell.parsing.metadata import identify_batches_from_gsm


def test_batch_identification_uses_gsm_proxy():
    """When no explicit batch field exists, GSM accession is used as batch."""
    gsm_entries = {"GSM100001": {}, "GSM100002": {}}
    batch_map = identify_batches_from_gsm(gsm_entries)
    assert batch_map["GSM100001"] == "GSM100001"
    assert batch_map["GSM100002"] == "GSM100002"
