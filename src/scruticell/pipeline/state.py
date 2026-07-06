"""LangGraph pipeline state definition.

The PipelineState flows through all nodes in the graph, accumulating
results at each stage. Every node reads from and writes to this state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd


@dataclass
class PipelineState:
    """State object flowing through the GEO -> AnnData pipeline graph.

    Each node in the LangGraph graph reads from this state object
    and returns an updated copy with its output fields populated.
    """

    # === Input ===
    gse_id: str = ""
    output_dir: Optional[Path] = None

    # === Download Phase ===
    download_dir: Optional[Path] = None
    soft_file: Optional[Path] = None
    supplementary_files: list[Path] = field(default_factory=list)
    download_errors: list[str] = field(default_factory=list)
    # Mapping: filename (basename) -> GSM ID or "__GSE__"
    file_gsm_map: dict[str, str] = field(default_factory=dict)

    # === Parse Phase ===
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    gsm_entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    parsed_characteristics: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw_gsm_entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    organism: str = ""
    platform_ids: list[str] = field(default_factory=list)

    # === Matrix Phase ===
    matrix_file: Optional[Path] = None
    matrix_files: list[Path] = field(default_factory=list)
    matrix_format: str = ""  # "h5ad", "mtx", "csv", "rds", "loom"
    expression_matrix: Optional[np.ndarray] = None
    is_sparse: bool = False
    matrix_shape: tuple[int, int] = (0, 0)
    cell_barcodes_raw: list[str] = field(default_factory=list)
    gene_names_raw: list[str] = field(default_factory=list)

    # === Literature Phase ===
    pubmed_id: str = ""
    literature_context: dict[str, Any] = field(default_factory=dict)
    methods_text: str = ""
    literature_metadata_supplement: dict[str, Any] = field(default_factory=dict)

    # === Alignment Phase ===
    obs: Optional[pd.DataFrame] = None
    var: Optional[pd.DataFrame] = None
    cell_barcodes_aligned: list[str] = field(default_factory=list)
    gene_names_unified: list[str] = field(default_factory=list)
    duplicate_gene_strategy: str = ""
    batch_column: str = "batch"
    alignment_warnings: list[str] = field(default_factory=list)
    cell_batch_labels: list[str] = field(default_factory=list)

    # === Validation Phase ===
    validation_passed: bool = False
    confidence_scores: dict[str, float] = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)

    # === Reflexion Phase ===
    retry_count: int = 0
    max_retries: int = 3
    reflexion_notes: list[str] = field(default_factory=list)
    current_strategy: dict[str, Any] = field(default_factory=dict)
    strategy_history: list[dict[str, Any]] = field(default_factory=list)

    # === Output Phase ===
    output_path: Optional[Path] = None
    audit_log: list[dict[str, Any]] = field(default_factory=list)

    # === Errors ===
    errors: list[str] = field(default_factory=list)
    fatal_error: bool = False

    def add_error(self, error: str) -> None:
        """Append a non-fatal error."""
        self.errors.append(error)

    def add_validation_error(self, error: str) -> None:
        """Append a validation error."""
        self.validation_errors.append(error)

    def overall_confidence(self) -> float:
        """Compute weighted overall confidence score."""
        if not self.confidence_scores:
            return 1.0
        return float(np.mean(list(self.confidence_scores.values())))

    def needs_reflexion(self) -> bool:
        """Check if confidence is below threshold for auto-accept."""
        from scruticell.config import CONFIDENCE_THRESHOLD_AUTO
        return self.overall_confidence() < CONFIDENCE_THRESHOLD_AUTO

    def can_retry(self) -> bool:
        """Check if retry budget remains."""
        return self.retry_count < self.max_retries
