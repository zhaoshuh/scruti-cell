"""Tests for gene name unification."""

from scruticell.knowledge.gene_mapping import (
    detect_gene_id_system,
    is_mitochondrial,
    is_ribosomal,
    normalize_gene_symbol,
)


def test_detect_gene_id_system_ensembl():
    """Should detect Ensembl gene IDs."""
    ids = ["ENSG00000139618", "ENSG00000012048", "ENSG00000141510"]
    assert detect_gene_id_system(ids) == "ensembl_gene"


def test_detect_gene_id_system_symbol():
    """Should detect gene symbols."""
    ids = ["BRCA1", "TP53", "GAPDH", "ACTB"]
    assert detect_gene_id_system(ids) == "symbol"


def test_detect_gene_id_system_entrez():
    """Should detect Entrez gene IDs."""
    ids = ["672", "7157", "2597", "60"]
    assert detect_gene_id_system(ids) == "entrez"


def test_is_mitochondrial():
    """Should correctly identify mitochondrial genes."""
    assert is_mitochondrial("MT-ND1")
    assert is_mitochondrial("MT-CO1")
    assert is_mitochondrial("MTRNR2L1")
    assert not is_mitochondrial("GAPDH")
    assert not is_mitochondrial("ACTB")


def test_is_ribosomal():
    """Should correctly identify ribosomal genes."""
    assert is_ribosomal("RPS3")
    assert is_ribosomal("RPL10")
    assert is_ribosomal("RPLP0")
    assert not is_ribosomal("GAPDH")


def test_normalize_gene_symbol():
    """Should strip whitespace from gene symbols."""
    assert normalize_gene_symbol(" BRCA1 ") == "BRCA1"
    assert normalize_gene_symbol("TP53") == "TP53"
