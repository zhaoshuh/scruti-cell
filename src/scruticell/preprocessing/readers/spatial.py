"""Spatial transcriptomics reader: loads Visium spatial files.

Reads tissue_positions_list.csv and scalefactors_json.json following
the 10x Visium / squidpy / scanpy conventions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# 10x Visium tissue_positions_list.csv columns (no header in v1, header in v2)
_POSITION_COLS_V1 = [
    "barcode", "in_tissue", "array_row", "array_col",
    "pxl_row_in_fullres", "pxl_col_in_fullres",
]
_POSITION_DTYPES = {
    "barcode": str, "in_tissue": int,
    "array_row": int, "array_col": int,
    "pxl_row_in_fullres": float, "pxl_col_in_fullres": float,
}


def read_tissue_positions(filepath: str) -> pd.DataFrame | None:
    """Read tissue_positions_list.csv from Visium spaceranger output.

    Args:
        filepath: Path to tissue_positions_list.csv

    Returns:
        DataFrame with columns:
          barcode, in_tissue, array_row, array_col,
          pxl_row_in_fullres, pxl_col_in_fullres
    """
    p = Path(filepath)
    if not p.exists():
        return None

    # Detect header: first row starts with "barcode" → has header
    first_line = pd.read_csv(filepath, nrows=0, header=None).columns.tolist()
    if first_line and str(first_line[0]).lower() == "barcode":
        df = pd.read_csv(filepath)
    else:
        df = pd.read_csv(filepath, names=_POSITION_COLS_V1, header=None)

    for col, dtype in _POSITION_DTYPES.items():
        if col in df.columns:
            df[col] = df[col].astype(dtype)

    return df


def read_scalefactors(filepath: str) -> dict | None:
    """Read scalefactors_json.json from Visium spaceranger output.

    Args:
        filepath: Path to scalefactors_json.json

    Returns:
        Dict with keys:
          spot_diameter_fullres, tissue_hires_scalef,
          tissue_lowres_scalef, fiducial_diameter_fullres
    """
    p = Path(filepath)
    if not p.exists():
        return None

    with open(p, encoding="utf-8") as f:
        return json.load(f)


def find_spatial_files(spatial_dir: str) -> dict:
    """Scan a spatial output directory for Visium files.

    Args:
        spatial_dir: Path to spatial modality directory

    Returns:
        Dict with keys: tissue_positions, scalefactors, image_hires, image_lowres
        Each value is the file path if found, else None.
    """
    p = Path(spatial_dir)
    result = {
        "tissue_positions": None,
        "scalefactors": None,
        "image_hires": None,
        "image_lowres": None,
    }

    for f in p.rglob("*"):
        if not f.is_file():
            continue
        name = f.name.lower()
        if "tissue_positions" in name and name.endswith(".csv"):
            result["tissue_positions"] = str(f)
        elif "scalefactor" in name and name.endswith(".json"):
            result["scalefactors"] = str(f)
        elif "hires" in name and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".tiff", ".tif"):
            result["image_hires"] = str(f)
        elif "lowres" in name and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".tiff", ".tif"):
            result["image_lowres"] = str(f)

    return result
