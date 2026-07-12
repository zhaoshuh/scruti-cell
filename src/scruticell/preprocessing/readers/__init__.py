"""Specialized omics format readers.

Each module handles a specific modality's native file format,
following community best practices.
"""

from scruticell.preprocessing.readers.methylation import (
    read_methylation_cov,
    merge_methylation_samples,
)
from scruticell.preprocessing.readers.spatial import (
    read_tissue_positions,
    read_scalefactors,
    find_spatial_files,
)
from scruticell.preprocessing.readers.atac import (
    read_peaks,
    read_fragments,
    build_peak_name,
)
from scruticell.preprocessing.readers.tcr import (
    read_contig_annotations,
    read_clonotypes,
    detect_chain_type,
    get_productive_cells,
)
from scruticell.preprocessing.readers.codegen import (
    generate_reader_code,
)

__all__ = [
    "read_methylation_cov",
    "merge_methylation_samples",
    "read_tissue_positions",
    "read_scalefactors",
    "find_spatial_files",
    "read_peaks",
    "read_fragments",
    "build_peak_name",
    "read_contig_annotations",
    "read_clonotypes",
    "detect_chain_type",
    "get_productive_cells",
    "generate_reader_code",
]
