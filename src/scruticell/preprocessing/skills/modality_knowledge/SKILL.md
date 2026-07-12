# Modality Knowledge

## Supported Modalities (12 types)

When classifying GEO dataset files, identify which of these modalities apply to each sample group.

---

### scrna_seq — Single-Cell RNA-seq

**Metadata signals**: molecule="total RNA" or "polyA RNA", library_strategy="RNA-Seq", suspension_type="cell"
**File patterns**: barcodes.tsv.gz + features.tsv.gz + matrix.mtx.gz (10x 3-file set), *.h5ad, *.h5
**Content signals**: Features are gene symbols (BRCA1, TP53) or Ensembl IDs (ENSGXXXXXXXXXXX). Barcodes follow 10x v2 (16bp) or v3 (16bp) format.
**Known platforms**: 10x Chromium v2/v3, Smart-seq2, Drop-seq, CEL-seq2, inDrop, Seq-Well, Parse Biosciences
**File roles**: matrix=h5/mtx/csv, barcodes=tsv with cell IDs, features=tsv with gene IDs

### snrna_seq — Single-Nucleus RNA-seq

**Metadata signals**: Like scrna_seq but suspension_type="nucleus" or "nuclei". Title often contains "single-nucleus", "snRNA-seq", "snRNA".
**Distinction from scrna_seq**: Same file patterns and content signals as scrna_seq. Only distinguished by suspension type in metadata.

### scatac_seq — Single-Cell ATAC-seq

**Metadata signals**: library_strategy="ATAC-seq", molecule="genomic DNA"
**File patterns**: peaks.bed, fragments.tsv.gz, *.h5 (10x ATAC output), filtered_tf_bc_matrix/
**Content signals**: Features are genomic intervals (chr1:12345-67890). Column names include "chr", "start", "end". Not gene symbols (genes do not appear in ATAC-seq features).
**Known platforms**: 10x ATAC v1/v2, snATAC-seq, sciATAC-seq
**File roles**: matrix=count matrix over peaks, peaks=peak annotations BED file, fragments=fragment file

### multiome — 10x Multiome (RNA + ATAC from same cells)

**Metadata signals**: Title/description contains "Multiome" or "Multi-ome". Often both RNA-Seq and ATAC-seq are mentioned.
**File patterns**: Two separate subdirectories: "rna/" or "RNA/" and "atac/" or "ATAC/". Or paired GSM entries with matching donor IDs.
**Content signals**: Combined signals from both scrna_seq and scatac_seq. Same cells have both RNA and ATAC data.
**Classification note**: Label as "multiome" ONLY if a single sample clearly has paired RNA+ATAC data from the same cells. Otherwise, classify RNA and ATAC as separate scrna_seq/scatac_seq groups.

### spatial_transcriptomics — Spatial Gene Expression (Visium/Slide-seq/MERFISH)

**Metadata signals**: library_strategy often "OTHER". Title contains "Visium", "Slide-seq", "MERFISH", "Stereo-seq", "spatial transcriptomics".
**File patterns**: tissue_positions_list.csv, spatial/ subdirectory, tissue_hires_image.png, scalefactors_json.json. Visium: filtered_feature_bc_matrix/ under spatial output.
**Content signals**: Features are gene symbols (same as RNA-seq). Additional spatial coordinate files. Barcodes correspond to spatial spots, not individual cells.
**File roles**: matrix=spatial count matrix, barcodes=tissue_positions_list, features=gene IDs, spatial=coordinate/image files

### dna_methylation — DNA Methylation (Bisulfite-Seq, snmC-seq, WGBS)

**Metadata signals**: library_strategy="Bisulfite-Seq" or "MethylC-seq" or "OTHER". Title/description often contains "methylation", "snmC-seq", "WGBS", "RRBS".
**File patterns**: *.cov.gz (CpG coverage files), *.bedGraph, methylation call files.
**Content signals**: Feature IDs are CpG sites (cgXXXXXXX). Values are methylation ratios (0.0 to 1.0) or coverage counts. NOT gene expression values.
**File roles**: matrix=methylation ratio matrix, features=CpG annotations

### cite_seq — CITE-seq (Antibody-based protein + RNA)

**Metadata signals**: Title/description contains "CITE-seq", "TotalSeq", "ADT", "antibody-derived tag", "hashtag".
**File patterns**: ADT_*.csv, ADT_*.tsv, antibody_*.csv. ALWAYS co-occurs with RNA-seq files.
**Content signals**: Features are short protein names (CD3, CD4, CD8, CD19, CD45RA, etc.) — NOT gene symbols. Very few features (typically 20-200 antibodies vs thousands of genes).
**Classification note**: CITE-seq is always multi-modal (RNA + protein). Classify the RNA portion as scrna_seq and the antibody portion as cite_seq. These are separate modalities on the same cells.

### tcr_seq — T-Cell Receptor Repertoire

**Metadata signals**: Title/description contains "TCR", "T-cell receptor", "T cell receptor". library_strategy often "OTHER".
**File patterns**: filtered_contig_annotations.csv, clonotypes.csv, consensus_annotations.csv. 10x VDJ output.
**Content signals**: Chains: TRA, TRB, TRD, TRG. Columns: v_gene, d_gene, j_gene, cdr3, cdr3_nt.
**File roles**: contigs=TCR contig annotations, clonotypes=clonal groupings

### bcr_seq — B-Cell Receptor Repertoire

**Metadata signals**: Title/description contains "BCR", "B-cell receptor", "immunoglobulin". library_strategy often "OTHER".
**File patterns**: Same as tcr_seq (filtered_contig_annotations.csv). 10x VDJ output.
**Content signals**: Chains: IGH, IGK, IGL. Similar structure to TCR.
**Distinction**: BCR vs TCR is determined from chain names: IGH/IGK/IGL = BCR, TRA/TRB = TCR.

### perturb_seq — CRISPR Perturbation Screen

**Metadata signals**: Title/description contains "Perturb-seq", "CRISPR screen", "CRISPRi", "CRISPRa", "CROP-seq", "single-cell CRISPR".
**File patterns**: guide_assignment.csv, sgRNA_info.csv, protospacer_calls.csv, cell_identities.csv.
**Content signals**: Features include sgRNA names (e.g., "sgTP53_1"). Usually co-occurs with RNA-seq data.
**Classification note**: Like CITE-seq, Perturb-seq is multi-modal. Classify the RNA portion as scrna_seq and the CRISPR guide portion as perturb_seq.

### proteomics — Mass Spectrometry Proteomics

**Metadata signals**: molecule="protein" or "peptide". Instrument often "LC-MS/MS", "Orbitrap", "Q-Exactive".
**File patterns**: protein_groups.csv, peptide_groups.csv, protein_abundance.tsv.
**Content signals**: Features are protein IDs (UniProt accession) or gene names. Values are protein abundance/intensity. Does NOT require single-cell resolution.
**File roles**: matrix=protein abundance matrix, features=protein annotations

### other — Unknown / Unclassified

**Use when**: No evidence supports any known modality. Features are unidentifiable. Metadata is empty or contradictory. After evidence gathering, still no clear answer.
**Do NOT use when**: You're just unsure between two modalities — in that case, pick the more likely one with lower confidence.
