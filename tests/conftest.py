"""Test fixtures for Scrut-Cell pipeline tests.

Provides mock GEO data, sample SOFT files, and test matrices
for reproducible testing without network access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def sample_gse_id() -> str:
    """Sample GSE accession for testing."""
    return "GSE12345"


@pytest.fixture
def mock_soft_content() -> str:
    """Mock SOFT family file content for testing."""
    return """
^SERIES = GSE12345
!Series_title = Test single-cell RNA-seq dataset
!Series_summary = This is a test dataset for pipeline validation
!Series_overall_design = PBMCs from 2 healthy donors, 10X Genomics 3' v3
!Series_pubmed_id = 12345678
!Series_platform_id = GPL24676
!Series_sample_id = GSM100001
!Series_sample_id = GSM100002

^PLATFORM = GPL24676
!Platform_title = Illumina NovaSeq 6000 (Homo sapiens)

^SAMPLE = GSM100001
!Sample_title = Donor1_PBMC
!Sample_organism_ch1 = Homo sapiens
!Sample_characteristics_ch1 = tissue: PBMC
!Sample_characteristics_ch1 = disease: healthy
!Sample_characteristics_ch1 = sex: M
!Sample_characteristics_ch1 = age: 45
!Sample_supplementary_file_1 = GSM100001_filtered_feature_bc_matrix.h5

^SAMPLE = GSM100002
!Sample_title = Donor2_PBMC
!Sample_organism_ch1 = Homo sapiens
!Sample_characteristics_ch1 = tissue: PBMC
!Sample_characteristics_ch1 = disease: COVID-19
!Sample_characteristics_ch1 = sex: F
!Sample_characteristics_ch1 = age: 32
!Sample_supplementary_file_1 = GSM100002_filtered_feature_bc_matrix.h5
"""


@pytest.fixture
def mock_pipeline_state() -> dict[str, Any]:
    """Minimal pipeline state for node testing."""
    return {
        "gse_id": "GSE12345",
        "output_dir": None,
        "download_dir": None,
        "soft_file": None,
        "supplementary_files": [],
        "raw_metadata": {},
        "gsm_entries": {},
        "parsed_characteristics": {},
        "organism": "Homo sapiens",
        "platform_ids": ["GPL24676"],
        "matrix_file": None,
        "matrix_format": "",
        "expression_matrix": None,
        "is_sparse": False,
        "cell_barcodes_raw": [],
        "gene_names_raw": [],
        "pubmed_id": "12345678",
        "literature_context": {},
        "obs": None,
        "var": None,
        "validation_passed": False,
        "confidence_scores": {},
        "validation_errors": [],
        "retry_count": 0,
        "max_retries": 3,
        "reflexion_notes": [],
        "current_strategy": {},
        "errors": [],
        "fatal_error": False,
    }


@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Path:
    """Temporary data directory for test artifacts."""
    data_dir = tmp_path / "scruticell_test_data"
    data_dir.mkdir()
    return data_dir
