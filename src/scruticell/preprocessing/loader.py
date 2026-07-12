"""Python loader for preprocessed omics data.

Implements the loading API defined in plan-omics-storage-spec.md.
Provides `load()`, `load_modality()`, and `list_groups()` for
convenient access to preprocessed sample groups.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Try optional imports — fail gracefully if not needed
try:
    import scanpy as sc
except ImportError:
    sc = None

try:
    from PIL import Image
except ImportError:
    Image = None


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

class ModalityData:
    """Lazy container for a single modality's files.

    Attributes are populated based on what files exist in the directory.
    Missing files → attribute is None.
    """

    def __init__(self, modality: str, assay: str = "",
                 n_cells: int | None = None, n_features: int | None = None):
        self.modality = modality
        self.assay = assay
        self.n_cells = n_cells
        self.n_features = n_features

        # AnnData files
        self.matrix: Any = None          # scRNA / snRNA / ATAC / Spatial / Methylation / Perturb / Proteomics
        self.rna_matrix: Any = None      # CITE-seq RNA / Multiome RNA
        self.adta_matrix: Any = None     # CITE-seq ADT
        self.atac_matrix: Any = None     # Multiome ATAC

        # DataFrames
        self.peaks: pd.DataFrame | None = None
        self.fragments: pd.DataFrame | None = None
        self.positions: pd.DataFrame | None = None
        self.contigs: pd.DataFrame | None = None
        self.clonotypes: pd.DataFrame | None = None
        self.guide_info: pd.DataFrame | None = None

        # Images
        self.image_hires: Any = None
        self.image_lowres: Any = None
        self.scalefactors: dict | None = None

    def __repr__(self) -> str:
        has = [k for k, v in self.__dict__.items()
               if v is not None and not k.startswith("_") and k not in ("modality", "assay", "n_cells", "n_features")]
        return f"ModalityData(modality={self.modality!r}, assay={self.assay!r}, files={has})"


class SampleGroup:
    """A sample group containing one or more modalities."""

    def __init__(self, label: str, gsm_ids: list[str],
                 sample_metadata: dict, metadata: dict):
        self.label = label
        self.gsm_ids = gsm_ids
        self.sample_metadata = sample_metadata
        self.metadata = metadata

    @property
    def modalities(self) -> list[str]:
        return list(self.metadata.get("modalities", {}).keys())

    def __repr__(self) -> str:
        return f"SampleGroup(label={self.label!r}, modalities={self.modalities})"


# ---------------------------------------------------------------------------
# File readers
# ---------------------------------------------------------------------------

def _read_h5ad_safe(path: str) -> Any:
    if sc is None:
        raise ImportError("scanpy is required to read .h5ad files. pip install scanpy")
    if not Path(path).exists():
        return None
    return sc.read_h5ad(path)


def _read_csv_safe(path: str, **kwargs) -> pd.DataFrame | None:
    p = Path(path)
    if not p.exists():
        return None
    return pd.read_csv(p, **kwargs)


def _read_image_safe(path: str) -> Any | None:
    if Image is None:
        raise ImportError("Pillow is required to read images. pip install Pillow")
    p = Path(path)
    if not p.exists():
        return None
    return Image.open(p)


# ---------------------------------------------------------------------------
# Modality loading
# ---------------------------------------------------------------------------

def load_modality(path: str) -> ModalityData:
    """Load a single modality directory.

    Reads the files.json (or scans the directory) and populates
    a ModalityData object.

    Args:
        path: Path to a modality subdirectory
              (e.g. "output/GSE215353/preprocessed/group_GSM001/scrna_seq/")

    Returns:
        ModalityData with all available files loaded.
    """
    mod_dir = Path(path)
    if not mod_dir.is_dir():
        raise FileNotFoundError(f"Modality directory not found: {path}")

    # Try to read files.json
    files_json = mod_dir / "files.json"
    if files_json.exists():
        with open(files_json, encoding="utf-8") as f:
            file_list = json.load(f)
    else:
        file_list = [{"path": f.name, "role": "unknown", "format": f.suffix.lstrip(".")}
                     for f in mod_dir.iterdir() if f.is_file()]

    data = ModalityData(modality=mod_dir.name)

    for entry in file_list:
        fpath = str(mod_dir / entry.get("path", ""))
        role = entry.get("role", "")
        fmt = entry.get("format", "")

        if fmt == "h5ad" and role in ("matrix", ""):
            adata = _read_h5ad_safe(fpath)
            if role == "matrix":
                data.matrix = adata
            elif "rna" in Path(fpath).stem.lower():
                data.rna_matrix = adata
            elif "atac" in Path(fpath).stem.lower():
                data.atac_matrix = adata
            elif "adta" in Path(fpath).stem.lower():
                data.adta_matrix = adata
            else:
                data.matrix = adata

        elif role == "peaks" and fmt == "bed":
            data.peaks = _read_csv_safe(fpath, sep="\t")

        elif role == "fragments":
            data.fragments = _read_csv_safe(
                fpath, compression="gzip" if fpath.endswith(".gz") else None
            )

        elif role == "spatial_positions":
            data.positions = _read_csv_safe(fpath)

        elif role == "spatial_image":
            if "hires" in Path(fpath).stem.lower() or "high" in Path(fpath).stem.lower():
                data.image_hires = _read_image_safe(fpath)
            elif "lowres" in Path(fpath).stem.lower() or "low" in Path(fpath).stem.lower():
                data.image_lowres = _read_image_safe(fpath)

        elif role == "contigs":
            data.contigs = _read_csv_safe(fpath)

        elif role == "clonotypes":
            data.clonotypes = _read_csv_safe(fpath)

        elif role == "guide_info":
            data.guide_info = _read_csv_safe(fpath)

        elif fmt == "json" and "scalefactor" in Path(fpath).stem.lower():
            if Path(fpath).exists():
                with open(fpath, encoding="utf-8") as f:
                    data.scalefactors = json.load(f)

        elif fmt == "png":
            img = _read_image_safe(fpath)
            if "hires" in Path(fpath).stem.lower() or "high" in Path(fpath).stem.lower():
                data.image_hires = img
            elif "lowres" in Path(fpath).stem.lower() or "low" in Path(fpath).stem.lower():
                data.image_lowres = img

    return data


# ---------------------------------------------------------------------------
# Sample group loading
# ---------------------------------------------------------------------------

def load(path: str) -> SampleGroup:
    """Load a preprocessed sample group.

    Reads metadata.json and loads all available modalities.

    Args:
        path: Path to a sample group directory
              (e.g. "output/GSE215353/preprocessed/group_GSM001/")

    Returns:
        SampleGroup with all modalities accessible as attributes.
    """
    group_dir = Path(path)
    if not group_dir.is_dir():
        raise FileNotFoundError(f"Group directory not found: {path}")

    meta_path = group_dir / "metadata.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"metadata.json not found in {path}")

    with open(meta_path, encoding="utf-8") as f:
        metadata = json.load(f)

    group = SampleGroup(
        label=metadata.get("group_label", group_dir.name),
        gsm_ids=metadata.get("gsm_ids", []),
        sample_metadata=metadata.get("sample_metadata", {}),
        metadata=metadata,
    )

    for mod_name, mod_info in metadata.get("modalities", {}).items():
        mod_dir = group_dir / mod_name
        if not mod_dir.is_dir():
            logger.debug("Modality directory not found: %s", mod_dir)
            continue

        try:
            mod_data = load_modality(str(mod_dir))
            mod_data.assay = mod_info.get("assay", "")
            mod_data.n_cells = mod_info.get("n_cells")
            mod_data.n_features = mod_info.get("n_features")
            setattr(group, mod_name, mod_data)
        except Exception as e:
            logger.warning("Failed to load modality %s: %s", mod_name, e)

    return group


# ---------------------------------------------------------------------------
# Bulk scanning
# ---------------------------------------------------------------------------

def list_groups(path: str) -> list[dict]:
    """List all sample groups in a preprocessed output directory.

    Args:
        path: Path to the preprocessed directory
              (e.g. "output/GSE215353/preprocessed/")

    Returns:
        List of dicts with label, modalities, and gsm_ids for each group.
    """
    root = Path(path)
    if not root.is_dir():
        raise FileNotFoundError(f"Directory not found: {path}")

    groups: list[dict] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("group_"):
            continue
        meta_path = entry / "metadata.json"
        if not meta_path.exists():
            continue

        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            groups.append({
                "label": meta.get("group_label", entry.name),
                "gsm_ids": meta.get("gsm_ids", []),
                "modalities": list(meta.get("modalities", {}).keys()),
                "path": str(entry),
            })
        except Exception as e:
            logger.warning("Failed to read %s: %s", meta_path, e)

    return groups


# ---------------------------------------------------------------------------
# Reader code loading (reads files using LLM-generated reader_code)
# ---------------------------------------------------------------------------

def read_with_skill(
    filepath: str,
    content_preview: dict | None = None,
    modality: str = "",
    file_role: str = "",
    reader_code: str = "",
    force_regenerate: bool = False,
) -> dict | None:
    """Read a file using its LLM-generated reader code.

    If reader_code is provided, executes it directly.
    Otherwise, generates reader_code via LLM, caches it in reader_code,
    and executes it.

    Args:
        filepath: Path to the file to read
        content_preview: ContentPreview dict (required if reader_code is empty)
        modality: Modality label (required if generating code)
        file_role: File role (required if generating code)
        reader_code: Existing LLM-generated reader code string. If empty,
                    generates new code.
        force_regenerate: If True, regenerates even if reader_code exists

    Returns:
        Execution result dict from the reader function, or None on failure.
    """
    from pathlib import Path

    if reader_code and not force_regenerate:
        code = reader_code
    else:
        from scruticell.preprocessing.readers.codegen import generate_reader_code

        cp = content_preview or {}
        code, result, err = generate_reader_code(
            cp, modality, file_role, str(Path(filepath)),
        )
        if err:
            logger.error("Reader code generation failed for %s: %s", filepath, err)
            return None
        if result is not None:
            return result
        # Code generated but sandbox execution happened inside generate_reader_code already
        # If code exists but no result, execute it now
        if not code:
            return None

    # Execute existing code
    from scruticell.preprocessing.readers.codegen import _execute_sandbox
    file_role_clean = file_role or _infer_role_from_path(filepath)
    ok, result, err = _execute_sandbox(code, str(Path(filepath)), file_role_clean)
    if not ok:
        logger.error("Reader code execution failed for %s: %s", filepath, err)
        return None

    return result


def _infer_role_from_path(filepath: str) -> str:
    """Infer file role from filename if not provided."""
    name = Path(filepath).name.lower()
    if "barcodes" in name:
        return "barcodes"
    if "features" in name or "genes" in name:
        return "features"
    if "matrix" in name or name.endswith((".mtx", ".mtx.gz", ".h5ad", ".h5", ".csv")):
        return "matrix"
    if "peaks" in name or name.endswith(".bed"):
        return "peaks"
    if "fragments" in name:
        return "fragments"
    if "contig" in name:
        return "contigs"
    if "clonotype" in name:
        return "clonotypes"
    if "tissue_positions" in name:
        return "spatial_positions"
    return "other"
