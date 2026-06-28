"""Neo4j knowledge graph schema definition.

The knowledge graph stores accumulated parsing strategies, field synonym mappings,
gene ID cross-references, failure cases, and batch detection patterns.
This is the long-term memory substrate for the Reflexion agent.
"""

from __future__ import annotations

# === Constraints and Indexes ===

CONSTRAINTS: list[str] = [
    # Field Synonym uniqueness
    "CREATE CONSTRAINT IF NOT EXISTS FOR (fs:FieldSynonym) REQUIRE fs.name IS UNIQUE",
    # Gene Mapping uniqueness (source_id + source_system) - composite unique for Community Edition
    "CREATE CONSTRAINT IF NOT EXISTS FOR (gm:GeneMapping) REQUIRE gm.source_id IS UNIQUE",
    # GSE uniqueness
    "CREATE CONSTRAINT IF NOT EXISTS FOR (gse:GSE) REQUIRE gse.gse_id IS UNIQUE",
    # Parse Strategy uniqueness
    "CREATE CONSTRAINT IF NOT EXISTS FOR (ps:ParseStrategy) REQUIRE ps.strategy_id IS UNIQUE",
    # Failure Case uniqueness
    "CREATE CONSTRAINT IF NOT EXISTS FOR (fc:FailureCase) REQUIRE fc.case_id IS UNIQUE",
    # Batch Pattern uniqueness
    "CREATE CONSTRAINT IF NOT EXISTS FOR (bp:BatchPattern) REQUIRE bp.pattern_name IS UNIQUE",
]

INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS FOR (fs:FieldSynonym) ON (fs.canonical_name)",
    "CREATE INDEX IF NOT EXISTS FOR (gm:GeneMapping) ON (gm.target_symbol)",
    "CREATE INDEX IF NOT EXISTS FOR (gse:GSE) ON (gse.organism)",
    "CREATE INDEX IF NOT EXISTS FOR (gse:GSE) ON (gse.assay_type)",
    "CREATE INDEX IF NOT EXISTS FOR (ps:ParseStrategy) ON (ps.success_count)",
    "CREATE INDEX IF NOT EXISTS FOR (fc:FailureCase) ON (fc.error_signature)",
    "CREATE INDEX IF NOT EXISTS FOR (bp:BatchPattern) ON (bp.detection_rule)",
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

CREATE_PARSE_STRATEGY = """
CREATE (ps:ParseStrategy {
    strategy_id: $strategy_id,
    gse_pattern: $gse_pattern,
    strategy_json: $strategy_json,
    success_count: $success_count,
    fail_count: $fail_count,
    avg_confidence: $avg_confidence,
    created_at: datetime()
})
WITH ps
MATCH (gse:GSE {gse_id: $gse_id})
CREATE (gse)-[:HAS_STRATEGY]->(ps)
"""

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
RETURN ps.strategy_id, ps.strategy_json, ps.success_count,
       ps.fail_count, ps.avg_confidence
ORDER BY ps.success_count DESC LIMIT 10
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
