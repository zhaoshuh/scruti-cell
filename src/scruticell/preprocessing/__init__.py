"""Omics Preprocessing Module for Scrut-Cell.

Provides GSE→sample×modality preprocessing using a deepagents-based
orchestrator with sub-agents for file preparation, evidence gathering,
and modality classification.

Storage specification: .opencode/plans/plan-omics-storage-spec.md
Reader code system: skills/file_reader/SKILL.md

Usage:
    import scruticell as scell
    group = scell.load("output/GSE215353/preprocessed/group_GSM001/")
    group.scrna_seq.matrix  # AnnData

    data = scell.read_with_skill("file.csv", content_preview={...},
                                  modality="scrna_seq", file_role="features")
"""

from scruticell.preprocessing.agent import preprocess_gse, create_preprocessing_agent
from scruticell.preprocessing.loader import load, load_modality, list_groups, read_with_skill
from scruticell.preprocessing.models import (
    AttributionResult,
    ClassificationResult,
    CompactClassificationResult,
    CompactSampleGroup,
    EvidenceReport,
    FieldValueMap,
    FileContentPreview,
    FileContextReport,
    FileSignalGroup,
    GroupMetadata,
    ModalityDecision,
    ModalityFileEntry,
    ModalityGroup,
    ModalityManifest,
    PreprocessingResult,
    SampleGroup,
    SampleKey,
    SharedFileGroup,
    ValueGroup,
)
from scruticell.preprocessing.readers.codegen import generate_reader_code

__all__ = [
    "preprocess_gse",
    "create_preprocessing_agent",
    "load",
    "load_modality",
    "list_groups",
    "read_with_skill",
    "generate_reader_code",
    "PreprocessingResult",
    "ClassificationResult",
    "CompactClassificationResult",
    "CompactSampleGroup",
    "ModalityDecision",
    "EvidenceReport",
    "FileContextReport",
    "FileContentPreview",
    "FieldValueMap",
    "FileSignalGroup",
    "SampleGroup",
    "ModalityGroup",
    "ValueGroup",
    "GroupMetadata",
    "ModalityManifest",
    "ModalityFileEntry",
    "AttributionResult",
    "SampleKey",
    "SharedFileGroup",
]
