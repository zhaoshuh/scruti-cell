# Scrut-Cell Database Configuration & Management

## Architecture

Scrut-Cell uses a **dual-backend knowledge base** with automatic fallback:

```
Scrut-Cell CLI
     │
     ├── Open-source (default) → SQLite (output/knowledge.db)
     │
     └── Enterprise (SCRUTICELL_LICENSE set) → Neo4j 5.x
           │
           └── Fallback: SQLite (if Neo4j unavailable)
```

Both backends expose the **same API surface**, so code never needs to know which backend is active.

---

## SQLite Backend (Default, Zero Configuration)

### Location

```
output/knowledge.db
```

Path controlled by `SCRUTICELL_DATA_DIR` environment variable (default: `output/`).

### Schema (10 tables)

| Table | Purpose | Writes By | Reads By |
|-------|---------|-----------|----------|
| `field_synonyms` | Field name → canonical mappings (77 seed entries from `config/field_synonyms.json`) | Auto-bootstrap on first run | `FieldResolver` during parse |
| `gene_mappings` | Gene ID cross-references (symbol↔ensembl↔entrez) | `alignment/genes.py` | `alignment/genes.py` |
| `inspect_reports` | GseReport fingerprints: organism, matrix formats, GSM counts, field names, signature hash | `plan` command | KB similarity matching |
| `processing_plans` | Full ProcessingPlan JSON + processing_code + assembly_code | `plan` command, `strategy.matcher` | KB similarity matching |
| `field_mappings` | Per-plan raw→canonical field mappings (one row per mapping) | `plan` command | Cross-dataset field analysis |
| `strategies` | Accumulated strategy stats by signature hash (success_count, fail_count, avg_confidence) | `plan` / `execute` completion | `StrategyMatcher.find_similar()` |
| `gse_runs` | One row per `execute` invocation (gse_id, success, n_cells/genes/batches) | `executor.py` | Run history |
| `execution_steps` | Per-step trace (step_name, status, details, errors) | `executor.py` | Debug/pipeline analysis |
| `batch_outputs` | Per-batch metadata (file_name, n_cells, n_genes, obs columns) | `executor.py` | Per-dataset metadata summary |
| `failure_cases` | Only actual failures (error_signature, pipeline_stage, error_message) | Reflexion / validate nodes | Reflexion agent |

### Connection

SQLite connects automatically at `LocalKnowledgeBase.__init__()`. No config needed. The database file is created on first access.

```
Initial state:      output/knowledge.db does not exist
First CLI command:  output/knowledge.db created with full schema
Subsequent runs:    Schema checked via CREATE TABLE IF NOT EXISTS
```

### Management Commands

```bash
# View all stored data
scruticell config                          # Shows DATA_DIR path

# Clear all strategy/run/plan data (keeps field_synonyms + gene_mappings)
scruticell db-clear

# Manually delete everything
rm output/knowledge.db
```

---

## Neo4j Backend (Enterprise)

### Prerequisites

| Software | Version | Notes |
|----------|---------|-------|
| Neo4j | 5.x Community or Enterprise | Download from https://neo4j.com/download/ |
| Java | 17+ | Required by Neo4j |

### Installation

```bash
# 1. Download Neo4j and extract
# 2. Configure data directory (avoid C: drive on Windows)
#    Edit conf/neo4j.conf:
server.directories.data=G:\\scruticell\\neo4j\\data

# 3. Start Neo4j
bin\neo4j console          # Foreground
bin\neo4j install-service  # Windows service
bin\neo4j start            # Start service

# 4. Verify (default credentials: neo4j / scruticell)
#    Open http://localhost:7474 in browser
```

### Activation

```bash
# Set environment variable (must contain "ENT-SCRUTICELL" suffix)
export SCRUTICELL_LICENSE="your-org-a1b2c3d4-ENT-SCRUTICELL"
```

The code checks via `scruticell.license.is_enterprise()` → auto-switches to `KnowledgeGraph` backend.

### Connection Config

| Environment Variable | Default | Purpose |
|---------------------|---------|---------|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URL |
| `NEO4J_USER` | `neo4j` | Username |
| `NEO4J_PASSWORD` | `scruticell` | Password |

All three can be viewed via `scruticell config`.

### Graph Schema

```
Nodes:
  GSE {gse_id, title, organism, assay_type, n_cells, n_genes, n_samples}
  ParseStrategy {strategy_id, gse_pattern, strategy_json, success_count, fail_count}
  FailureCase {case_id, error_signature, pipeline_stage, error_message, fix_strategy}
  FieldSynonym {name, canonical_name, category, confidence}
  GeneMapping {source_id, source_system, target_symbol, confidence, species}
  BatchPattern {pattern_name, detection_rule, examples, confidence}

Relationships:
  (GSE)-[:HAS_STRATEGY]->(ParseStrategy)
  (GSE)-[:HAS_FAILURE]->(FailureCase)
  (FailureCase)-[:FIXED_BY]->(ParseStrategy)
  (FieldSynonym)-[:FOUND_IN]->(GSE)
  (GeneMapping)-[:USED_IN]->(GSE)
```

### Useful Cypher Queries

```cypher
// Find all strategies for a specific organism
MATCH (gse:GSE {organism: "Homo sapiens"})-[:HAS_STRATEGY]->(ps:ParseStrategy)
RETURN gse.gse_id, ps.strategy_id, ps.success_count, ps.fail_count
ORDER BY ps.success_count DESC

// Find failures that have known fixes
MATCH (fc:FailureCase)-[:FIXED_BY]->(ps:ParseStrategy)
RETURN fc.case_id, fc.error_message, ps.strategy_id
LIMIT 20

// Which field synonyms are most used?
MATCH (fs:FieldSynonym)-[:FOUND_IN]->(gse:GSE)
RETURN fs.name, fs.canonical_name, COUNT(gse) AS usage_count
ORDER BY usage_count DESC LIMIT 20

// Full trace for a specific GSE
MATCH (gse:GSE {gse_id: "GSE334035"})
OPTIONAL MATCH (gse)-[:HAS_STRATEGY]->(ps)
OPTIONAL MATCH (gse)-[:HAS_FAILURE]->(fc)
RETURN gse, ps, fc
```

### Management

```bash
# Reset Neo4j completely (via Neo4j Browser or cypher-shell)
MATCH (n) DETACH DELETE n
```

---

## Data Flow: What Gets Stored When

```
scruticell inspect GSE12345
  → NOT stored in KB (inspect output is file-based: output/GSE12345/inspect.json)

scruticell plan GSE12345
  → inspect_reports:    organism, matrix_formats, gsm_count, signature_hash
  → processing_plans:   full plan_json, processing_code, assembly_code
  → field_mappings:     one row per raw_field→canonical mapping
  → strategies:         upserted with success_count

scruticell execute GSE12345
  → gse_runs:           run_id, success, n_cells, n_genes, n_batches
  → execution_steps:    one row per pipeline step (download, parse, read_matrix, ...)
  → batch_outputs:      one row per .h5ad written (cells, genes, obs metadata)
  → strategies:         success_count incremented if execution succeeded
```

---

## Best Practices

1. **Clear KB before batch testing** — Run `scruticell db-clear` to start fresh with a known state.
2. **Backup before schema changes** — The database auto-migrates. If testing schema changes, delete `output/knowledge.db` and let it recreate.
3. **Neo4j for production** — SQLite is fine for development. For multi-user or long-running deployments, use Neo4j (supports concurrent reads, graph traversal, better scalability).
4. **Monitor DB size** — `output/knowledge.db` grows with each run. After batch testing 100 datasets, expect ~10-50 MB. Purge with `db-clear` periodically.
5. **Environment isolation** — Use `SCRUTICELL_DATA_DIR` to isolate KB per project or environment.
