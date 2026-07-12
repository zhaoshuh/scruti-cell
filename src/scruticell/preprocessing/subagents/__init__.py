"""Sub-agent definitions for the omics preprocessing pipeline."""

from scruticell.preprocessing.subagents.file_prep import create_file_prep_subagent
from scruticell.preprocessing.subagents.classifier import create_classifier_subagent
from scruticell.preprocessing.subagents.evidence import create_evidence_subagent

__all__ = [
    "create_file_prep_subagent",
    "create_classifier_subagent",
    "create_evidence_subagent",
]
