"""Integration tests for pipeline stages.

Uses mock data (no network access required) to test end-to-end
pipeline logic without needing real GEO downloads.
"""

from pathlib import Path

import numpy as np
import pytest

from scruticell.pipeline.nodes.matrix import _is_gene_x_cell
from scruticell.parsing.metadata import _split_characteristics, extract_gsm_metadata
from scruticell.readers.csv_matrix import (
    _detect_delimiter,
    _detect_orientation,
    _has_row_names,
    _looks_like_gene_name,
)


class TestCharacteristicsParsing:
    def test_split_simple_colon(self):
        pairs = _split_characteristics(["tissue: PBMC", "disease: healthy"])
        assert pairs == [("tissue", "PBMC"), ("disease", "healthy")]

    def test_split_no_colon(self):
        pairs = _split_characteristics(["just_a_value"])
        assert pairs == [("unknown_field", "just_a_value")]

    def test_split_empty(self):
        assert _split_characteristics(None) == []
        assert _split_characteristics("") == []

    def test_split_spaces_in_value(self):
        pairs = _split_characteristics(["cell type: CD4+ T cell"])
        assert pairs == [("cell type", "CD4+ T cell")]

    def test_split_strips_whitespace(self):
        pairs = _split_characteristics(["  tissue :  liver  "])
        assert pairs == [("tissue", "liver")]

    def test_split_multiple_values_per_gsm(self):
        pairs = _split_characteristics([
            "Sex: M",
            "Age: 45",
            "Condition: treated",
        ])
        assert len(pairs) == 3
        assert pairs[1] == ("Age", "45")


class TestGsmMetadataExtraction:
    def test_extract_basic_gsm(self):
        raw = {
            "GSM001": {
                "_geo_title": "Sample 1",
                "source_name_ch1": "PBMC",
                "characteristics_ch1": ["tissue: blood", "disease: control"],
                "taxid_ch1": "9606",
            }
        }
        result = extract_gsm_metadata(raw)
        assert "GSM001" in result
        assert result["GSM001"]["tissue"] == "blood"
        assert result["GSM001"]["disease"] == "control"
        assert result["GSM001"]["source_name"] == "PBMC"

    def test_extract_organism_field(self):
        raw = {
            "GSM001": {
                "_geo_title": "Sample",
                "organism_ch1": "Homo sapiens",
                "source_name_ch1": "",
                "characteristics_ch1": [],
            }
        }
        result = extract_gsm_metadata(raw)
        assert result["GSM001"]["organism_ch1"] == "Homo sapiens"


class TestMatrixOrientationHeuristics:
    def test_gene_x_cell_detected(self):
        gene_names = ["BRCA1", "TP53", "GAPDH"]
        assert _is_gene_x_cell(gene_names, (3, 1000))

    def test_cell_x_gene_detected(self):
        gene_names = ["BRCA1", "TP53", "GAPDH"]
        assert not _is_gene_x_cell(gene_names, (1000, 3))

    def test_ambiguous_falls_back(self):
        gene_names = ["gene1", "gene2", "gene3"]
        assert _is_gene_x_cell(gene_names, (3, 3)) is False
        assert _is_gene_x_cell(gene_names, (100, 200)) is True

    def test_empty_input(self):
        assert _is_gene_x_cell([], (0, 0)) is False


class TestCsvHeuristics:
    def test_gene_like_detection(self):
        assert _looks_like_gene_name("ENSG00000139618")
        assert _looks_like_gene_name("ENSMUSG00000001")
        assert _looks_like_gene_name("BRCA1")
        assert _looks_like_gene_name("TP53")
        assert _looks_like_gene_name("GAPDH")
        assert _looks_like_gene_name("MT-ND1")
        assert not _looks_like_gene_name("42")

    def test_delimiter_detection_tab(self, tmp_path: Path):
        f = tmp_path / "test.tsv"
        f.write_text("a\tb\tc\n1\t2\t3\n")
        assert _detect_delimiter(f) == "\t"

    def test_delimiter_detection_comma(self, tmp_path: Path):
        f = tmp_path / "test.csv"
        f.write_text("a,b,c\n1,2,3\n")
        assert _detect_delimiter(f) == ","

    def test_row_names_detection(self, tmp_path: Path):
        f = tmp_path / "has_rownames.csv"
        f.write_text(",cell1,cell2\ngene1,1,2\n")
        assert _has_row_names(f, ",") is True

    def test_no_row_names(self, tmp_path: Path):
        f = tmp_path / "no_rownames.csv"
        f.write_text("cell1,cell2\n1,2\n")
        assert _has_row_names(f, ",") is False
