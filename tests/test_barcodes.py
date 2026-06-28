"""Tests for cell barcode alignment."""

from scruticell.alignment.barcodes import (
    detect_barcode_format,
    normalize_barcodes,
    strip_barcode_suffix,
)


def test_detect_barcode_format_10x_v3():
    """Should detect 10X v3 barcodes with -1 suffix."""
    barcodes = [
        "AAACCCAAGAAACTCA-1",
        "AAACCCAAGAAAGCCC-1",
        "AAACCCAAGAAATTCG-1",
        "AAACCCAAGAACCCGA-1",
        "AAACCCAAGAACCTTC-1",
    ]
    fmt = detect_barcode_format(barcodes)
    assert fmt == "10x_v3"


def test_detect_barcode_format_10x_v2():
    """Should detect 10X v2 barcodes without suffix."""
    barcodes = [
        "AAACCCAAGAAACTCA",
        "AAACCCAAGAAAGCCC",
        "AAACCCAAGAAATTCG",
        "AAACCCAAGAACCCGA",
        "AAACCCAAGAACCTTC",
    ]
    fmt = detect_barcode_format(barcodes)
    assert fmt == "10x_v2"


def test_strip_barcode_suffix():
    """Should strip -1 suffix from 10X barcodes."""
    assert strip_barcode_suffix("AAACCCAAGAAACTCA-1") == "AAACCCAAGAAACTCA"
    assert strip_barcode_suffix("AAACCCAAGAAACTCA") == "AAACCCAAGAAACTCA"


def test_normalize_barcodes_10x():
    """Should upper-case and strip suffixes for 10X format."""
    barcodes = ["aaacccaagaaactca-1", "AAACCCAAGAAAGCCC-2"]
    normalized = normalize_barcodes(barcodes, fmt="10x_v3")
    assert normalized[0] == "AAACCCAAGAAACTCA"
    assert normalized[1] == "AAACCCAAGAAAGCCC"
