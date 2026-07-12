# File Reader Code Generator

Generate Python code to read any bioinformatics file format. Output is a function that reads the specific file at hand — not a generic parser.

## Input

- **content_preview**: raw_lines (first 10 lines of actual text), columns, feature_samples, barcode_samples, n_rows, n_cols, feature_type, barcode_format
- **modality**: one of scrna_seq/snrna_seq/scatac_seq/multiome/spatial/dna_methylation/cite_seq/tcr_seq/bcr_seq/perturb_seq/proteomics/other
- **file_role**: matrix/barcodes/features/peaks/fragments/contigs/clonotypes/guide_info/spatial_positions/spatial_image/other

## Output

A Python function:

```python
def read_{role}(filepath: str) -> dict:
    """
    Returns dict with keys appropriate for the file role.
    For matrix files: {"data": ndarray/sparse, "row_names": list, "col_names": list, "shape": tuple, "is_sparse": bool}
    For feature files: {"feature_ids": list, "feature_names": list}
    For barcode files: {"barcodes": list}
    For contig/clonotype files: {"data": DataFrame}
    For BED files: {"data": DataFrame, "columns": list}
    """
```

## File Type Specifications

### features.tsv.gz (10x v2)
- 2 columns, NO header, tab-separated
- Column 1: feature ID (ENSG...), Column 2: gene symbol
- Return: {"feature_ids": col1, "feature_names": col2}

### features.tsv.gz (10x v3)
- 3+ columns, NO header, tab-separated
- Column 1: feature ID (ENSG...), Column 2: gene symbol, Column 3: feature_type ("Gene Expression"/"Peaks"/"Antibody Capture")
- Return: {"feature_ids": col1, "feature_names": col2, "feature_types": col3}

### barcodes.tsv.gz (10x)
- 1 column, NO header, tab-separated (no tabs, just one value per line)
- Each line is a 16bp DNA barcode, may have -N suffix
- Strip suffix, uppercase everything
- Return: {"barcodes": [str, ...]}

### matrix.mtx.gz (10x MatrixMarket)
- Sparse coordinate format. Headers: %%MatrixMarket..., %%, then size line
- Use scipy.io.mmread. Converts to CSR if CSC.
- Orientation: check shape against companion barcode/feature file line counts
- Return: {"data": sparse_csr, "shape": (n_rows, n_cols), "is_sparse": True}

### .h5ad / .h5 (AnnData / 10x HDF5)
- Use scanpy.read_10x_h5 or anndata.read_h5ad
- Check for .raw (raw counts layer)
- Return: {"data": matrix, "row_names": obs_names, "col_names": var_names, "shape": ..., "is_sparse": ...}

### .csv / .tsv / .txt (generic matrix)
- May have header. Look at first line: if it contains words like "gene_id", "gene_name", "barcode", "sample" → header. If it looks like ENSG IDs or barcode strings → no header.
- May have row names (index column). If first column looks like gene names/ENSGs/barcodes → it's row names, use index_col=0.
- Delimiter: look for consistent tab or comma separation in raw_lines.
- Orientation: if first column is gene names → gene×cell (transpose needed for AnnData). If first row is gene names → cell×gene.
- Return: {"data": ndarray, "row_names": [], "col_names": [], "shape": (r,c), "is_sparse": False}

### peaks.bed
- Tab-separated, may have comment lines (track/##/browser). Skip those.
- BED3: chrom, start, end. BED6: + name, score, strand. narrowPeak: 10 columns.
- 0-based coordinates, half-open intervals (standard BED convention).
- Return: {"data": DataFrame, "columns": ["chrom","start","end",...]}

### fragments.tsv.gz (10x ATAC)
- 5 columns, NO header, tab-separated, gzip-compressed
- chrom, start, end, cell_barcode, count
- Return: {"data": DataFrame}

### .cov.gz (Bismark methylation)
- NO header, tab-separated, gzip-compressed
- 6 columns: chrom, start, end, pct_meth, n_meth, n_unmeth
- May have 7th column: context (CpG/CHG/CHH)
- pct_meth is 0-100, convert to 0-1 fraction
- Return: {"data": DataFrame}

### tissue_positions_list.csv (Visium spatial)
- Visium v1: NO header, 6 columns (barcode, in_tissue, array_row, array_col, pxl_row_in_fullres, pxl_col_in_fullres)
- Visium v2: HAS header
- Check first line: if "barcode" → v2 with header. If looks like barcode string → v1 no header.
- Return: {"data": DataFrame}

### filtered_contig_annotations.csv (10x VDJ)
- HAS header. Columns: barcode, is_cell, contig_id, high_confidence, length, chain, v_gene, d_gene, j_gene, c_gene, full_length, productive, cdr3, cdr3_nt, reads, umis, raw_clonotype_id, raw_consensus_id
- Barcode may have -N suffix. Strip it.
- Return: {"data": DataFrame}

### clonotypes.csv (10x VDJ)
- HAS header. Columns: clonotype_id, frequency, proportion, cdr3s_aa, cdr3s_nt
- Return: {"data": DataFrame}

### .rds / .RData (R Seurat/SCE)
- Use pyreadr.read_r()
- Find the expression matrix: look for key containing "count"/"expr"/"data"
- Orientation: R stores gene×cell. Transpose to cell×gene.
- Return: {"data": matrix, "row_names": barcodes, "col_names": genes, ...}

### .loom
- Use loompy.connect()
- Row attributes for gene names: check "Gene"/"gene_name"/"symbol"/"Accession"
- Column attributes for barcodes: check "barcode"/"CellID"/"cell_id"
- Matrix layer: try "matrix" → "counts" → "" (default)
- Return: {"data": matrix, "row_names": barcodes, "col_names": genes, ...}

## Sandbox Constraints

- Allowed imports: pandas, numpy, scipy, anndata, scanpy, gzip, csv, re, io, os.path (read-only), pyreadr, loompy
- NOT allowed: os.system, subprocess, network, file write, __import__
- 30s timeout, 2GB memory limit
- Function signature MUST be: def read_{role}(filepath: str) -> dict

## Retry on Failure

If execution fails, the error traceback is shown. Regenerate code fixing the issue.
