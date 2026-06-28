"""Tests for alignment node, validation node, and output node."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from scruticell.pipeline.state import PipelineState
from scruticell.pipeline.nodes.alignment import (
    align_cells_node,
    output_node,
    unify_genes_node,
    _detect_barcode_sample_prefixes,
    _align_matrix_to_annotations,
)
from scruticell.pipeline.nodes.validation import (
    validate_node,
    _check_count_integrity,
    _check_alignment,
    _check_metadata_completeness,
    _check_batch_validity,
)
from scruticell.alignment.barcodes import align_barcodes_to_metadata

try:
    import anndata  # noqa: F401
    ANNDATA_AVAILABLE = True
except ImportError:
    ANNDATA_AVAILABLE = False


class TestBarcodeAlignment:
    def test_perfect_match(self):
        barcodes = ["AAACCCAAGAAACTCA", "AAACCCAAGAAAGCCC"]
        meta = ["AAACCCAAGAAACTCA", "AAACCCAAGAAAGCCC"]
        aligned_mat, aligned_meta, rate = align_barcodes_to_metadata(barcodes, meta)
        assert aligned_mat == barcodes
        assert rate == 1.0

    def test_case_mismatch(self):
        barcodes = ["AAACCCAAGAAACTCA", "AAACCCAAGAAAGCCC"]
        meta = ["aaacccaagaaactca", "aaacccaagaaagccc"]
        _, _, rate = align_barcodes_to_metadata(barcodes, meta)
        assert rate > 0.8

    def test_empty_input(self):
        _, _, rate = align_barcodes_to_metadata([], [])
        assert rate == 1.0

    def test_sample_prefix_detection(self):
        barcodes = [f"S1_{i}" for i in range(10)] + [f"S2_{i}" for i in range(10)]
        result = _detect_barcode_sample_prefixes(barcodes, ["GSM001", "GSM002"])
        assert result is not None
        assert len(set(result)) == 2

    def test_sample_prefix_without_delimiter(self):
        barcodes = [f"cell_{i}" for i in range(20)]
        result = _detect_barcode_sample_prefixes(barcodes, ["GSM001", "GSM002"])
        assert result is None


class TestGeneUnification:
    def test_detect_gene_id_system_symbol(self, tmp_path):
        state = PipelineState(
            gse_id="GSE12345",
            gene_names_raw=["BRCA1", "TP53", "GAPDH", "ACTB"],
            expression_matrix=np.ones((4, 10)),
        )
        result = unify_genes_node(state)
        assert result["var"] is not None
        assert len(result["gene_names_unified"]) == 4
        assert result["duplicate_gene_strategy"] == "none"

    def test_handle_duplicate_genes(self, tmp_path):
        state = PipelineState(
            gse_id="GSE12345",
            gene_names_raw=["BRCA1", "BRCA1", "TP53", "TP53", "GAPDH"],
            expression_matrix=np.ones((5, 10)),
        )
        result = unify_genes_node(state)
        var = result["var"]
        assert len(var) <= 3
        assert result["duplicate_gene_strategy"] == "first"

    def test_flag_mito_ribo_genes(self, tmp_path):
        state = PipelineState(
            gse_id="GSE12345",
            gene_names_raw=["MT-ND1", "RPS3", "GAPDH", "MT-CO1", "RPL10"],
            expression_matrix=np.ones((5, 10)),
        )
        result = unify_genes_node(state)
        var = result["var"]
        assert var.loc["MT-ND1", "is_mito"] == True
        assert var.loc["RPS3", "is_ribo"] == True
        assert var.loc["GAPDH", "is_mito"] == False

    def test_empty_genes(self, tmp_path):
        state = PipelineState(gse_id="GSE12345", gene_names_raw=[])
        result = unify_genes_node(state)
        assert result["var"] is None


class TestCellAlignment:
    def test_single_gsm_assignment(self, tmp_path):
        state = PipelineState(
            gse_id="GSE12345",
            cell_barcodes_raw=["AAACCCAAGAAACTCA-1", "AAACCCAAGAAAGCCC-1"],
            expression_matrix=np.ones((5, 2)),
            gsm_entries={"GSM001": {"title": "Sample1"}},
            parsed_characteristics={"GSM001": {"disease": "control"}},
        )
        result = align_cells_node(state)
        obs = result["obs"]
        assert obs is not None
        assert len(obs) == 2
        assert obs["batch"].iloc[0] == "GSM001"
        assert obs["disease"].iloc[0] == "control"

    def test_no_gsm_falls_back_to_matrix_name(self, tmp_path):
        matrix_file = Path("/tmp/GSE12345/matrix.h5ad")
        state = PipelineState(
            gse_id="GSE12345",
            cell_barcodes_raw=["AAACCCAAGAAACTCA-1"],
            expression_matrix=np.ones((5, 1)),
            matrix_file=matrix_file,
        )
        result = align_cells_node(state)
        obs = result["obs"]
        assert obs is not None
        assert obs["batch"].iloc[0] == "matrix"

    def test_10x_barcodes_normalized(self, tmp_path):
        state = PipelineState(
            gse_id="GSE12345",
            cell_barcodes_raw=["aaacccaagaaactca-1", "AAACCCAAGAAAGCCC-2"],
            expression_matrix=np.ones((5, 2)),
            gsm_entries={"GSM001": {}},
            parsed_characteristics={"GSM001": {}},
        )
        result = align_cells_node(state)
        aligned = result["cell_barcodes_aligned"]
        assert aligned[0] == "AAACCCAAGAAACTCA"
        assert aligned[1] == "AAACCCAAGAAAGCCC"

    def test_empty_barcodes(self, tmp_path):
        state = PipelineState(gse_id="GSE12345", cell_barcodes_raw=[])
        result = align_cells_node(state)
        assert result["obs"] is None
        assert any("barcodes" in e.lower() for e in state.errors)


class TestValidation:
    def test_passing_validation(self, tmp_path):
        n_cells, n_genes = 100, 200
        matrix = np.random.randint(0, 100, (n_genes, n_cells)).astype(np.float32)
        matrix[matrix < 0] = 0
        obs = pd.DataFrame(
            {"batch": ["batch_A"] * 50 + ["batch_B"] * 50},
            index=[f"cell_{i}" for i in range(n_cells)],
        )
        var = pd.DataFrame(
            {"gene_symbol": [f"gene_{i}" for i in range(n_genes)]},
            index=[f"gene_{i}" for i in range(n_genes)],
        )

        state = PipelineState(
            gse_id="GSE12345",
            expression_matrix=matrix,
            obs=obs,
            var=var,
            batch_column="batch",
            parsed_characteristics={"GSM001": {"tissue": "blood"}},
        )
        result = validate_node(state)
        assert result["validation_passed"]
        assert result["confidence_scores"]["alignment_accuracy"] == 1.0

    def test_detect_non_integer_values(self, tmp_path):
        n_cells, n_genes = 100, 200
        matrix = np.random.randn(n_genes, n_cells).astype(np.float32) * 10 + 50
        obs = pd.DataFrame(index=[f"cell_{i}" for i in range(n_cells)])
        obs["batch"] = "batch_A"
        var = pd.DataFrame(index=[f"gene_{i}" for i in range(n_genes)])

        state = PipelineState(
            gse_id="GSE12345",
            expression_matrix=matrix,
            obs=obs,
            var=var,
            batch_column="batch",
        )
        result = validate_node(state)
        assert result["confidence_scores"]["count_integrity"] < 1.0

    def test_detect_negative_values(self, tmp_path):
        matrix = np.array([[-1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        obs = pd.DataFrame(index=["c1", "c2"])
        obs["batch"] = "batch_A"
        var = pd.DataFrame(index=["g1", "g2"])

        score = _check_count_integrity(matrix, [], [])
        assert score <= 0.5

    def test_dimension_mismatch_fails(self, tmp_path):
        matrix = np.ones((10, 5), dtype=np.float32)
        obs = pd.DataFrame(index=[f"cell_{i}" for i in range(100)])
        obs["batch"] = "batch_A"
        var = pd.DataFrame(index=[f"gene_{i}" for i in range(10)])

        score = _check_alignment(matrix, obs, var, [])
        assert score < 1.0

    def test_single_batch_many_cells_warns(self, tmp_path):
        obs = pd.DataFrame(
            {"batch": ["only_batch"] * 2000},
            index=[f"cell_{i}" for i in range(2000)],
        )
        score = _check_batch_validity(obs, "batch", [], [])
        assert score < 0.8


class TestOutput:
    @pytest.mark.skipif(not ANNDATA_AVAILABLE, reason="anndata not installed")
    def test_assemble_anndata(self, tmp_path):
        n_cells, n_genes = 10, 20
        matrix = np.random.randint(0, 100, (n_genes, n_cells)).astype(np.float32)
        obs = pd.DataFrame(
            {"batch": ["GSM001"] * n_cells, "disease": ["control"] * n_cells},
            index=[f"cell_{i}" for i in range(n_cells)],
        )
        var = pd.DataFrame(
            {"gene_symbol": [f"gene_{i}" for i in range(n_genes)]},
            index=[f"gene_{i}" for i in range(n_genes)],
        )

        state = PipelineState(
            gse_id="GSE12345",
            output_dir=tmp_path,
            expression_matrix=matrix,
            obs=obs,
            var=var,
            organism="Homo sapiens",
            gsm_entries={"GSM001": {"title": "Sample"}},
        )

        result = output_node(state)
        output_path = result["output_path"]
        assert output_path is not None
        assert output_path.exists()
        assert output_path.suffix == ".h5ad"

        import anndata
        adata = anndata.read_h5ad(output_path)
        assert adata.n_obs == n_cells
        assert adata.n_vars == n_genes
        assert adata.uns["gse_id"] == "GSE12345"
        assert adata.uns["organism"] == "Homo sapiens"

    @pytest.mark.skipif(not ANNDATA_AVAILABLE, reason="anndata not installed")
    def test_assemble_anndata_sparse(self, tmp_path):
        matrix = sparse.csr_matrix(np.ones((5, 10), dtype=np.float32))
        obs = pd.DataFrame(
            {"batch": ["GSM001"] * 10},
            index=[f"cell_{i}" for i in range(10)],
        )
        var = pd.DataFrame(index=[f"gene_{i}" for i in range(5)])

        state = PipelineState(
            gse_id="GSE12345",
            output_dir=tmp_path,
            expression_matrix=matrix,
            obs=obs,
            var=var,
        )
        result = output_node(state)
        assert result["output_path"].exists()

    @pytest.mark.skipif(not ANNDATA_AVAILABLE, reason="anndata not installed")
    def test_output_without_obs_var_creates_defaults(self, tmp_path):
        matrix = np.ones((3, 4), dtype=np.float32)
        state = PipelineState(
            gse_id="GSE12345",
            output_dir=tmp_path,
            expression_matrix=matrix,
        )
        result = output_node(state)
        assert result["output_path"].exists()

    @pytest.mark.skipif(not ANNDATA_AVAILABLE, reason="anndata not installed")
    def test_output_no_matrix_fails(self, tmp_path):
        state = PipelineState(gse_id="GSE12345", output_dir=tmp_path)
        result = output_node(state)
        assert result["output_path"] is None
        assert any("AnnData" in e for e in state.errors)

    def test_align_matrix_transposes_when_needed(self):
        matrix = np.ones((5, 10), dtype=np.float32)
        obs = pd.DataFrame(index=[f"c{i}" for i in range(10)])
        var = pd.DataFrame(index=[f"g{i}" for i in range(5)])
        result = _align_matrix_to_annotations(matrix, obs, var)
        assert result.shape == (10, 5)
