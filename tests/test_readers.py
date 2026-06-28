"""Tests for expression matrix readers."""

import tempfile
from pathlib import Path

import numpy as np
import pytest

from scruticell.readers.csv_matrix import read_csv_matrix


def _write_csv(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


class TestCsvReader:
    def test_read_gene_x_cell_with_row_names(self, tmp_path: Path):
        csv_path = tmp_path / "matrix.csv"
        _write_csv(csv_path, """,cell1,cell2,cell3
BRCA1,10.0,20.0,30.0
TP53,5.0,15.0,25.0
GAPDH,100.0,200.0,300.0
""")
        result = read_csv_matrix(csv_path)
        assert result["matrix"].shape == (3, 3)
        assert result["gene_names"] == ["BRCA1", "TP53", "GAPDH"]
        assert result["cell_barcodes"] == ["cell1", "cell2", "cell3"]
        assert not result["is_sparse"]

    def test_detect_ensembl_gene_names(self, tmp_path: Path):
        csv_path = tmp_path / "ensembl_matrix.csv"
        _write_csv(csv_path, """ENSG00000000003,ENSG00000000005,ENSG00000000419
10.0,20.0,30.0
5.0,15.0,25.0
""")
        result = read_csv_matrix(csv_path)
        assert result["gene_names"] == ["ENSG00000000003", "ENSG00000000005", "ENSG00000000419"]
        assert result["matrix"].shape == (2, 3)

    def test_detect_tab_delimiter(self, tmp_path: Path):
        csv_path = tmp_path / "matrix.tsv"
        _write_csv(csv_path, "\tcell1\tcell2\nBRCA1\t10.5\t20.5\nTP53\t5.5\t15.5\n")
        result = read_csv_matrix(csv_path)
        assert result["gene_names"] == ["BRCA1", "TP53"]
        assert result["cell_barcodes"] == ["cell1", "cell2"]
        assert result["matrix"].shape == (2, 2)

    def test_cell_x_gene_orientation_detected(self, tmp_path: Path):
        csv_path = tmp_path / "cell_x_gene.csv"
        _write_csv(csv_path, """,cell1,cell2
geneA,10.0,20.0
geneB,5.0,15.0
""")
        result = read_csv_matrix(csv_path)
        assert len(result["gene_names"]) == 2
        assert len(result["cell_barcodes"]) == 2

    def test_returns_float32(self, tmp_path: Path):
        csv_path = tmp_path / "float_test.csv"
        _write_csv(csv_path, """,c1\nG1,42\n""")
        result = read_csv_matrix(csv_path)
        assert result["matrix"].dtype == np.float32
