"""Neo4j knowledge graph schema definition.

The knowledge graph stores accumulated parsing strategies as true graph data,
decomposing each plan into linked nodes: ParseStrategy → BatchStrategy →
MatrixStrategy, with ProcessingCode / AssemblyCode nodes for generated code.

This is the long-term memory substrate for the Reflexion agent.
"""

from __future__ import annotations

# === Constraints and Indexes ===

CONSTRAINTS: list[str] = [
    # Field Synonym uniqueness
    "CREATE CONSTRAINT IF NOT EXISTS FOR (fs:FieldSynonym) REQUIRE fs.name IS UNIQUE",
    # Gene Mapping uniqueness (source_id + source_system)
    "CREATE CONSTRAINT IF NOT EXISTS FOR (gm:GeneMapping) REQUIRE gm.source_id IS UNIQUE",
    # GSE uniqueness
    "CREATE CONSTRAINT IF NOT EXISTS FOR (gse:GSE) REQUIRE gse.gse_id IS UNIQUE",
    # Parse Strategy uniqueness
    "CREATE CONSTRAINT IF NOT EXISTS FOR (ps:ParseStrategy) REQUIRE ps.strategy_id IS UNIQUE",
    # Failure Case uniqueness
    "CREATE CONSTRAINT IF NOT EXISTS FOR (fc:FailureCase) REQUIRE fc.case_id IS UNIQUE",
    # Batch Pattern uniqueness
    "CREATE CONSTRAINT IF NOT EXISTS FOR (bp:BatchPattern) REQUIRE bp.pattern_name IS UNIQUE",
    # Batch Strategy uniqueness (composite: strategy_id + gsm_id)
    "CREATE CONSTRAINT IF NOT EXISTS FOR (bs:BatchStrategy) REQUIRE bs.batch_id IS UNIQUE",
    # Matrix Strategy uniqueness (composite: file_key within strategy)
    "CREATE CONSTRAINT IF NOT EXISTS FOR (ms:MatrixStrategy) REQUIRE ms.matrix_id IS UNIQUE",
    # Processing Code uniqueness
    "CREATE CONSTRAINT IF NOT EXISTS FOR (pc:ProcessingCode) REQUIRE pc.code_id IS UNIQUE",
    # Assembly Code uniqueness
    "CREATE CONSTRAINT IF NOT EXISTS FOR (ac:AssemblyCode) REQUIRE ac.code_id IS UNIQUE",
    # Field Mapping Strategy uniqueness
    "CREATE CONSTRAINT IF NOT EXISTS FOR (fm:FieldMappingStrategy) REQUIRE fm.mapping_id IS UNIQUE",
]

INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS FOR (fs:FieldSynonym) ON (fs.canonical_name)",
    "CREATE INDEX IF NOT EXISTS FOR (gm:GeneMapping) ON (gm.target_symbol)",
    "CREATE INDEX IF NOT EXISTS FOR (gse:GSE) ON (gse.organism)",
    "CREATE INDEX IF NOT EXISTS FOR (gse:GSE) ON (gse.assay_type)",
    "CREATE INDEX IF NOT EXISTS FOR (ps:ParseStrategy) ON (ps.success_count)",
    "CREATE INDEX IF NOT EXISTS FOR (fc:FailureCase) ON (fc.error_signature)",
    "CREATE INDEX IF NOT EXISTS FOR (bp:BatchPattern) ON (bp.detection_rule)",
    "CREATE INDEX IF NOT EXISTS FOR (bs:BatchStrategy) ON (bs.gsm_id)",
    "CREATE INDEX IF NOT EXISTS FOR (bs:BatchStrategy) ON (bs.genotype)",
    "CREATE INDEX IF NOT EXISTS FOR (bs:BatchStrategy) ON (bs.treatment)",
    "CREATE INDEX IF NOT EXISTS FOR (ms:MatrixStrategy) ON (ms.format)",
    "CREATE INDEX IF NOT EXISTS FOR (fm:FieldMappingStrategy) ON (fm.canonical)",
]

# === Node Creation Queries ===

CREATE_FIELD_SYNONYM = """
MERGE (fs:FieldSynonym {name: $name})
SET fs.canonical_name = $canonical_name,
    fs.category = $category,
    fs.confidence = $confidence,
    fs.first_seen = COALESCE(fs.first_seen, $gse_id),
    fs.last_seen = $gse_id,
    fs.occurrence_count = COALESCE(fs.occurrence_count, 0) + 1
"""

CREATE_GENE_MAPPING = """
MERGE (gm:GeneMapping {source_id: $source_id, source_system: $source_system})
SET gm.target_symbol = $target_symbol,
    gm.confidence = $confidence,
    gm.species = $species,
    gm.first_seen = COALESCE(gm.first_seen, $gse_id),
    gm.last_seen = $gse_id,
    gm.occurrence_count = COALESCE(gm.occurrence_count, 0) + 1
"""

CREATE_GSE_NODE = """
MERGE (gse:GSE {gse_id: $gse_id})
SET gse.title = $title,
    gse.organism = $organism,
    gse.assay_type = $assay_type,
    gse.n_cells = $n_cells,
    gse.n_genes = $n_genes,
    gse.n_samples = $n_samples,
    gse.platform_ids = $platform_ids,
    gse.pubmed_id = $pubmed_id,
    gse.processed_at = datetime()
"""

# ── Strategy Graph Nodes ──

CREATE_PARSE_STRATEGY = """
CREATE (ps:ParseStrategy {
    strategy_id: $strategy_id,
    gse_pattern: $gse_pattern,
    gene_id_system: $gene_id_system,
    normalize_genes: $normalize_genes,
    drop_duplicate_genes: $drop_duplicate_genes,
    normalization: $normalization,
    is_raw_counts: $is_raw_counts,
    batch_separation_strategy: $batch_separation_strategy,
    strategy_notes: $strategy_notes,
    confidence: $confidence,
    success_count: $success_count,
    fail_count: $fail_count,
    avg_confidence: $avg_confidence,
    created_at: datetime()
})
WITH ps
MATCH (gse:GSE {gse_id: $gse_id})
CREATE (gse)-[:HAS_STRATEGY]->(ps)
"""

CREATE_BATCH_STRATEGY = """
CREATE (bs:BatchStrategy {
    batch_id: $batch_id,
    gsm_id: $gsm_id,
    batch_label: $batch_label,
    cell_count: $cell_count,
    barcode_prefix: $barcode_prefix,
    note: $note,
    reasoning: $reasoning,
    defining_fields: $defining_fields
})
WITH bs
MATCH (ps:ParseStrategy {strategy_id: $strategy_id})
CREATE (ps)-[:HAS_BATCH]->(bs)
"""

CREATE_MATRIX_STRATEGY = """
CREATE (ms:MatrixStrategy {
    matrix_id: $matrix_id,
    file_key: $file_key,
    format: $format,
    orientation: $orientation,
    transpose: $transpose,
    note: $note
})
WITH ms
MATCH (ps:ParseStrategy {strategy_id: $strategy_id})
CREATE (ps)-[:USES_MATRIX]->(ms)
"""

LINK_BATCH_TO_MATRIX = """
MATCH (bs:BatchStrategy {batch_id: $batch_id})
MATCH (ms:MatrixStrategy {matrix_id: $matrix_id})
CREATE (bs)-[:USES_MATRIX]->(ms)
"""

CREATE_PROCESSING_CODE = """
CREATE (pc:ProcessingCode {
    code_id: $code_id,
    code: $code,
    language: 'python'
})
WITH pc
MATCH (ps:ParseStrategy {strategy_id: $strategy_id})
CREATE (ps)-[:EXECUTES]->(pc)
"""

CREATE_ASSEMBLY_CODE = """
CREATE (ac:AssemblyCode {
    code_id: $code_id,
    code: $code,
    language: 'python'
})
WITH ac
MATCH (ps:ParseStrategy {strategy_id: $strategy_id})
CREATE (ps)-[:ASSEMBLES_WITH]->(ac)
"""

CREATE_FIELD_MAPPING_STRATEGY = """
CREATE (fm:FieldMappingStrategy {
    mapping_id: $mapping_id,
    raw_field: $raw_field,
    canonical: $canonical,
    source: $source,
    note: $note
})
WITH fm
MATCH (ps:ParseStrategy {strategy_id: $strategy_id})
CREATE (ps)-[:MAPS_FIELD]->(fm)
"""

# ── Cleanup helper: delete old strategy + all linked sub-nodes ──

DELETE_STRATEGY_CASCADE = """
MATCH (ps:ParseStrategy {strategy_id: $strategy_id})
OPTIONAL MATCH (ps)-[:HAS_BATCH]->(bs:BatchStrategy)
OPTIONAL MATCH (ps)-[:USES_MATRIX]->(ms:MatrixStrategy)
OPTIONAL MATCH (ps)-[:EXECUTES]->(pc:ProcessingCode)
OPTIONAL MATCH (ps)-[:ASSEMBLES_WITH]->(ac:AssemblyCode)
OPTIONAL MATCH (ps)-[:MAPS_FIELD]->(fm:FieldMappingStrategy)
OPTIONAL MATCH (bs)-[:USES_MATRIX]->(bms:MatrixStrategy)
DETACH DELETE bs, ms, pc, ac, fm, ps, bms
"""

# === Failure & Pattern queries ===

CREATE_FAILURE_CASE = """
MERGE (fc:FailureCase {case_id: $case_id})
ON CREATE SET
    fc.error_signature = $error_signature,
    fc.gse_id = $gse_id,
    fc.pipeline_stage = $pipeline_stage,
    fc.error_message = $error_message,
    fc.fix_strategy = $fix_strategy,
    fc.fixed = $fixed,
    fc.success_count = $success_count,
    fc.fail_count = $fail_count,
    fc.created_at = datetime()
ON MATCH SET
    fc.fixed = $fixed,
    fc.success_count = COALESCE(fc.success_count, 0) + $success_count,
    fc.fail_count = COALESCE(fc.fail_count, 0) + $fail_count
WITH fc
MERGE (gse:GSE {gse_id: $gse_id})
MERGE (gse)-[:HAS_FAILURE]->(fc)
"""

CREATE_BATCH_PATTERN = """
MERGE (bp:BatchPattern {pattern_name: $pattern_name})
SET bp.detection_rule = $detection_rule,
    bp.examples = $examples,
    bp.confidence = $confidence,
    bp.occurrence_count = COALESCE(bp.occurrence_count, 0) + 1
"""

# === Find queries ===

FIND_SIMILAR_FAILURES = """
MATCH (fc:FailureCase)
WHERE fc.pipeline_stage = $pipeline_stage
RETURN fc.case_id AS case_id, fc.error_signature AS error_signature,
       fc.fix_strategy AS fix_strategy, fc.fixed AS fixed,
       fc.error_message AS error_message, fc.gse_id AS gse_id,
       COALESCE(fc.success_count, 0) AS success_count,
       COALESCE(fc.fail_count, 0) AS fail_count
ORDER BY fc.success_count DESC, fc.created_at DESC
LIMIT 20
"""

FIND_BEST_STRATEGY = """
MATCH (ps:ParseStrategy)
WHERE ps.organism = $organism AND ps.assay_type = $assay_type
RETURN ps.strategy_id, ps.gene_id_system, ps.strategy_notes,
       ps.success_count, ps.fail_count, ps.avg_confidence, ps.confidence
ORDER BY ps.success_count DESC LIMIT 10
"""

# ── Graph traversal: reconstruct full plan from linked nodes ──

RECONSTRUCT_STRATEGY_PLAN = """
MATCH (ps:ParseStrategy {strategy_id: $strategy_id})
OPTIONAL MATCH (ps)-[:HAS_BATCH]->(bs:BatchStrategy)
OPTIONAL MATCH (bs)-[:USES_MATRIX]->(bms:MatrixStrategy)
OPTIONAL MATCH (ps)-[:USES_MATRIX]->(ms:MatrixStrategy)
OPTIONAL MATCH (ps)-[:EXECUTES]->(pc:ProcessingCode)
OPTIONAL MATCH (ps)-[:ASSEMBLES_WITH]->(ac:AssemblyCode)
OPTIONAL MATCH (ps)-[:MAPS_FIELD]->(fm:FieldMappingStrategy)
RETURN ps,
       collect(DISTINCT bs) AS batches,
       collect(DISTINCT ms) AS matrices,
       collect(DISTINCT bms) AS batch_matrices,
       collect(DISTINCT pc) AS processing_codes,
       collect(DISTINCT ac) AS assembly_codes,
       collect(DISTINCT fm) AS field_mappings
"""

FIND_STRATEGIES_BY_SIGNATURE = """
MATCH (ps:ParseStrategy)
WHERE ps.strategy_id = $strategy_id
   OR ps.strategy_id STARTS WITH $prefix
RETURN ps.strategy_id, ps.strategy_notes, ps.confidence,
       ps.success_count, ps.fail_count, ps.avg_confidence
ORDER BY ps.success_count DESC, ps.created_at DESC
LIMIT $limit
"""

UPDATE_STRATEGY_STATS = """
MATCH (ps:ParseStrategy {strategy_id: $strategy_id})
SET ps.success_count = COALESCE(ps.success_count, 0) + $increment_success,
    ps.fail_count = COALESCE(ps.fail_count, 0) + $increment_fail
"""

LINK_FAILURE_TO_STRATEGY = """
MATCH (fc:FailureCase {case_id: $case_id})
MATCH (ps:ParseStrategy {strategy_id: $strategy_id})
MERGE (fc)-[:FIXED_BY]->(ps)
"""

LINK_SYNONYM_TO_GSE = """
MATCH (fs:FieldSynonym {name: $name})
MATCH (gse:GSE {gse_id: $gse_id})
MERGE (fs)-[:FOUND_IN]->(gse)
"""

LINK_GENE_MAPPING_TO_GSE = """
MATCH (gm:GeneMapping {source_id: $source_id, source_system: $source_system})
MATCH (gse:GSE {gse_id: $gse_id})
MERGE (gm)-[:USED_IN]->(gse)
"""

# === Retrieval Queries ===

FIND_FIELD_SYNONYM = """
MATCH (fs:FieldSynonym)
WHERE fs.name CONTAINS $fragment
   OR fs.canonical_name CONTAINS $fragment
RETURN fs.name, fs.canonical_name, fs.confidence, fs.category
ORDER BY fs.occurrence_count DESC, fs.confidence DESC
LIMIT 20
"""

FIND_GENE_MAPPING = """
MATCH (gm:GeneMapping)
WHERE gm.source_id = $source_id
  AND gm.source_system = $source_system
RETURN gm.target_symbol, gm.confidence, gm.species
ORDER BY gm.occurrence_count DESC
LIMIT 1
"""

FIND_SIMILAR_GSE = """
MATCH (gse:GSE)
WHERE gse.organism = $organism
  AND gse.assay_type = $assay_type
  AND gse.gse_id <> $gse_id
RETURN gse.gse_id, gse.title, gse.n_cells, gse.n_genes
ORDER BY gse.processed_at DESC
LIMIT 10
"""

# === Initialization ===

ALL_SETUP_QUERIES = CONSTRAINTS + INDEXES
