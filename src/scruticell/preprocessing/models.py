"""Pydantic data models for the omics preprocessing module."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Signal extraction (context layer)
# ---------------------------------------------------------------------------

class MetadataSignal(BaseModel):
    """Structured signals extracted from GSM metadata."""

    gsm_id: str = ""
    molecule: str = ""
    library_strategy: str = ""
    library_source: str = ""
    title: str = ""
    description: str = ""
    organism: str = ""
    suspension: str = ""
    keywords: list[str] = Field(default_factory=list)


class FilenameSignal(BaseModel):
    """Structured signals extracted from file names and directory structure."""

    keywords: list[str] = Field(default_factory=list)
    companion_files: list[str] = Field(default_factory=list)
    subdirectory_hint: str = ""
    is_10x_format: bool = False
    raw_name: str = ""


class ContentSignal(BaseModel):
    """Structured signals extracted from file content inspection."""

    feature_id_type: str = ""
    feature_samples: list[str] = Field(default_factory=list)
    barcode_format: str = ""
    barcode_prefixes: list[str] = Field(default_factory=list)
    column_patterns: list[str] = Field(default_factory=list)
    n_rows: int = 0
    n_cols: int = 0
    is_sparse: bool = False


class FileSignals(BaseModel):
    """Aggregated signals for a single file."""

    ancestry: str = ""
    file_type: str = ""
    metadata: MetadataSignal = Field(default_factory=MetadataSignal)
    filename: FilenameSignal = Field(default_factory=FilenameSignal)
    content: ContentSignal = Field(default_factory=ContentSignal)

    def signal_fingerprint(self) -> str:
        """Stable hash of signal content for deduplication grouping."""
        return (
            f"{self.file_type}|"
            f"{self.content.feature_id_type}|"
            f"{self.content.barcode_format}|"
            f"{','.join(sorted(self.filename.keywords))}|"
        )


# ---------------------------------------------------------------------------
# L1 — Field-value mapping (metadata deduplication)
# ---------------------------------------------------------------------------

class ValueGroup(BaseModel):
    """A unique value for a field and which GSM have it."""

    value: str
    gsm_ids: list[str] = Field(default_factory=list)
    gsm_count: int = 0


class FieldValueMap(BaseModel):
    """Deduplicated values for one metadata field across all GSM."""

    field_name: str
    category: str = ""  # clinical / sample / experimental / technical
    values: list[ValueGroup] = Field(default_factory=list)
    distinct_count: int = 0


class FileContentPreview(BaseModel):
    """A file's content snapshot for LLM classification — what the data actually looks like.

    All fields are null/empty if the information is not available for this file type.
    """

    ancestry: str = ""
    file_type: str = ""                # csv/h5/mtx/h5ad/rds/loom/bed/cov/png/json/other
    n_rows: int | None = None
    n_cols: int | None = None
    is_sparse: bool = False

    # Columns (tabular files)
    columns: list[str] = Field(default_factory=list)

    # Feature axis
    feature_type: str = ""             # gene_symbol / ensembl / genomic_interval / cpg / protein / sgrna / unknown
    feature_samples: list[str] = Field(default_factory=list)

    # 10x v3 features.tsv.gz 第三列
    feature_types_detected: list[str] = Field(default_factory=list)

    # Barcode axis
    barcode_format: str = ""
    barcode_samples: list[str] = Field(default_factory=list)

    # H5AD specific
    obs_columns: list[str] = Field(default_factory=list)
    var_columns: list[str] = Field(default_factory=list)
    uns_keys: list[str] = Field(default_factory=list)

    # RDS specific
    rds_keys: list[str] = Field(default_factory=list)

    # Companion files in same directory
    companion_files: list[str] = Field(default_factory=list)

    # Raw text — LLM safety net when feature_type detection fails
    raw_lines: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# File metadata extraction — per-file deep parsing for attribution
# ---------------------------------------------------------------------------

class ColumnMeta(BaseModel):
    """Semantic metadata from a single token group (column header, barcode prefix, or unique value)."""

    original: str = ""
    tokens: list[str] = Field(default_factory=list)
    sample_ids: list[str] = Field(default_factory=list)
    donor_ids: list[str] = Field(default_factory=list)
    treatments: list[str] = Field(default_factory=list)
    timepoints: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    tissues: list[str] = Field(default_factory=list)
    replicates: list[str] = Field(default_factory=list)
    unrecognized: list[str] = Field(default_factory=list)

    def all_semantic_tokens(self) -> list[str]:
        """All semantically meaningful tokens for matching."""
        return (
            self.sample_ids + self.donor_ids + self.treatments +
            self.timepoints + self.conditions + self.tissues + self.replicates
        )


class FileNameMeta(BaseModel):
    """All metadata parsed from file name tokens."""

    tokens: list[str] = Field(default_factory=list)
    gsm_ids: list[str] = Field(default_factory=list)
    sample_ids: list[str] = Field(default_factory=list)
    donor_ids: list[str] = Field(default_factory=list)
    treatments: list[str] = Field(default_factory=list)
    timepoints: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    tissues: list[str] = Field(default_factory=list)
    replicates: list[str] = Field(default_factory=list)
    unrecognized: list[str] = Field(default_factory=list)
    raw: str = ""


class ColumnSummary(BaseModel):
    """Summary for a single column — header semantics + value analysis."""

    header_meta: ColumnMeta = Field(default_factory=ColumnMeta)
    unique_count: int = 0
    unique_values: list[str] = Field(default_factory=list)
    value_frequencies: dict[str, int] = Field(default_factory=dict)
    is_categorical: bool = False
    value_hints: list[dict] = Field(default_factory=list)
    dtype: str = ""


class RowIndexMeta(BaseModel):
    """Metadata from row index / barcodes / first column."""

    barcode_format: str = ""
    prefixes: list[str] = Field(default_factory=list)
    prefix_counts: dict[str, int] = Field(default_factory=dict)
    prefix_meta: list[ColumnMeta] = Field(default_factory=list)
    feature_id_type: str = ""
    feature_samples: list[str] = Field(default_factory=list)
    n_rows: int = 0


class FileMeta(BaseModel):
    """Complete metadata portrait of a single file — extracted signals from all sources."""

    ancestry: str = ""
    file_type: str = ""
    filename: FileNameMeta = Field(default_factory=FileNameMeta)
    columns: list[ColumnSummary] = Field(default_factory=list)
    row_index: RowIndexMeta = Field(default_factory=RowIndexMeta)
    companion_files: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# SampleKey — unique sample identifier (key ↔ sample_group 1:1 双射)
# ---------------------------------------------------------------------------

class SampleKey(BaseModel):
    """归属 key — 文件/文件组唯一归属标识。"""

    key: str                             # 主键：GSM ID 或推断的复合 key
    key_source: str = ""                 # "gsm_id" | "inferred_composite"
    gsm_id: str | None = None            # 若 key 本身是 GSM ID
    inferred_from: dict[str, str] = Field(default_factory=dict)
                                         # 推断依据，e.g. {"donor": "P1", "treatment": "control"}


# ---------------------------------------------------------------------------
# Attribution result — signal collection for LLM, NOT decision
# ---------------------------------------------------------------------------

class AttributionResult(BaseModel):
    """Pure signal collection. LLM (classifier) makes all decisions."""

    ancestry: str = ""
    source: str = ""                     # "ancestry" | "soft_ref" | "none"
    confidence: float = 0.0
    candidates: list[str] = Field(default_factory=list)
                                         # 候选 GSM ID（L0 ancestry + L1 SOFT ref）
    signals: dict[str, Any] = Field(default_factory=dict)
                                         # 从 FileMeta 提取的 token 信号
    note: str = ""


# ---------------------------------------------------------------------------
# L2+L3 — File signal grouping (content deduplication + GSM linkage + content preview)
# ---------------------------------------------------------------------------

class FileSignalGroup(BaseModel):
    """A group of files sharing identical signal patterns."""

    pattern: str = ""
    representative: FileSignals = Field(default_factory=FileSignals)
    file_count: int = 0
    gsm_ids: list[str] = Field(default_factory=list)
    ancestry_pattern: str = ""

    shape_range: dict[str, int] = Field(default_factory=dict)
    is_sparse: bool = False
    content_preview: FileContentPreview | None = None

    # Deep file metadata portrait (for attribution)
    file_meta: FileMeta | None = None
    attribution: AttributionResult | None = None

    # Per-file ancestry→GSM mapping (ancestry path extracted, no inference)
    per_file_gsm_id: dict[str, str | None] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# DemuxRule — rules for splitting merged multi-sample files
# ---------------------------------------------------------------------------

class DemuxRule(BaseModel):
    """Splitting rules for a merged file containing multiple samples."""

    method: str = ""
    # "column_split"  — column headers contain sample identifiers
    # "row_split"     — a column value directly identifies the sample
    # "barcode_split" — identifiers appear anywhere in barcode/row-name strings

    identifier_pattern: str = ""
    # regex to extract sample identifiers (re.search, any position)
    # e.g. "GSM\\d+", "Sample[A-Z]\\d*", "donor_\\w+__treatment_\\w+"

    value_column: str = ""
    # row_split: column name whose value indicates sample ownership

    barcode_column: str = ""
    # barcode_split: column name containing barcodes with embedded identifiers

    @field_validator("method")
    @classmethod
    def _validate_method(cls, v: str) -> str:
        allowed = {"column_split", "row_split", "barcode_split", ""}
        if v not in allowed:
            raise ValueError(f"Invalid demux method '{v}'. Allowed: {allowed}")
        return v


# ---------------------------------------------------------------------------
# Verification — quality checks for LLM-generated outputs
# ---------------------------------------------------------------------------

class VerificationCheck(BaseModel):
    """A single verification check result."""

    type: str = ""
    severity: str = ""  # "warning" | "error"
    detail: str = ""


class VerificationReport(BaseModel):
    """Result of running verification checks on an LLM output."""

    passed: bool = False
    checks: list[VerificationCheck] = Field(default_factory=list)
    repair_attempts: int = 0
    fallback: bool = False

    def failure_summary(self) -> str:
        errors = [c for c in self.checks if c.severity == "error"]
        return "\n".join(f"[{c.type}] {c.detail}" for c in errors)


# ---------------------------------------------------------------------------
# L14 — Compact classification output
# ---------------------------------------------------------------------------

class ModalityDecision(BaseModel):
    """Compact modality classification for a group of GSM."""

    modality: str
    confidence: float = 1.0
    file_pattern: str = ""                 # e.g. "10x triplet (mtx+barcodes+features)"
    has_shared: bool = False               # 是否有跨样本共享文件
    has_demux: bool = False                # 是否有需要拆分的合并文件
    demux_rule: DemuxRule | None = None    # 拆分规则 (has_demux=true 时填充)
    demux_content_preview: dict | None = None  # 文件内容预览 (demux LLM code-gen 用)
    files: list[dict] = Field(default_factory=list)
                                           # [{ancestry, source_path, role}]


class CompactSampleGroup(BaseModel):
    """Compact sample group covering multiple GSM with same classification."""

    gsm_ids: list[str] = Field(default_factory=list)
    note: str = ""
    sample_key: str = ""                   # 归属 key（GSM ID 或推断复合 key）
    key_source: str = ""                   # "gsm_id" | "inferred_composite"
    file_prefix: str = ""                  # Scenario d: filename prefix for file match
    modalities: dict[str, ModalityDecision] = Field(default_factory=dict)


class CompactClassificationResult(BaseModel):
    """L14: Compact classifier output (GSM-level deduplicated)."""

    sample_groups: list[CompactSampleGroup] = Field(default_factory=list)
    shared_file_groups: list[dict] = Field(default_factory=list)
    unclassified: list[dict] = Field(default_factory=list)
    reasoning: str = ""
    verification: VerificationReport | None = None
    repair_attempts: int = 0
    fallback: bool = False


class SharedFileGroup(BaseModel):
    """A file shared across multiple sample groups (e.g. common features.tsv.gz)."""

    ancestry: str = ""
    source_path: str = ""
    role: str = ""
    modality: str = ""
    ref_by_gsm: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Context report (compact, token-efficient)
# ---------------------------------------------------------------------------

_GSM_THRESHOLD = 5

class FileContextReport(BaseModel):
    """Aggregated context for all files in a GSE — compact for LLM efficiency."""

    gse_id: str = ""
    gse_info: dict[str, Any] = Field(default_factory=dict)
    experiment_flowchart: str = ""
    total_files: int = 0
    total_gsm_count: int = 0
    errors: list[str] = Field(default_factory=list)

    # L1: Compact metadata (primary; large GSE)
    field_value_maps: dict[str, FieldValueMap] = Field(default_factory=dict)

    # L2+L3: Compact file signals with content previews
    file_signal_groups: list[FileSignalGroup] = Field(default_factory=list)

    # Fallback: full per-GSM entries (only when GSM ≤ threshold)
    gsm_entries: list[dict[str, Any]] = Field(default_factory=list)

    # Fallback: full per-file signals (only when files ≤ threshold or unique patterns)
    per_file_signals: list[FileSignals] = Field(default_factory=list)

    # Raw file summaries kept for organize_tool (not sent to LLM)
    file_summaries: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @property
    def use_compact_format(self) -> bool:
        return self.total_gsm_count > _GSM_THRESHOLD


# ---------------------------------------------------------------------------
# Evidence reports
# ---------------------------------------------------------------------------

class EvidenceReport(BaseModel):
    """Evidence gathered from PubMed papers to resolve modality ambiguity."""

    source: str = "none"
    pmids: list[str] = Field(default_factory=list)
    findings: str = ""
    excerpts: list[str] = Field(default_factory=list)
    confidence_impact: str = ""


# ---------------------------------------------------------------------------
# Full file-level classification (kept for organize_tool compatibility)
# ---------------------------------------------------------------------------

class FileEntry(BaseModel):
    """A single file assigned to a modality."""

    source_path: str = ""
    ancestry: str = ""
    role: str = ""
    signals: dict[str, Any] = Field(default_factory=dict)
    reader_code: str = ""               # LLM-generated reader code (initially empty)


class ModalityGroup(BaseModel):
    """A modality group within a sample (full file list)."""

    modality: str
    confidence: float = 1.0
    files: list[FileEntry] = Field(default_factory=list)


class SampleGroup(BaseModel):
    """A sample with classified modalities (full file list)."""

    label: str
    gsm_ids: list[str] = Field(default_factory=list)
    note: str = ""
    modalities: dict[str, ModalityGroup] = Field(default_factory=dict)


class UnclassifiedFile(BaseModel):
    """A file that could not be classified."""

    source_path: str = ""
    ancestry: str = ""
    reason: str = ""


class ClassificationResult(BaseModel):
    """Full classification output (backward-compatible)."""

    sample_groups: list[SampleGroup] = Field(default_factory=list)
    unclassified: list[UnclassifiedFile] = Field(default_factory=list)
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Preprocessing result (top-level output)
# ---------------------------------------------------------------------------

class PreprocessingResult(BaseModel):
    """Final result of the preprocessing pipeline."""

    gse_id: str = ""
    output_dir: str = ""
    sample_groups: list[SampleGroup] = Field(default_factory=list)
    unclassified: list[UnclassifiedFile] = Field(default_factory=list)
    evidence: EvidenceReport | None = None
    llm_call_count: int = 0
    cache_hit: bool = False
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Storage specification models (plan-omics-storage-spec.md)
# ---------------------------------------------------------------------------

class ModalityFileEntry(BaseModel):
    """A file entry in a modality manifest."""

    path: str = ""
    role: str = ""   # matrix / barcodes / features / peaks / fragments / contigs / clonotypes / spatial_positions / spatial_image / guide_info / other
    format: str = ""  # h5ad / csv / bed / tsv.gz / png / json


class ModalityManifest(BaseModel):
    """Manifest for a single modality within a sample group."""

    assay: str = ""
    suspension: str = ""    # cell / nucleus / na
    n_cells: int | None = None
    n_features: int | None = None
    files: list[ModalityFileEntry] = Field(default_factory=list)
    source_files: list[ModalityFileEntry] = Field(default_factory=list)


class GroupMetadata(BaseModel):
    """Metadata for a sample group (one or more GSM)."""

    gse_id: str = ""
    group_label: str = ""
    gsm_ids: list[str] = Field(default_factory=list)
    sample_metadata: dict = Field(default_factory=dict)
    modalities: dict[str, ModalityManifest] = Field(default_factory=dict)
