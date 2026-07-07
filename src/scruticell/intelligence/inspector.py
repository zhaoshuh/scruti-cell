"""File content inspector: extracts structured summaries for all file types.

Generates a minimal but informative fingerprint for each file:
- Text files (.csv, .tsv, .txt): first N rows, column names, row/col counts
- HDF5 files (.h5, .h5ad): obs/var columns, matrix shape, sparsity, uns keys
- RDS files (.rds, .RData): DataFrame keys, shapes
- Loom files (.loom): matrix shape, row/col attr names
- Other: file size, extension, note as opaque
"""

from __future__ import annotations

import csv
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PREVIEW_ROWS = 30
_PREVIEW_COLS = 30
_PREVIEW_MAX_CHARS = 2000
_MAX_INSPECTION_CELLS = 500_000  # load full matrix only if below this
_MAX_BARCODE_SAMPLE = 100

_CSV_EXTS = {".csv", ".csv.gz", ".tsv", ".tsv.gz", ".tab", ".txt", ".txt.gz"}
_H5_EXTS = {".h5", ".h5ad"}
_RDS_EXTS = {".rds", ".rdata", ".RDS", ".RData", ".Rda", ".rda"}
_LOOM_EXTS = {".loom"}
_MTX_EXTS = {".mtx", ".mtx.gz"}
_SOFT_EXTS = {".soft", ".soft.gz"}


@dataclass
class FileSummary:
    """Structured summary of a single file."""

    filename: str
    ancestry: str  # decompression path lineage
    extension: str
    size_bytes: int
    file_type: str = "unknown"  # "csv", "h5", "rds", "loom", "mtx", "soft", "other"

    # CSV/TSV/text
    n_rows: int | None = None
    n_cols: int | None = None
    columns: list[str] = field(default_factory=list)
    preview_rows: list[dict[str, str]] = field(default_factory=list)
    preview_rows_raw: list[list[str]] = field(default_factory=list)
    column_categories: dict[str, list[str]] = field(default_factory=dict)
    delimiter: str = ""

    # Reader code (LLM-generated, for reading matrix correctly)
    reader_code: str = ""
    reader_path: str = ""
    reader_error: str = ""

    # H5/H5AD
    h5_shape: tuple[int, int] | None = None
    h5_sparse: bool | None = None
    obs_columns: list[str] = field(default_factory=list)
    var_columns: list[str] = field(default_factory=list)
    uns_keys: list[str] = field(default_factory=list)
    raw_available: bool | None = None

    # Barcode analysis (extracted from obs index or barcode column)
    barcode_samples: list[str] = field(default_factory=list)
    barcode_format: str = ""
    barcode_prefixes: list[str] = field(default_factory=list)
    barcode_prefix_counts: dict[str, int] = field(default_factory=dict)

    # RDS
    rds_keys: list[str] = field(default_factory=list)
    rds_shapes: dict[str, tuple[int, int]] = field(default_factory=dict)

    # Loom
    loom_shape: tuple[int, int] | None = None
    loom_row_attrs: list[str] = field(default_factory=list)
    loom_col_attrs: list[str] = field(default_factory=list)

    # MTX
    mtx_shape: tuple[int, int] | None = None
    mtx_is_sparse: bool | None = None

    # SOFT
    soft_sections: list[str] = field(default_factory=list)
    soft_gsm_count: int | None = None
    soft_gpl_count: int | None = None

    # Fallback
    error: str = ""


class Inspector:
    """Inspect files and produce structured summaries.

    Usage:
        inspector = Inspector()
        summary = inspector.inspect_file(path, ancestry)
    """

    def inspect_file(self, filepath: Path, ancestry: str = "") -> FileSummary:
        """Generate a FileSummary for a single file."""
        summary = FileSummary(
            filename=filepath.name,
            ancestry=ancestry or filepath.name,
            extension=filepath.suffix.lower(),
            size_bytes=filepath.stat().st_size if filepath.exists() else 0,
        )

        name_lower = filepath.name.lower()

        try:
            if filepath.is_dir():
                summary.file_type = "directory"
                return summary

            if self._is_csv(name_lower):
                self._inspect_csv(filepath, summary)
            elif self._is_h5(name_lower):
                self._inspect_h5(filepath, summary)
            elif self._is_rds(name_lower):
                self._inspect_rds(filepath, summary)
            elif self._is_loom(name_lower):
                self._inspect_loom(filepath, summary)
            elif self._is_mtx(name_lower):
                self._inspect_mtx(filepath, summary)
            elif self._is_soft(name_lower):
                self._inspect_soft(filepath, summary)
            elif self._is_text(name_lower):
                self._inspect_csv(filepath, summary)
            else:
                summary.file_type = "other"
        except Exception as exc:
            summary.error = str(exc)
            logger.warning("Inspection failed for %s: %s", filepath, exc)

        return summary

    def _is_csv(self, name: str) -> bool:
        return any(name.endswith(ext) for ext in _CSV_EXTS)

    def _is_h5(self, name: str) -> bool:
        return any(name.endswith(ext) for ext in _H5_EXTS)

    def _is_rds(self, name: str) -> bool:
        return any(name.endswith(ext) for ext in _RDS_EXTS)

    def _is_loom(self, name: str) -> bool:
        return any(name.endswith(ext) for ext in _LOOM_EXTS)

    def _is_mtx(self, name: str) -> bool:
        return any(name.endswith(ext) for ext in _MTX_EXTS)

    def _is_soft(self, name: str) -> bool:
        return any(name.endswith(ext) for ext in _SOFT_EXTS)

    def _is_text(self, name: str) -> bool:
        return name.endswith(".txt") or name.endswith(".txt.gz") or name.endswith(".json")

    def _open_text(self, filepath: Path) -> tuple[Any, str]:
        """Open a file as text, handling gzip."""
        if filepath.name.endswith(".gz"):
            import gzip
            return gzip.open(filepath, "rt", encoding="utf-8", errors="replace"), "gzip"
        return open(filepath, encoding="utf-8", errors="replace"), "plain"

    def _inspect_csv(self, filepath: Path, summary: FileSummary) -> None:
        """Inspect a delimited text file.

        Handles both regular CSV (header row) and headerless files
        (10X barcodes.tsv, features.tsv) by detecting whether the
        first row looks like data or column names.
        """
        summary.file_type = "csv"
        f, _ = self._open_text(filepath)

        try:
            sample = f.read(4096)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample[:1024])
            summary.delimiter = dialect.delimiter
        except Exception:
            if "\t" in sample:
                summary.delimiter = "\t"
            else:
                summary.delimiter = ","

        reader = csv.reader(f, delimiter=summary.delimiter)
        all_rows: list[list[str]] = []
        try:
            for i, row in enumerate(reader):
                if i >= _PREVIEW_ROWS + 1:  # +1 for header detection
                    break
                all_rows.append(row)
        except Exception:
            pass
        finally:
            f.close()

        if not all_rows:
            return

        summary.n_cols = len(all_rows[0])

        # Save raw preview (lenient, string-only, for LLM reader code generation)
        summary.preview_rows_raw = [
            row[:_PREVIEW_COLS] for row in all_rows
        ]

        # Truncate columns to preview limit
        def _col(i: int) -> str:
            return all_rows[0][i] if i < len(all_rows[0]) else f"col_{i}"

        # Detect if file has a header row
        has_header = self._detect_has_header(all_rows)

        if has_header:
            cols = all_rows[0][:_PREVIEW_COLS]
            summary.columns = cols
            summary.preview_rows = [
                {_col(j): str(r[j]) for j in range(min(len(r), _PREVIEW_COLS))}
                for r in all_rows[1:_PREVIEW_ROWS + 1]
            ]
        else:
            # Headerless: use column_0, column_1, etc.
            summary.columns = [f"col_{j}" for j in range(min(len(all_rows[0]), _PREVIEW_COLS))]
            summary.preview_rows = [
                {f"col_{j}": str(v) for j, v in enumerate(r)}
                if len(r) <= _PREVIEW_COLS
                else {f"col_{j}": str(v) for j, v in enumerate(r[:_PREVIEW_COLS])}
                for r in all_rows[:_PREVIEW_ROWS]
            ]

        # Barcode analysis on matrix-like CSVs
        if (summary.n_rows or 0) > 10 and (summary.n_cols or 0) > 10:
            # Heuristic: if first column looks like barcodes, or any column looks like gene IDs
            self._analyze_csv_barcodes(all_rows, summary)

        # Extract column categories for potential metadata files (small, categorical)
        if has_header and len(all_rows) >= 2 and len(all_rows) <= 1000:
            categories: dict[str, list[str]] = {}
            cols = summary.columns or [f"col_{j}" for j in range(len(all_rows[0]))]
            for j, col_name in enumerate(cols):
                vals: list[str] = []
                for r in all_rows[1:]:
                    if j < len(r) and r[j].strip():
                        vals.append(r[j].strip())
                unique = sorted(set(vals))[:50]
                if 1 < len(unique) <= 30:  # Categorical: 2-30 unique values
                    categories[col_name] = unique
            if categories:
                summary.column_categories = categories

        # Count total rows
        try:
            f2, _ = self._open_text(filepath)
            total = sum(1 for _ in f2)
            f2.close()
            if has_header:
                summary.n_rows = max(0, total - 1)
            else:
                summary.n_rows = total
        except Exception:
            pass

    def _analyze_csv_barcodes(
        self, rows: list[list[str]], summary: FileSummary,
    ) -> None:
        """Try barcode analysis on CSV data if it looks like a matrix."""
        if not rows or not rows[0]:
            return
        # Check first column for barcode-like patterns
        first_col = [r[0] for r in rows if r]
        if not first_col:
            return
        barcode_hits = sum(
            1 for v in first_col
            if len(v) >= 14 and all(c in "ACGTacgt-" for c in v)
        )
        if barcode_hits >= len(first_col) * 0.6:
            self._analyze_barcodes(first_col, summary)
            return
        # Check first row (if headerless) for barcode-like patterns
        if len(first_col) == 1 and len(rows[0]) >= 10:
            barcode_hits = sum(
                1 for v in rows[0]
                if len(v) >= 14 and all(c in "ACGTacgt-" for c in v)
            )
            if barcode_hits >= len(rows[0]) * 0.6:
                self._analyze_barcodes(rows[0], summary)

    def _detect_has_header(self, rows: list[list[str]]) -> bool:
        """Detect whether the first row is a header or data row.

        Returns False if the first row looks like data (barcodes,
        gene IDs, counts), True if it looks like column names.
        """
        if len(rows) < 2:
            return True  # Single row - assume header

        first = [v.strip() for v in rows[0]]
        second = [v.strip() for v in rows[1]] if len(rows) > 1 else []

        # 1. Single column: almost certainly headerless (barcode file)
        if len(first) <= 1:
            return False

        # 2. Barcode patterns: AAACCCA... with -1 suffix
        barcode_like = sum(
            1 for v in first
            if len(v) >= 14 and all(c in "ACGTacgt-" for c in v)
        )
        if barcode_like >= len(first) * 0.7:
            return False

        # 3. Gene IDs: ENSG... patterns in ANY column of first row = features file
        any_ensg = any(v.startswith("ENS") for v in first)
        all_digit = all(v.isdigit() for v in first if v)
        if any_ensg or all_digit:
            return False

        # 4. All numeric first row suggests data, not header
        numeric_first = sum(1 for v in first if self._is_numeric(v))
        if numeric_first >= len(first) * 0.8:
            return False

        # 5. Type mismatch: first row words, second row numbers = header
        word_count_first = sum(1 for v in first if v and not self._is_numeric(v))
        numeric_count_second = sum(1 for v in second if self._is_numeric(v))
        if word_count_first >= 1 and numeric_count_second >= 1:
            return True

        return True  # Default: assume header

    def _is_numeric(self, s: str) -> bool:
        """Check if a string is a number (int or float)."""
        try:
            float(s)
            return True
        except (ValueError, TypeError):
            return False

    def re_read_with_reader(
        self, filepath: Path, reader_code: str, nrows: int | None = 30,
    ) -> dict[str, Any] | None:
        """Execute LLM-generated reader code to get accurate preview / full data.

        Args:
            filepath: Path to the matrix file.
            reader_code: Python code containing def read_matrix(filepath, nrows=None):.
            nrows: Max rows to read (None = full).

        Returns:
            Dict from read_matrix() or None on failure.
        """
        local_ns: dict[str, Any] = {}
        try:
            exec(reader_code, {"np": __import__("numpy"), "pd": __import__("pandas"),
                               "sc": __import__("scanpy"),
                               "csv": __import__("csv"), "gzip": __import__("gzip")}, local_ns)
            read_fn = local_ns.get("read_matrix")
            if read_fn is None:
                return None
            result = read_fn(str(filepath), nrows)
            if not isinstance(result, dict):
                return None
            return result
        except Exception as exc:
            logger.warning("Reader code execution failed for %s: %s", filepath.name, exc)
            return None

    def _inspect_h5(self, filepath: Path, summary: FileSummary) -> None:
        """Inspect an HDF5 file (.h5 or .h5ad) — memory-safe via h5py peek.

        Strategy:
        1. Open with h5py, read shape + sample barcodes/genes without loading matrix
        2. If cell count < MAX_INSPECTION_CELLS, optionally load via anndata for full metadata
        """
        summary.file_type = "h5"
        try:
            import h5py
            with h5py.File(filepath, "r") as f:
                if self._is_tenx_format(f):
                    self._inspect_tenx_h5_light(f, summary)
                else:
                    self._inspect_h5ad_light(f, summary)
            # h5py peek succeeded — try anndata load for richer metadata if small enough
            shape = summary.h5_shape
            n_cells = shape[1] if shape else 0
            if n_cells > 0 and n_cells <= _MAX_INSPECTION_CELLS:
                self._inspect_h5_full(filepath, summary)
        except Exception as exc1:
            summary.error = f"h5 read failed: {exc1}"

    def _is_tenx_format(self, f: Any) -> bool:
        """Detect 10X Genomics .h5 format (has 'matrix' group with barcodes)."""
        return "matrix" in f and "barcodes" in f["matrix"]

    def _inspect_tenx_h5_light(self, f: Any, summary: FileSummary) -> None:
        """Peek at 10X .h5 metadata without loading the matrix."""
        mat = f["matrix"]
        attrs_shape = mat.attrs.get("shape")
        if attrs_shape is not None:
            summary.h5_shape = (int(attrs_shape[0]), int(attrs_shape[1]))
        else:
            # Fallback: count barcodes × features dimensions
            n_barcodes = len(mat["barcodes"])
            features = mat["features"]
            if "name" in features:
                n_features = len(features["name"])
            elif "id" in features:
                n_features = len(features["id"])
            else:
                n_features = n_barcodes
            summary.h5_shape = (n_barcodes, n_features)
        summary.h5_sparse = True
        summary.raw_available = False

        # Sample barcodes
        try:
            barcodes_ds = mat["barcodes"]
            n_sample = min(_MAX_BARCODE_SAMPLE, len(barcodes_ds))
            barcodes = [bc.decode("utf-8") if isinstance(bc, bytes) else str(bc)
                        for bc in barcodes_ds[:n_sample]]
            self._analyze_barcodes(barcodes, summary)
        except Exception:
            pass

        # Sample genes
        try:
            features = mat["features"]
            gene_key = "name" if "name" in features else "id"
            n_feat = min(_PREVIEW_ROWS, len(features[gene_key]))
            genes = [g.decode("utf-8") if isinstance(g, bytes) else str(g)
                     for g in features[gene_key][:n_feat]]
            summary.preview_rows = [{"gene": g} for g in genes]
        except Exception:
            pass

    def _inspect_h5ad_light(self, f: Any, summary: FileSummary) -> None:
        """Peek at h5ad metadata without loading the matrix."""
        # Shape from X
        if "X" in f:
            x_group = f["X"]
            if "shape" in x_group.attrs:
                summary.h5_shape = tuple(int(s) for s in x_group.attrs["shape"])
            elif "indptr" in x_group:
                n_obs = len(x_group["indptr"]) - 1
                if "shape" in x_group.attrs:
                    n_vars = x_group.attrs["shape"][1]
                else:
                    n_vars = 0
                summary.h5_shape = (n_obs, n_vars)
            summary.h5_sparse = "data" in x_group

        # obs columns
        if "obs" in f:
            obs_group = f["obs"]
            summary.obs_columns = list(obs_group.keys()) if obs_group else []
            # Barcodes from obs index
            if "_index" in obs_group:
                idx_ds = obs_group["_index"]
                n_sample = min(_MAX_BARCODE_SAMPLE, len(idx_ds))
                barcodes = [bc.decode("utf-8") if isinstance(bc, bytes) else str(bc)
                            for bc in idx_ds[:n_sample]]
                self._analyze_barcodes(barcodes, summary)

        # var columns
        if "var" in f:
            var_group = f["var"]
            summary.var_columns = list(var_group.keys()) if var_group else []
            # Gene samples from var index
            if "_index" in var_group:
                idx_ds = var_group["_index"]
                n_feat = min(_PREVIEW_ROWS, len(idx_ds))
                genes = [g.decode("utf-8") if isinstance(g, bytes) else str(g)
                         for g in idx_ds[:n_feat]]
                summary.preview_rows = [{"gene": g} for g in genes]

        summary.uns_keys = list(f["uns"].keys()) if "uns" in f else []
        summary.raw_available = "raw" in f

    def _inspect_h5_full(self, filepath: Path, summary: FileSummary) -> None:
        """Load via anndata/scanpy for richer metadata (obs/var columns, uns keys)."""
        try:
            import anndata
            adata = anndata.read_h5ad(filepath)
            summary.h5_shape = adata.shape
            summary.h5_sparse = (
                str(adata.X.__class__.__name__).lower().startswith("sparse")
                if hasattr(adata.X, "__class__") else None
            )
            summary.obs_columns = list(adata.obs.columns)
            summary.var_columns = list(adata.var.columns)
            summary.uns_keys = list(adata.uns.keys())
            summary.raw_available = adata.raw is not None
            if not summary.barcode_samples:
                self._analyze_barcodes(list(adata.obs_names), summary)
        except Exception:
            try:
                import scanpy as sc
                adata = sc.read_10x_h5(filepath)
                summary.h5_shape = adata.shape
                summary.h5_sparse = True
                summary.obs_columns = list(adata.obs.columns)
                summary.var_columns = list(adata.var.columns)
                summary.uns_keys = list(adata.uns.keys())
                summary.raw_available = adata.raw is not None
                if not summary.barcode_samples:
                    self._analyze_barcodes(list(adata.obs_names), summary)
            except Exception:
                pass  # already have light metadata, don't overwrite with error

    def _analyze_barcodes(
        self, barcodes: list[str], summary: FileSummary,
    ) -> None:
        """Extract barcode format, samples, and prefix patterns."""
        if not barcodes:
            return
        sample = barcodes[:min(_PREVIEW_ROWS, len(barcodes))]
        summary.barcode_samples = [str(b) for b in sample]

        # Detect format
        from scruticell.alignment.barcodes import detect_barcode_format
        try:
            summary.barcode_format = detect_barcode_format(barcodes)
        except Exception:
            # Simple heuristic
            acgt_count = sum(
                1 for b in sample
                if all(c in "ACGTacgt-" for c in str(b))
            )
            if acgt_count > len(sample) * 0.7:
                summary.barcode_format = "10x_like"

        # Detect prefixes (underscore-delimited)
        prefix_counts: dict[str, int] = {}
        for b in barcodes[:min(1000, len(barcodes))]:
            parts = str(b).split("_", 1)
            if len(parts) == 2:
                prefix = parts[0]
                prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
            # Also check for dot-delimited prefixes
            parts2 = str(b).split(".", 1)
            if len(parts2) == 2 and parts2[0] not in prefix_counts:
                prefix_counts[parts2[0]] = prefix_counts.get(parts2[0], 0) + 1

        if prefix_counts:
            # Filter noise: only keep prefixes with >1 occurrences
            significant = {k: v for k, v in prefix_counts.items() if v > 1}
            if significant:
                summary.barcode_prefixes = sorted(significant.keys())
                summary.barcode_prefix_counts = dict(
                    sorted(significant.items(), key=lambda x: -x[1])
                )


    def _inspect_rds(self, filepath: Path, summary: FileSummary) -> None:
        """Inspect an RDS file."""
        summary.file_type = "rds"
        try:
            import pyreadr
            result = pyreadr.read_r(str(filepath))
            summary.rds_keys = list(result.keys())
            for key, df in result.items():
                if hasattr(df, "shape"):
                    summary.rds_shapes[key] = df.shape
            # Populate preview from first DataFrame
            if result:
                first_key = next(iter(result))
                df = result[first_key]
                if hasattr(df, "columns"):
                    cols = list(df.columns[:_PREVIEW_COLS])
                    summary.columns = cols
                if hasattr(df, "head"):
                    head_rows = df.head(_PREVIEW_ROWS)
                    summary.preview_rows = [
                        {str(c): str(row[c]) if row[c] is not None else ""
                         for c in head_rows.columns[:_PREVIEW_COLS]}
                        for _, row in head_rows.iterrows()
                    ]
                # Barcode analysis on row names or first column
                if hasattr(df, "index") and len(df.index) > 0:
                    idx_sample = [str(i) for i in df.index[:_MAX_BARCODE_SAMPLE]]
                    self._analyze_barcodes(idx_sample, summary)
        except ImportError:
            summary.error = "pyreadr not installed"
        except Exception as exc:
            summary.error = f"rds read failed: {exc}"

    def _inspect_loom(self, filepath: Path, summary: FileSummary) -> None:
        """Inspect a Loom file."""
        summary.file_type = "loom"
        try:
            import loompy
            with loompy.connect(str(filepath)) as ds:
                summary.loom_shape = ds.shape
                ra_keys = list(ds.ra.keys()) if hasattr(ds, "ra") else []
                ca_keys = list(ds.ca.keys()) if hasattr(ds, "ca") else []
                summary.loom_row_attrs = ra_keys
                summary.loom_col_attrs = ca_keys
                # Sample attribute values
                col_attrs = {}
                for key in ca_keys[:_PREVIEW_COLS]:
                    try:
                        vals = ds.ca[key]
                        sample = [str(v) for v in vals[:_PREVIEW_ROWS]]
                        col_attrs[key] = sample
                    except Exception:
                        col_attrs[key] = []
                if col_attrs:
                    keys = list(col_attrs.keys())
                    summary.columns = keys
                    summary.preview_rows = [
                        {k: col_attrs[k][i] if i < len(col_attrs.get(k, [])) else ""
                         for k in keys}
                        for i in range(_PREVIEW_ROWS)
                    ]
                # Barcode analysis
                if "barcodes" in ca_keys:
                    try:
                        bcs = [str(b) for b in ds.ca["barcodes"][:_MAX_BARCODE_SAMPLE]]
                        self._analyze_barcodes(bcs, summary)
                    except Exception:
                        pass
        except ImportError:
            summary.error = "loompy not installed"
        except Exception as exc:
            summary.error = f"loom read failed: {exc}"

    def _inspect_mtx(self, filepath: Path, summary: FileSummary) -> None:
        """Inspect a Matrix Market file + companion barcodes/features files."""
        summary.file_type = "mtx"
        summary.mtx_is_sparse = True
        try:
            f, _ = self._open_text(filepath)
            for line in f:
                line = line.strip()
                if line.startswith("%"):
                    continue
                parts = line.split()
                if len(parts) == 3:
                    summary.mtx_shape = (int(parts[0]), int(parts[1]))
                break
            f.close()
        except Exception as exc:
            summary.error = f"mtx read failed: {exc}"
            return

        # Inspect companion barcodes/features files in the same directory
        parent = filepath.parent
        barcode_file = self._find_companion(parent, ("barcodes.tsv", "barcodes.tsv.gz"))
        features_file = self._find_companion(
            parent, ("features.tsv", "features.tsv.gz", "genes.tsv", "genes.tsv.gz"),
        )

        if barcode_file:
            try:
                lines, _ = self._read_lines(barcode_file, _PREVIEW_ROWS)
                if lines:
                    barcodes = [line.split("\t")[0].strip() for line in lines if line.strip()]
                    self._analyze_barcodes(barcodes, summary)
            except Exception:
                pass

        if features_file:
            try:
                lines, _ = self._read_lines(features_file, _PREVIEW_ROWS)
                if lines:
                    rows: list[dict[str, str]] = []
                    for line in lines:
                        parts = line.strip().split("\t")
                        if len(parts) >= 2:
                            rows.append({"gene_id": parts[0], "gene_name": parts[1]})
                        elif parts:
                            rows.append({"gene": parts[0]})
                    if rows:
                        summary.preview_rows = rows
            except Exception:
                pass

        # If only barcodes found, still set preview_rows
        if not summary.preview_rows and summary.barcode_samples:
            summary.preview_rows = [{"barcode": b} for b in summary.barcode_samples]

    def _find_companion(self, parent: Path, names: tuple[str, ...]) -> Path | None:
        """Find a companion file in the same directory."""
        for name in names:
            path = parent / name
            if path.exists():
                return path
            for child in parent.iterdir():
                if child.is_file() and child.name.lower() == name.lower():
                    return child
        return None

    def _read_lines(self, filepath: Path, max_lines: int) -> tuple[list[str], int]:
        """Read up to max_lines lines from a (possibly gzipped) file."""
        fh, _ = self._open_text(filepath)
        try:
            lines = []
            for i, line in enumerate(fh):
                if i >= max_lines:
                    break
                lines.append(line)
            return lines, len(lines)
        finally:
            fh.close()

    def deduplicate(
        self, summaries: dict[str, FileSummary]
    ) -> dict[str, FileSummary]:
        """Remove duplicate files by (file_type, shape, gene_sample, barcode_prefix) fingerprint."""
        seen: dict[tuple, str] = {}
        dedup: dict[str, FileSummary] = {}
        for ancestry, s in summaries.items():
            fp = self._file_fingerprint(s)
            if fp in seen:
                logger.debug("Duplicate: %s (same as %s)", ancestry, seen[fp])
            else:
                seen[fp] = ancestry
                dedup[ancestry] = s
        logger.info("Deduplication: %d -> %d unique", len(summaries), len(dedup))
        return dedup

    def _file_fingerprint(self, s: FileSummary) -> tuple:
        shape = s.h5_shape or s.mtx_shape or s.loom_shape or (s.n_rows or 0, s.n_cols or 0)
        gene_sample = tuple(
            list(s.preview_rows[i].values())[0]
            for i in range(min(5, len(s.preview_rows)))
            if s.preview_rows and s.preview_rows[i]
        )
        prefix_key = tuple(sorted(s.barcode_prefixes)) if s.barcode_prefixes else ()
        return (s.file_type, shape, gene_sample, prefix_key)

    def _inspect_soft(self, filepath: Path, summary: FileSummary) -> None:
        """Inspect a SOFT file."""
        summary.file_type = "soft"
        try:
            f, _ = self._open_text(filepath)
            content = f.read(_PREVIEW_MAX_CHARS * 4)
            f.close()

            if "!Series_" in content:
                summary.soft_sections.append("series")
            if "!Platform_" in content:
                summary.soft_sections.append("platform")
            if "!Sample_" in content:
                summary.soft_sections.append("sample")

            import re
            summary.soft_gsm_count = len(re.findall(r"\^SAMPLE\s*=\s*GSM", content, re.I))
            summary.soft_gpl_count = len(re.findall(r"\^PLATFORM\s*=\s*GPL", content, re.I))
        except Exception as exc:
            summary.error = f"soft read failed: {exc}"

    def to_dict(self, summary: FileSummary) -> dict[str, Any]:
        """Convert summary to JSON-serializable dict, dropping None/empty values."""
        d: dict[str, Any] = {}
        for k, v in asdict(summary).items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            if k == "preview_rows" and v:
                d[k] = v
            else:
                d[k] = v
        return d
