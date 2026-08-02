# Omics Preprocessing Agent — Test Suite

## Test Selection Rationale

The preprocessing agent must handle 12 modality types across diverse dataset structures.  
20 multi-omics GSEs were identified in `multi_omics_samples.csv`. From these, 5 core + 2 extended
were selected to cover all modality families, edge cases, and complexity tiers.

---

## Core 5 (covers all modality families + key edge cases)

### 1. GSE151302 — ATAC-seq + snRNA (simplest ATAC multi-modal)

| Field | Value |
|-------|-------|
| Datasets | 2 |
| Modalities | ATAC_seq, snRNA_seq |
| Assays | 10x 5' v1, 10x scATAC-seq |
| Suspension | nucleus |
| Organism | Homo sapiens |
| Total Cells | 47,019 |

**Why**: Simplest ATAC case. Nucleus suspension tests snRNA vs scRNA distinction.
ATAC detection via `scATAC-seq` assay + genomic interval features.

**Expected**: agent detects one sample group with 2 modalities (ATAC + snRNA).
File→sample 1:1 mapping via ancestry paths (GSM subdirectories with standard 10x layout).

---

### 2. GSE166895 — CITE-seq (only proteomics multi-modal)

| Field | Value |
|-------|-------|
| Datasets | 4 |
| Modalities | CITE_seq, scRNA_seq |
| Assays | 10x 3' v2, 10x 3' v3, 10x 5' v1 |
| Suspension | cell |
| Organism | Homo sapiens |
| Total Cells | 156,947 |

**Why**: The ONLY CITE-seq in the multi-omics list. Tests antibody feature detection:
features are short protein names (CD3, CD4...), not gene symbols. ADT file recognition.

**Expected**: agent detects each GSM as a sample group with 2 modalities (scRNA + CITE-seq).
ADT antibody features identified via content_preview (CD3, CD4 protein names).

---

### 3. GSE192742 — Spatial + RNA + snRNA (most complex spatial)

| Field | Value |
|-------|-------|
| Datasets | 21 |
| Modalities | Spatial_Transcriptomics, scRNA_seq, snRNA_seq |
| Assays | 10x 3' v2, 10x 3' v3, Visium Spatial Gene Expression V1 |
| Suspension | cell, na, nucleus |
| Organism | **Homo sapiens + Mus musculus** |
| Total Cells | 416,444 |

**Why**: Toughest edge case — **dual organism**, all 3 suspension types (cell/na/nucleus),
Visium spatial detection, 3 distinct modality types within one GSE.

**Expected**: agent correctly separates human vs mouse samples, identifies
spatial (Visium) vs non-spatial (10x), distinguishes scRNA (cell suspension) from snRNA (nucleus).

---

### 4. GSE196235 — Multiome (simplest single-dataset case)

| Field | Value |
|-------|-------|
| Datasets | 1 |
| Modalities | Multiome_RNA_ATAC |
| Assays | 10x multiome |
| Suspension | nucleus |
| Organism | Homo sapiens |
| Total Cells | 51,645 |

**Why**: Single dataset Multiome — tests the "no grouping needed" minimal path.
`10x multiome` assay means RNA+ATAC from same cells simultaneously.

**Expected**: agent creates 1 sample group with Multiome modality (or splits into RNA + ATAC subtrees).

---

### 5. GSE271304 — Spatial + RNA (largest GSM count)

| Field | Value |
|-------|-------|
| Datasets | **39** |
| Modalities | Spatial_Transcriptomics, scRNA_seq |
| Assays | 10x 3' v2, 10x 3' v3, 10x 5' v1, 10x 5' v2, Visium Spatial Gene Expression V1 |
| Suspension | cell, na |
| Organism | Homo sapiens |
| Total Cells | 1,142,463 |

**Why**: Largest GSM count — stress-tests L1-L3 token dedup optimizations.
39 GSM × 5 fields → field_value_maps compression should achieve >90% savings.
Visium spatial files mixed with regular 10x 3'/5' assays.

**Expected**: agent efficiently groups 39 GSM, correctly separates spatial from non-spatial,
L1-L3 dedup produces compact (<1K token) report.

---

## Extended 2 (deeper edge case coverage)

### 6. GSE176078 — Spatial + RNA (assay variety)

| Field | Value |
|-------|-------|
| Datasets | 13 |
| Modalities | Spatial_Transcriptomics, scRNA_seq |
| Assays | 10x 3' v2, 10x 3' v3, 10x 5' v1, Visium Spatial Gene Expression V1 |
| Suspension | cell, na |
| Organism | Homo sapiens |
| Total Cells | 225,725 |

**Why**: Good mid-size spatial test with 4 different assay types.
Tests that agent can distinguish regular 10x from Visium without confusion.

---

### 7. GSE321762 — ATAC-seq + RNA (mouse context)

| Field | Value |
|-------|-------|
| Datasets | 3 |
| Modalities | ATAC_seq, scRNA_seq |
| Assays | 10x 3' v3, 10x scATAC-seq |
| Suspension | cell, nucleus |
| Organism | **Mus musculus** |
| Total Cells | 286,276 |

**Why**: Mouse ATAC case — tests non-human gene ID detection (ENSMUSG vs ENSG).
Mixed cell+nucleus suspension with ATAC+RNA dual-modality.

---

## Coverage Matrix

| Capability | GSE151302 | GSE166895 | GSE192742 | GSE196235 | GSE271304 | GSE176078 | GSE321762 |
|-----------|:---------:|:---------:|:---------:|:---------:|:---------:|:---------:|:---------:|
| ATAC detection | ✓ | — | — | — | — | — | ✓ |
| Spatial detection | — | — | ✓ | — | ✓ | ✓ | — |
| CITE-seq / protein | — | ✓ | — | — | — | — | — |
| Multiome detection | — | — | — | ✓ | — | — | — |
| scRNA → snRNA distinction | ✓ | — | ✓ | — | — | — | — |
| Nucleus suspension | ✓ | — | ✓ | ✓ | — | — | ✓ |
| Cell suspension | — | ✓ | ✓ | — | ✓ | ✓ | ✓ |
| Na suspension | — | — | ✓ | — | ✓ | ✓ | — |
| Multi-organism (human + mouse) | — | — | ✓ | — | — | — | — |
| Mouse gene detection | — | — | — | — | — | — | ✓ |
| Minimal (1 dataset) | — | — | — | ✓ | — | — | — |
| Mid-size (2-13 datasets) | ✓ | ✓ | — | — | — | ✓ | ✓ |
| Large (21 datasets) | — | — | ✓ | — | — | — | — |
| Maximum (39 datasets) | — | — | — | — | ✓ | — | — |
| Multiple assay types in one GSE | — | ✓ | ✓ | — | ✓ | ✓ | — |
| Token dedup stress (L1-L3) | Low | Low | High | Minimal | **Maximum** | Medium | Low |

---

## Recommended Test Order

Start simple, escalate complexity:

1. **GSE196235** — smoke test (1 dataset, simplest flow)
2. **GSE151302** — verify ATAC detection
3. **GSE166895** — verify CITE-seq / protein modality
4. **GSE321762** — verify mouse context
5. **GSE176078** — mid-size spatial verification
6. **GSE192742** — complex multi-organism spatial
7. **GSE271304** — stress test (39 datasets, max dedup)

---

## Expected Per-Test Metrics

| Metric | Small (1-4 ds) | Medium (13 ds) | Large (21-39 ds) |
|--------|---------------|----------------|-----------------|
| FileContextReport tokens | ~300-500 | ~500-800 | ~800-1200 |
| L1 savings (vs raw metadata) | 60-70% | 85-90% | 93-97% |
| L2 savings (vs per-file signals) | 80-90% | 95% | 98% |
| per_file_gsm_id entries | =file_count | =file_count | =file_count |
| Classification correctness | All deterministic from assay + ancestry | Spatial vs non-spatial edge | Max dedup pressure |
| Shared file detection | Common features.tsv.gz | Common for spatial images | Common for all groups |
