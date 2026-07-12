"""Context extraction layer — produces structured observations, not decisions."""

from scruticell.preprocessing.context.metadata import extract_metadata_signals
from scruticell.preprocessing.context.filenames import extract_filename_signals
from scruticell.preprocessing.context.contents import extract_content_signals
from scruticell.preprocessing.context.field_mapper import build_field_value_maps
from scruticell.preprocessing.context.signal_grouper import group_file_signals
from scruticell.preprocessing.context.summary_grouper import build_content_preview, compute_shape_range
from scruticell.preprocessing.context.semantic import extract_filename_meta, extract_column_meta, tokenize, match_token
from scruticell.preprocessing.context.file_meta import build_file_meta
from scruticell.preprocessing.context.attribution import compute_attribution, build_soft_suppl_refs
from scruticell.preprocessing.context.extraction_context import build_extraction_context

__all__ = [
    "extract_metadata_signals",
    "extract_filename_signals",
    "extract_content_signals",
    "build_field_value_maps",
    "group_file_signals",
    "build_content_preview",
    "compute_shape_range",
    "extract_filename_meta",
    "extract_column_meta",
    "tokenize",
    "match_token",
    "build_file_meta",
    "compute_attribution",
    "build_soft_suppl_refs",
    "build_extraction_context",
]
