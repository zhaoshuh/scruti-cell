"""Edge case and regression tests for the full pipeline."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scruticell.alignment.barcodes import (
    align_barcodes_to_metadata,
    detect_barcode_format,
    normalize_barcodes,
    strip_barcode_suffix,
)
from scruticell.alignment.genes import build_var_dataframe
from scruticell.knowledge.field_synonyms import resolve_field_name
from scruticell.knowledge.gene_mapping import detect_gene_id_system, make_unique_genes
from scruticell.license import LicenseManager, get_license, is_enterprise
from scruticell.parsing.metadata import (
    _split_characteristics,
    identify_batches_from_gsm,
)


class TestBarcodeEdgeCases:
    def test_mixed_barcode_types(self):
        barcodes = ["AAACCCAAGAAACTCA-1", "cell_001", "AAACCCAAGAAAGCCC-1"]
        fmt = detect_barcode_format(barcodes)
        assert fmt in ("10x_v2", "10x_v3", "unknown")

    def test_very_long_barcodes(self):
        barcodes = ["AAACCCAAGAAACTCAAAAAAA-1", "AAACCCAAGAAAGCCCCCCCCC-1"]
        fmt = detect_barcode_format(barcodes)
        assert fmt in ("unknown",)

    def test_empty_string_barcodes(self):
        barcodes = ["", "AAACCCAAGAAACTCA-1", ""]
        normalized = normalize_barcodes(barcodes, fmt="10x_v3")
        assert normalized[1] == "AAACCCAAGAAACTCA"

    def test_numeric_barcodes(self):
        barcodes = ["12345", "67890"]
        fmt = detect_barcode_format(barcodes)
        assert fmt == "unknown"

    def test_suffix_without_match(self):
        assert strip_barcode_suffix("CELL-001") == "CELL-001"


class TestGeneEdgeCases:
    def test_mixed_id_systems(self):
        ids = ["ENSG00000000003", "BRCA1", "ENSG00000000005", "TP53"]
        system = detect_gene_id_system(ids)
        assert system in ("symbol", "unknown")

    def test_ensembl_with_version_numbers(self):
        ids = ["ENSG00000139618.17", "ENSG00000012048.15"]
        system = detect_gene_id_system(ids)
        assert system == "ensembl_gene"

    def test_lowercase_ensembl(self):
        ids = ["ensg00000139618", "ensg00000012048"]
        system = detect_gene_id_system(ids)
        assert system in ("symbol", "unknown")

    def test_unique_genes_all_duplicates(self):
        genes = ["BRCA1", "BRCA1", "BRCA1"]
        unique, strat = make_unique_genes(genes, "first")
        assert unique == ["BRCA1"]
        assert "dropped 2" in strat

    def test_unique_genes_no_duplicates(self):
        genes = ["BRCA1", "TP53", "GAPDH"]
        unique, strat = make_unique_genes(genes, "first")
        assert unique == genes
        assert strat == "first"

    def test_gene_with_spaces_and_quotes(self):
        at_scale_ids = ["BRCA1", ' "TP53" ', " GAPDH "]
        unique, _ = make_unique_genes(at_scale_ids, "first")
        assert len(unique) == 3

    def test_many_genes_build_var(self):
        genes = [f"gene_{i}" for i in range(5000)]
        var = build_var_dataframe(genes)
        assert len(var) == 5000
        assert var.index.name == "gene_symbol"


class TestMetadataEdgeCases:
    def test_empty_gsm_entries(self):
        result = identify_batches_from_gsm({})
        assert result == {}

    def test_single_gsm(self):
        result = identify_batches_from_gsm({"GSM001": {"batch": "batch_1"}})
        assert result["GSM001"] == "batch_1"

    def test_characteristics_with_equals(self):
        pairs = _split_characteristics(["key=value"])
        assert pairs == [("unknown_field", "key=value")]

    def test_characteristics_json_like(self):
        pairs = _split_characteristics(['{"key": "value"}'])
        assert len(pairs) == 1

    def test_characteristics_multi_colon(self):
        pairs = _split_characteristics(["cell: type: CD4+ T"])
        assert pairs == [("cell", "type: CD4+ T")]


class TestFieldResolution:
    def test_unicode_field_names(self):
        result = resolve_field_name("disease_state")
        assert result == "disease"

    def test_field_with_special_chars(self):
        result = resolve_field_name("disease-state")
        assert result == "disease-state"


class TestLicense:
    def test_default_is_oss(self):
        lm = LicenseManager()
        assert lm.tier == "oss"

    def test_oss_features(self):
        lm = LicenseManager()
        assert not lm.neo4j_enabled
        assert not lm.llm_enabled
        assert not lm.mcts_enabled
        assert lm.reflexion_enabled

    def test_invalid_key_detected(self):
        lm = LicenseManager()
        assert not lm._validate_key("invalid")
        assert not lm._validate_key("")
        assert not lm._validate_key("a-b-c")

    def test_license_generation(self):
        lm = LicenseManager()
        key = lm.generate_license_key("test_client")
        assert len(key) > 20
        assert "test_client" in key

    def test_get_license_singleton(self):
        lm1 = get_license()
        lm2 = get_license()
        assert lm1 is lm2

    def test_is_enterprise_default_false(self):
        assert not is_enterprise()


class TestPipelineEdgeCases:
    def test_empty_output_dir(self, tmp_path):
        from scruticell.pipeline.state import PipelineState
        state = PipelineState(gse_id="GSE_EMPTY", output_dir=tmp_path)
        assert state.gse_id == "GSE_EMPTY"

    def test_multiple_retries_exhausted(self):
        from scruticell.pipeline.state import PipelineState
        state = PipelineState(gse_id="GSE_X", max_retries=1)
        state.retry_count = 1
        assert not state.can_retry()

    def test_retries_available(self):
        from scruticell.pipeline.state import PipelineState
        state = PipelineState(gse_id="GSE_X", max_retries=3)
        state.retry_count = 1
        assert state.can_retry()

    def test_error_accumulation(self):
        from scruticell.pipeline.state import PipelineState
        state = PipelineState(gse_id="GSE_X")
        state.add_error("first error")
        state.add_error("second error")
        assert len(state.errors) == 2
        assert state.errors[0] == "first error"

    def test_csv_with_empty_rows_skipped(self, tmp_path):
        csv_path = tmp_path / "empty_rows.csv"
        csv_path.write_text(",cell1\n\nBRCA1,10\n\nTP53,20\n")
        from scruticell.readers.csv_matrix import read_csv_matrix
        result = read_csv_matrix(csv_path)
        assert result["gene_names"] == ["BRCA1", "TP53"]

    def test_matrix_big_values(self):
        matrix = np.array([[1e8, 2.0], [3.0, 4.0]], dtype=np.float32)
        from scruticell.pipeline.nodes.validation import _check_count_integrity
        errors: list[str] = []
        warnings: list[str] = []
        score = _check_count_integrity(matrix, errors, warnings)
        assert score < 1.0 or len(warnings) > 0
