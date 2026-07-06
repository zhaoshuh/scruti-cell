"""Neo4j knowledge graph client for Scrut-Cell.

Provides connection management and high-level operations on the Neo4j
knowledge graph. For enterprise use with Neo4j; open-source version
falls back to SQLite-based local storage.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from scruticell.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from scruticell.knowledge.schema import ALL_SETUP_QUERIES

logger = logging.getLogger(__name__)

try:
    from neo4j import GraphDatabase, Session
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False
    logger.warning("neo4j package not installed. Knowledge graph unavailable.")


class KnowledgeGraph:
    """Neo4j-based knowledge graph for Scrut-Cell pipeline intelligence.

    Stores and retrieves parse strategies, field synonym mappings,
    gene ID cross-references, failure cases, and batch detection patterns.

    For open-source version without Neo4j, use LocalKnowledgeBase instead.
    """

    def __init__(
        self,
        uri: str = NEO4J_URI,
        user: str = NEO4J_USER,
        password: str = NEO4J_PASSWORD,
    ):
        self.uri = uri
        self.user = user
        self.password = password
        self._driver: Any = None
        self._initialized = False

    @property
    def available(self) -> bool:
        return NEO4J_AVAILABLE

    def connect(self) -> None:
        """Establish connection to Neo4j and initialize schema."""
        if not NEO4J_AVAILABLE:
            raise RuntimeError("neo4j package not installed")
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self.uri, auth=(self.user, self.password)
            )
            self._driver.verify_connectivity()
            logger.info("Connected to Neo4j at %s", self.uri)

    def initialize_schema(self) -> None:
        """Create constraints and indexes if they don't exist."""
        self.connect()
        with self._driver.session() as session:
            for query in ALL_SETUP_QUERIES:
                session.run(query)
        self._initialized = True
        logger.info("Neo4j schema initialized")

    def close(self) -> None:
        """Close the Neo4j driver connection."""
        if self._driver:
            self._driver.close()
            self._driver = None

    def _run(self, query: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Execute a Cypher query and return results."""
        if not self._driver:
            self.connect()
        params = params or {}
        with self._driver.session() as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    # === Field Synonyms ===

    def store_field_synonym(
        self,
        name: str,
        canonical_name: str,
        category: str = "",
        confidence: float = 1.0,
        gse_id: str = "",
    ) -> None:
        """Store a field name -> canonical name mapping."""
        from scruticell.knowledge.schema import CREATE_FIELD_SYNONYM
        self._run(CREATE_FIELD_SYNONYM, {
            "name": name,
            "canonical_name": canonical_name,
            "category": category,
            "confidence": confidence,
            "gse_id": gse_id,
        })

    def find_field_synonym(self, fragment: str) -> list[dict]:
        """Search for field synonyms matching a text fragment."""
        from scruticell.knowledge.schema import FIND_FIELD_SYNONYM
        return self._run(FIND_FIELD_SYNONYM, {"fragment": fragment})

    # === Gene Mappings ===

    def store_gene_mapping(
        self,
        source_id: str,
        source_system: str,
        target_symbol: str,
        confidence: float = 1.0,
        species: str = "Homo sapiens",
        gse_id: str = "",
    ) -> None:
        """Store a gene ID cross-reference mapping."""
        from scruticell.knowledge.schema import CREATE_GENE_MAPPING
        self._run(CREATE_GENE_MAPPING, {
            "source_id": source_id,
            "source_system": source_system,
            "target_symbol": target_symbol,
            "confidence": confidence,
            "species": species,
            "gse_id": gse_id,
        })

    def find_gene_mapping(
        self, source_id: str, source_system: str
    ) -> Optional[dict]:
        """Look up a gene ID mapping. Returns None if not found."""
        from scruticell.knowledge.schema import FIND_GENE_MAPPING
        results = self._run(FIND_GENE_MAPPING, {
            "source_id": source_id,
            "source_system": source_system,
        })
        return results[0] if results else None

    # === GSE Nodes ===

    def store_gse(
        self,
        gse_id: str,
        title: str = "",
        organism: str = "",
        assay_type: str = "",
        n_cells: int = 0,
        n_genes: int = 0,
        n_samples: int = 0,
        platform_ids: list[str] | None = None,
        pubmed_id: str = "",
    ) -> None:
        """Store a processed GSE record."""
        from scruticell.knowledge.schema import CREATE_GSE_NODE
        self._run(CREATE_GSE_NODE, {
            "gse_id": gse_id,
            "title": title,
            "organism": organism,
            "assay_type": assay_type,
            "n_cells": n_cells,
            "n_genes": n_genes,
            "n_samples": n_samples,
            "platform_ids": platform_ids or [],
            "pubmed_id": pubmed_id,
        })

    def find_similar_gse(
        self, organism: str, assay_type: str, gse_id: str = ""
    ) -> list[dict]:
        """Find similar previously processed GSE records."""
        from scruticell.knowledge.schema import FIND_SIMILAR_GSE
        return self._run(FIND_SIMILAR_GSE, {
            "organism": organism,
            "assay_type": assay_type,
            "gse_id": gse_id,
        })

    # === Parse Strategies ===

    def store_parse_strategy(
        self,
        strategy_id: str,
        gse_pattern: str,
        strategy_json: dict[str, Any],
        gse_id: str,
        success_count: int = 1,
        fail_count: int = 0,
        avg_confidence: float = 1.0,
    ) -> None:
        """Store a parse strategy linked to a GSE."""
        from scruticell.knowledge.schema import CREATE_PARSE_STRATEGY
        self._run(CREATE_PARSE_STRATEGY, {
            "strategy_id": strategy_id,
            "gse_pattern": gse_pattern,
            "strategy_json": strategy_json,
            "success_count": success_count,
            "fail_count": fail_count,
            "avg_confidence": avg_confidence,
            "gse_id": gse_id,
        })

    # === Failure Cases ===

    def store_failure_case(
        self,
        case_id: str,
        error_signature: str,
        gse_id: str,
        pipeline_stage: str = "",
        error_message: str = "",
        fix_strategy: str = "",
        fixed: bool = False,
        success_count: int = 0,
        fail_count: int = 0,
    ) -> None:
        """Store or update a failure case (upsert with counter increment)."""
        from scruticell.knowledge.schema import CREATE_FAILURE_CASE
        self._run(CREATE_FAILURE_CASE, {
            "case_id": case_id,
            "error_signature": error_signature,
            "gse_id": gse_id,
            "pipeline_stage": pipeline_stage,
            "error_message": error_message,
            "fix_strategy": fix_strategy,
            "fixed": fixed,
            "success_count": success_count,
            "fail_count": fail_count,
        })

    def find_similar_failures(
        self, error_signature: str, pipeline_stage: str = ""
    ) -> list[dict]:
        """Find similar past failures. Now returns all matching a stage."""
        from scruticell.knowledge.schema import FIND_SIMILAR_FAILURES
        return self._run(FIND_SIMILAR_FAILURES, {
            "pipeline_stage": pipeline_stage,
        })

    def find_all_by_stage(self, pipeline_stage: str) -> list[dict]:
        """Return all stored strategies for a given pipeline stage."""
        return self.find_similar_failures("", pipeline_stage)

    def link_failure_to_strategy(
        self, case_id: str, strategy_id: str
    ) -> None:
        """Link a failure case to the strategy that fixed it."""
        from scruticell.knowledge.schema import LINK_FAILURE_TO_STRATEGY
        self._run(LINK_FAILURE_TO_STRATEGY, {
            "case_id": case_id,
            "strategy_id": strategy_id,
        })

    # === Batch Patterns ===

    def store_batch_pattern(
        self,
        pattern_name: str,
        detection_rule: str,
        examples: list[str] | None = None,
        confidence: float = 1.0,
    ) -> None:
        """Store a batch detection pattern."""
        from scruticell.knowledge.schema import CREATE_BATCH_PATTERN
        self._run(CREATE_BATCH_PATTERN, {
            "pattern_name": pattern_name,
            "detection_rule": detection_rule,
            "examples": examples or [],
            "confidence": confidence,
        })

    # === Strategies (graph-decomposed storage) ===

    def store_strategy(
        self,
        strategy_id: str,
        plan_json: str = "{}",
        success: bool = True,
        confidence: float = 1.0,
        gse_id: str = "",
    ) -> None:
        """Store a processing strategy by decomposing it into graph nodes.

        The plan JSON is parsed and decomposed into linked nodes:
          ParseStrategy → BatchStrategy → MatrixStrategy
                       → ProcessingCode
                       → AssemblyCode
                       → FieldMappingStrategy

        Falls back to storing as a flat JSON blob if parsing fails.
        """
        from scruticell.knowledge.schema import (
            CREATE_ASSEMBLY_CODE,
            CREATE_BATCH_STRATEGY,
            CREATE_FIELD_MAPPING_STRATEGY,
            CREATE_MATRIX_STRATEGY,
            CREATE_PARSE_STRATEGY,
            CREATE_PROCESSING_CODE,
            DELETE_STRATEGY_CASCADE,
            LINK_BATCH_TO_MATRIX,
        )

        # Parse plan JSON
        try:
            plan = json.loads(plan_json) if isinstance(plan_json, str) else plan_json
        except (json.JSONDecodeError, TypeError):
            plan = {"plan": plan_json}

        if not isinstance(plan, dict):
            plan = {"plan": str(plan)}

        # ── Delete any existing strategy with this ID (cascade) ──
        try:
            self._run(DELETE_STRATEGY_CASCADE, {"strategy_id": strategy_id})
        except Exception:
            pass

        # ── Create ParseStrategy root node ──
        self._run(CREATE_PARSE_STRATEGY, {
            "strategy_id": strategy_id,
            "gse_pattern": strategy_id,
            "gene_id_system": plan.get("gene_id_system", "symbol"),
            "normalize_genes": plan.get("normalize_genes", True),
            "drop_duplicate_genes": plan.get("drop_duplicate_genes", True),
            "normalization": plan.get("normalization", "raw_counts"),
            "is_raw_counts": plan.get("is_raw_counts", True),
            "batch_separation_strategy": plan.get("batch_separation_strategy", ""),
            "strategy_notes": plan.get("strategy_notes", ""),
            "confidence": confidence,
            "success_count": 1 if success else 0,
            "fail_count": 0 if success else 1,
            "avg_confidence": confidence,
            "gse_id": gse_id,
        })

        # ── Create BatchStrategy nodes ──
        matrix_id_to_batch: dict[str, str] = {}
        for b in plan.get("batches", []) or []:
            batch_id = f"{strategy_id}_{b.get('gsm_id', '')}"
            self._run(CREATE_BATCH_STRATEGY, {
                "batch_id": batch_id,
                "gsm_id": b.get("gsm_id", ""),
                "batch_label": b.get("batch_label", ""),
                "cell_count": b.get("cell_count", 0),
                "barcode_prefix": b.get("barcode_prefix", ""),
                "note": b.get("note", ""),
                "reasoning": b.get("reasoning", ""),
                "defining_fields": b.get("defining_fields", []),
                "strategy_id": strategy_id,
            })
            for fk in b.get("file_keys", []) or []:
                matrix_id_to_batch[fk] = batch_id

        # ── Create MatrixStrategy nodes (ps-level matrices) ──
        for i, m in enumerate(plan.get("matrices", []) or []):
            matrix_id = f"{strategy_id}_m{i}"
            self._run(CREATE_MATRIX_STRATEGY, {
                "matrix_id": matrix_id,
                "file_key": m.get("file_key", ""),
                "format": m.get("format", ""),
                "orientation": m.get("orientation", "auto"),
                "transpose": m.get("transpose", False),
                "note": m.get("note", ""),
                "strategy_id": strategy_id,
            })

        # ── Link batches to their matrices ──
        for fk, batch_id in matrix_id_to_batch.items():
            for i, m in enumerate(plan.get("matrices", []) or []):
                if m.get("file_key") == fk:
                    matrix_id = f"{strategy_id}_m{i}"
                    try:
                        self._run(LINK_BATCH_TO_MATRIX, {
                            "batch_id": batch_id,
                            "matrix_id": matrix_id,
                        })
                    except Exception:
                        pass

        # ── Create ProcessingCode node ──
        proc_code = plan.get("processing_code", "")
        if proc_code:
            code_id = f"{strategy_id}_proc"
            self._run(CREATE_PROCESSING_CODE, {
                "code_id": code_id,
                "code": proc_code,
                "strategy_id": strategy_id,
            })

        # ── Create AssemblyCode node ──
        asm_code = plan.get("assembly_code", "")
        if asm_code:
            code_id = f"{strategy_id}_asm"
            self._run(CREATE_ASSEMBLY_CODE, {
                "code_id": code_id,
                "code": asm_code,
                "strategy_id": strategy_id,
            })

        # ── Create FieldMappingStrategy nodes ──
        for i, fm in enumerate(plan.get("field_mappings", []) or []):
            mapping_id = f"{strategy_id}_fm{i}"
            self._run(CREATE_FIELD_MAPPING_STRATEGY, {
                "mapping_id": mapping_id,
                "raw_field": fm.get("raw_field", ""),
                "canonical": fm.get("canonical", ""),
                "source": fm.get("source", ""),
                "note": fm.get("note", ""),
                "strategy_id": strategy_id,
            })

        logger.info("Stored strategy %s as graph with %d batches, %d matrices",
                     strategy_id,
                     len(plan.get("batches", []) or []),
                     len(plan.get("matrices", []) or []))

    def update_strategy_stats(
        self, strategy_id: str, success: bool = True
    ) -> None:
        """Update success/fail counters for an existing strategy."""
        from scruticell.knowledge.schema import UPDATE_STRATEGY_STATS
        self._run(UPDATE_STRATEGY_STATS, {
            "strategy_id": strategy_id,
            "increment_success": 1 if success else 0,
            "increment_fail": 0 if success else 1,
        })

    def find_similar_strategies(
        self, signature_hash: str, limit: int = 5
    ) -> list[dict]:
        """Find strategies matching a fingerprint signature hash.

        Reconstructs the full plan JSON from graph nodes for each matched strategy.
        """
        from scruticell.knowledge.schema import (
            FIND_STRATEGIES_BY_SIGNATURE,
            RECONSTRUCT_STRATEGY_PLAN,
        )

        results = self._run(FIND_STRATEGIES_BY_SIGNATURE, {
            "strategy_id": signature_hash,
            "prefix": signature_hash[:8],
            "limit": limit,
        })

        output: list[dict] = []
        seen: set[str] = set()

        for row in results:
            sid = row.get("strategy_id", "")
            if sid in seen:
                continue
            seen.add(sid)

            # Reconstruct full plan from graph
            graph_data = self._run(RECONSTRUCT_STRATEGY_PLAN, {
                "strategy_id": sid,
            })
            if graph_data:
                full_plan = _reconstruct_plan_from_graph(graph_data[0])
            else:
                full_plan = {}

            output.append({
                "strategy_id": sid,
                "plan_json": json.dumps(full_plan, ensure_ascii=False),
                "success_count": row.get("success_count", 0),
                "fail_count": row.get("fail_count", 0),
                "avg_confidence": row.get("avg_confidence", 0.0),
                "gse_id": "",
            })

        return output

    def find_best_strategies(
        self, organism: str, assay_type: str
    ) -> list[dict]:
        """Find highest-confidence parse strategies for similar datasets.

        Reconstructs full plan JSON from graph traversal for each match.
        """
        from scruticell.knowledge.schema import (
            FIND_BEST_STRATEGY,
            RECONSTRUCT_STRATEGY_PLAN,
        )

        results = self._run(FIND_BEST_STRATEGY, {
            "organism": organism,
            "assay_type": assay_type,
        })

        output: list[dict] = []
        seen: set[str] = set()

        for row in results:
            sid = row.get("strategy_id", "")
            if sid in seen:
                continue
            seen.add(sid)

            graph_data = self._run(RECONSTRUCT_STRATEGY_PLAN, {
                "strategy_id": sid,
            })
            if graph_data:
                full_plan = _reconstruct_plan_from_graph(graph_data[0])
            else:
                full_plan = {}

            output.append({
                "strategy_id": sid,
                "strategy_json": json.dumps(full_plan, ensure_ascii=False),
                "success_count": row.get("success_count", 0),
                "fail_count": row.get("fail_count", 0),
                "avg_confidence": row.get("avg_confidence", 0.0),
            })

        return output

    def clear_all(self) -> dict[str, int]:
        """Delete all strategy/run/plan data. Returns counts per table."""
        if not self._driver:
            self.connect()
        tables = [
            "gse_runs", "inspect_reports", "processing_plans",
            "field_mappings", "execution_steps", "batch_outputs",
            "strategies", "failure_cases",
        ]
        cypher_labels = {
            "gse_runs": "GseRun",
            "inspect_reports": "InspectReport",
            "processing_plans": "ProcessingPlan",
            "field_mappings": "FieldMapping",
            "execution_steps": "ExecutionStep",
            "batch_outputs": "BatchOutput",
            "strategies": "ParseStrategy",
            "failure_cases": "FailureCase",
        }
        counts: dict[str, int] = {}
        with self._driver.session() as session:
            for table, label in cypher_labels.items():
                count_result = session.run(
                    f"MATCH (n:{label}) RETURN count(n) AS c"
                )
                record = count_result.single()
                counts[table] = record["c"] if record else 0
                if counts[table] > 0:
                    session.run(f"MATCH (n:{label}) DETACH DELETE n")
        return counts

    def clear_failures(self) -> int:
        """Legacy: clear only failure_cases."""
        return self.clear_all()["failure_cases"]

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()


class LocalKnowledgeBase:
    """Local SQLite-based fallback for users without Neo4j.

    Provides the same API surface as KnowledgeGraph but stores data
    in a local SQLite database. Used by the open-source version.
    """

    def __init__(self, db_path: Optional[Path] = None):
        import sqlite3

        if db_path is None:
            from scruticell.config import DATA_DIR
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            db_path = DATA_DIR / "knowledge.db"

        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._initialize_tables()

    def _initialize_tables(self) -> None:
        """Create local SQLite tables mirroring Neo4j schema."""
        cursor = self._conn.cursor()
        cursor.executescript("""
            -- ▸ Seed tables (keep unchanged)
            CREATE TABLE IF NOT EXISTS field_synonyms (
                name TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                category TEXT DEFAULT '',
                confidence REAL DEFAULT 1.0,
                occurrence_count INTEGER DEFAULT 1,
                first_seen TEXT DEFAULT '',
                last_seen TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS gene_mappings (
                source_id TEXT NOT NULL,
                source_system TEXT NOT NULL,
                target_symbol TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                species TEXT DEFAULT 'Homo sapiens',
                occurrence_count INTEGER DEFAULT 1,
                PRIMARY KEY (source_id, source_system)
            );

            -- ▸ GSE runs (one row per execute invocation)
            CREATE TABLE IF NOT EXISTS gse_runs (
                run_id        TEXT PRIMARY KEY,
                gse_id        TEXT NOT NULL,
                version       TEXT DEFAULT '0.1.0',
                success       INTEGER DEFAULT 0,
                confidence    REAL DEFAULT 1.0,
                n_cells       INTEGER DEFAULT 0,
                n_genes       INTEGER DEFAULT 0,
                n_batches     INTEGER DEFAULT 0,
                started_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                finished_at   TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_runs_gse ON gse_runs(gse_id);

            -- ▸ Inspection reports (one per inspect run)
            CREATE TABLE IF NOT EXISTS inspect_reports (
                report_id           TEXT PRIMARY KEY,
                gse_id              TEXT NOT NULL,
                organism            TEXT DEFAULT '',
                matrix_formats      TEXT DEFAULT '',
                gsm_count           INTEGER DEFAULT 0,
                matrix_file_count   INTEGER DEFAULT 0,
                total_cells         INTEGER DEFAULT 0,
                total_genes         INTEGER DEFAULT 0,
                field_names         TEXT DEFAULT '',
                signature_hash      TEXT NOT NULL,
                raw_json            TEXT DEFAULT '{}',
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_reports_hash ON inspect_reports(signature_hash);
            CREATE INDEX IF NOT EXISTS idx_reports_organism ON inspect_reports(organism);

            -- ▸ Processing plans (one per plan run)
            CREATE TABLE IF NOT EXISTS processing_plans (
                plan_id             TEXT PRIMARY KEY,
                gse_id              TEXT NOT NULL,
                signature_hash      TEXT NOT NULL,
                plan_json           TEXT DEFAULT '{}',
                processing_code     TEXT DEFAULT '',
                assembly_code       TEXT DEFAULT '',
                plan_confidence     REAL DEFAULT 1.0,
                strategy_notes      TEXT DEFAULT '',
                kb_match_count      INTEGER DEFAULT 0,
                field_mapping_count INTEGER DEFAULT 0,
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_plans_hash ON processing_plans(signature_hash);
            CREATE INDEX IF NOT EXISTS idx_plans_gse ON processing_plans(gse_id);

            -- ▸ Field mappings (one per raw→canonical pair per plan)
            CREATE TABLE IF NOT EXISTS field_mappings (
                mapping_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id      TEXT NOT NULL,
                raw_field    TEXT NOT NULL,
                canonical    TEXT NOT NULL,
                source       TEXT DEFAULT '',
                note         TEXT DEFAULT '',
                FOREIGN KEY (plan_id) REFERENCES processing_plans(plan_id)
            );
            CREATE INDEX IF NOT EXISTS idx_mappings_canonical ON field_mappings(canonical);
            CREATE INDEX IF NOT EXISTS idx_mappings_plan ON field_mappings(plan_id);

            -- ▸ Execution steps (one per step per run)
            CREATE TABLE IF NOT EXISTS execution_steps (
                step_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       TEXT NOT NULL,
                step_name    TEXT NOT NULL,
                status       TEXT NOT NULL,
                step_index   INTEGER DEFAULT 0,
                details_json TEXT DEFAULT '{}',
                error        TEXT DEFAULT '',
                started_at   TEXT,
                finished_at  TEXT,
                FOREIGN KEY (run_id) REFERENCES gse_runs(run_id)
            );
            CREATE INDEX IF NOT EXISTS idx_steps_run ON execution_steps(run_id);

            -- ▸ Batch outputs (one per .h5ad file per run)
            CREATE TABLE IF NOT EXISTS batch_outputs (
                batch_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       TEXT NOT NULL,
                batch_label  TEXT NOT NULL,
                file_name    TEXT NOT NULL,
                n_cells      INTEGER DEFAULT 0,
                n_genes      INTEGER DEFAULT 0,
                metadata_json TEXT DEFAULT '{}',
                FOREIGN KEY (run_id) REFERENCES gse_runs(run_id)
            );
            CREATE INDEX IF NOT EXISTS idx_batches_run ON batch_outputs(run_id);

            -- ▸ Strategies (accumulated success/fail by signature)
            CREATE TABLE IF NOT EXISTS strategies (
                strategy_id     TEXT PRIMARY KEY,
                plan_json       TEXT DEFAULT '{}',
                success_count   INTEGER DEFAULT 0,
                fail_count      INTEGER DEFAULT 0,
                avg_confidence  REAL DEFAULT 0.0,
                first_seen_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen_at    TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- ▸ Failure cases (only actual failures)
            CREATE TABLE IF NOT EXISTS failure_cases (
                case_id         TEXT PRIMARY KEY,
                run_id          TEXT,
                error_signature TEXT NOT NULL,
                gse_id          TEXT DEFAULT '',
                pipeline_stage  TEXT DEFAULT '',
                error_message   TEXT DEFAULT '',
                fix_strategy    TEXT DEFAULT '',
                fixed           INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_failures_signature
                ON failure_cases(error_signature, pipeline_stage);

            -- ▸ Legacy table cleanup
            DROP TABLE IF EXISTS gse_records;
            DROP TABLE IF EXISTS parse_strategies;
        """)
        self._conn.commit()

    def connect(self) -> None:
        pass  # SQLite connects at init

    def close(self) -> None:
        self._conn.close()

    def store_field_synonym(
        self, name: str, canonical_name: str, category: str = "",
        confidence: float = 1.0, gse_id: str = ""
    ) -> None:
        self._conn.execute(
            """INSERT INTO field_synonyms (name, canonical_name, category, confidence, last_seen, first_seen)
               VALUES (?, ?, ?, ?, ?, COALESCE(?, ?))
               ON CONFLICT(name) DO UPDATE SET
               canonical_name = excluded.canonical_name,
               confidence = (confidence * occurrence_count + excluded.confidence) / (occurrence_count + 1),
               occurrence_count = occurrence_count + 1,
               last_seen = excluded.last_seen""",
            (name, canonical_name, category, confidence, gse_id, gse_id, gse_id),
        )
        self._conn.commit()

    def find_field_synonym(self, fragment: str) -> list[dict]:
        cursor = self._conn.execute(
            """SELECT name, canonical_name, confidence, category
               FROM field_synonyms
               WHERE name LIKE ? OR canonical_name LIKE ?
               ORDER BY occurrence_count DESC, confidence DESC
               LIMIT 20""",
            (f"%{fragment}%", f"%{fragment}%"),
        )
        return [dict(row) for row in cursor.fetchall()]

    def store_gene_mapping(
        self, source_id: str, source_system: str, target_symbol: str,
        confidence: float = 1.0, species: str = "Homo sapiens", gse_id: str = ""
    ) -> None:
        self._conn.execute(
            """INSERT INTO gene_mappings (source_id, source_system, target_symbol, confidence, species)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source_id, source_system) DO UPDATE SET
               target_symbol = excluded.target_symbol,
               confidence = (confidence * occurrence_count + excluded.confidence) / (occurrence_count + 1),
               occurrence_count = occurrence_count + 1""",
            (source_id, source_system, target_symbol, confidence, species),
        )
        self._conn.commit()

    def find_gene_mapping(self, source_id: str, source_system: str) -> dict | None:
        cursor = self._conn.execute(
            """SELECT target_symbol, confidence, species
               FROM gene_mappings
               WHERE source_id = ? AND source_system = ?
               ORDER BY occurrence_count DESC LIMIT 1""",
            (source_id, source_system),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    # === Inspect Reports ===

    def store_inspect_report(self, report_id: str, gse_id: str,
                              organism: str, matrix_formats: str,
                              gsm_count: int, matrix_file_count: int,
                              total_cells: int, total_genes: int,
                              field_names: str, signature_hash: str,
                              raw_json: str = "{}") -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO inspect_reports
               (report_id, gse_id, organism, matrix_formats, gsm_count,
                matrix_file_count, total_cells, total_genes, field_names,
                signature_hash, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (report_id, gse_id, organism, matrix_formats, gsm_count,
             matrix_file_count, total_cells, total_genes, field_names,
             signature_hash, raw_json),
        )
        self._conn.commit()

    def find_similar_reports(self, signature_hash: str, organism: str = "",
                              matrix_formats: str = "", gsm_count: int = 0,
                              limit: int = 10) -> list[dict]:
        cursor = self._conn.execute(
            """SELECT report_id, gse_id, organism, matrix_formats, gsm_count,
                      total_cells, total_genes, field_names, signature_hash, raw_json
               FROM inspect_reports
               WHERE signature_hash = ?
                  OR (organism = ? AND matrix_formats = ? AND gsm_count = ?)
               ORDER BY total_cells DESC
               LIMIT ?""",
            (signature_hash, organism, matrix_formats, gsm_count, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def find_reports_by_hash(self, signature_hash: str, limit: int = 10) -> list[dict]:
        cursor = self._conn.execute(
            """SELECT report_id, gse_id, signature_hash, gsm_count, raw_json
               FROM inspect_reports
               WHERE signature_hash = ?
               ORDER BY created_at DESC LIMIT ?""",
            (signature_hash, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    # === Processing Plans ===

    def store_plan(self, plan_id: str, gse_id: str, signature_hash: str,
                   plan_json: str = "{}", processing_code: str = "",
                   assembly_code: str = "", plan_confidence: float = 1.0,
                   strategy_notes: str = "", kb_match_count: int = 0,
                   field_mapping_count: int = 0) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO processing_plans
               (plan_id, gse_id, signature_hash, plan_json, processing_code,
                assembly_code, plan_confidence, strategy_notes,
                kb_match_count, field_mapping_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (plan_id, gse_id, signature_hash, plan_json, processing_code,
             assembly_code, plan_confidence, strategy_notes,
             kb_match_count, field_mapping_count),
        )
        self._conn.commit()

    def find_plans_by_hash(self, signature_hash: str, limit: int = 5) -> list[dict]:
        cursor = self._conn.execute(
            """SELECT plan_id, gse_id, plan_json, processing_code,
                      assembly_code, plan_confidence, strategy_notes,
                      kb_match_count, created_at
               FROM processing_plans
               WHERE signature_hash = ?
               ORDER BY created_at DESC LIMIT ?""",
            (signature_hash, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    # === Field Mappings ===

    def store_field_mappings(self, plan_id: str,
                              mappings: list[dict]) -> None:
        for m in mappings:
            self._conn.execute(
                """INSERT INTO field_mappings
                   (plan_id, raw_field, canonical, source, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (plan_id, m.get("raw_field", ""), m.get("canonical", ""),
                 m.get("source", ""), m.get("note", "")),
            )
        self._conn.commit()

    def find_mappings_by_canonical(self, canonical: str, limit: int = 20) -> list[dict]:
        cursor = self._conn.execute(
            """SELECT fm.raw_field, fm.canonical, fm.source, fm.note, p.gse_id
               FROM field_mappings fm
               JOIN processing_plans p ON fm.plan_id = p.plan_id
               WHERE fm.canonical = ?
               ORDER BY p.created_at DESC LIMIT ?""",
            (canonical, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    # === GSE Runs ===

    def store_run(self, run_id: str, gse_id: str, success: bool = False,
                  confidence: float = 1.0, n_cells: int = 0,
                  n_genes: int = 0, n_batches: int = 0) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO gse_runs
               (run_id, gse_id, success, confidence, n_cells, n_genes, n_batches, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (run_id, gse_id, int(success), confidence, n_cells, n_genes, n_batches),
        )
        self._conn.commit()

    # === Execution Steps ===

    def store_execution_step(self, run_id: str, step_name: str, status: str,
                              step_index: int = 0, details_json: str = "{}",
                              error: str = "", started_at: str = "",
                              finished_at: str = "") -> None:
        self._conn.execute(
            """INSERT INTO execution_steps
               (run_id, step_name, status, step_index, details_json, error, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, step_name, status, step_index, details_json, error, started_at, finished_at),
        )
        self._conn.commit()

    def find_steps_by_run(self, run_id: str) -> list[dict]:
        cursor = self._conn.execute(
            """SELECT step_name, status, step_index, details_json, error, started_at, finished_at
               FROM execution_steps
               WHERE run_id = ?
               ORDER BY step_index ASC""",
            (run_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    # === Batch Outputs ===

    def store_batch_output(self, run_id: str, batch_label: str,
                            file_name: str, n_cells: int, n_genes: int,
                            metadata_json: str = "{}") -> None:
        self._conn.execute(
            """INSERT INTO batch_outputs
               (run_id, batch_label, file_name, n_cells, n_genes, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, batch_label, file_name, n_cells, n_genes, metadata_json),
        )
        self._conn.commit()

    # === Strategies ===

    def store_strategy(self, strategy_id: str, plan_json: str = "{}",
                        success: bool = True, confidence: float = 1.0) -> None:
        existing = self._conn.execute(
            "SELECT success_count, fail_count, avg_confidence FROM strategies WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
        sc = 0
        fc = 0
        if existing:
            sc = existing["success_count"]
            fc = existing["fail_count"]
        if success:
            sc += 1
        else:
            fc += 1
        total = sc + fc
        avg = ((confidence + (existing["avg_confidence"] or 0) * (total - 1)) / total) if total > 1 else confidence
        self._conn.execute(
            """INSERT OR REPLACE INTO strategies
               (strategy_id, plan_json, success_count, fail_count, avg_confidence, last_seen_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (strategy_id, plan_json, sc, fc, avg),
        )
        self._conn.commit()

    def find_similar_strategies(self, signature_hash: str, limit: int = 5) -> list[dict]:
        cursor = self._conn.execute(
            """SELECT strategy_id, plan_json, success_count, fail_count, avg_confidence
               FROM strategies
               WHERE strategy_id = ? OR strategy_id LIKE ?
               ORDER BY success_count DESC, last_seen_at DESC
               LIMIT ?""",
            (signature_hash, signature_hash[:8] + "%", limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def update_strategy_stats(self, strategy_id: str, success: bool = True) -> None:
        existing = self._conn.execute(
            "SELECT success_count, fail_count FROM strategies WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
        if existing:
            sc = existing["success_count"] + (1 if success else 0)
            fc = existing["fail_count"] + (0 if success else 1)
        else:
            sc = 1 if success else 0
            fc = 0 if success else 1
        self._conn.execute(
            """INSERT OR REPLACE INTO strategies
               (strategy_id, success_count, fail_count, last_seen_at)
               VALUES (?, ?, ?, datetime('now'))""",
            (strategy_id, sc, fc),
        )
        self._conn.commit()

    # === Failure Cases ===

    def store_failure_case(self, case_id: str, error_signature: str, gse_id: str,
                            pipeline_stage: str = "", error_message: str = "",
                            fix_strategy: str = "", fixed: bool = False,
                            run_id: str = "") -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO failure_cases
               (case_id, run_id, error_signature, gse_id, pipeline_stage,
                error_message, fix_strategy, fixed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (case_id, run_id, error_signature, gse_id, pipeline_stage,
             error_message, fix_strategy, int(fixed)),
        )
        self._conn.commit()

    def find_similar_failures(self, error_signature: str,
                               pipeline_stage: str = "") -> list[dict]:
        cursor = self._conn.execute(
            """SELECT case_id, fix_strategy, fixed, error_message, gse_id
               FROM failure_cases
               WHERE error_signature = ? AND pipeline_stage = ?
               ORDER BY created_at DESC LIMIT 5""",
            (error_signature, pipeline_stage),
        )
        return [dict(row) for row in cursor.fetchall()]

    def find_all_by_stage(self, pipeline_stage: str) -> list[dict]:
        """Return all failure cases for a given pipeline stage."""
        cursor = self._conn.execute(
            """SELECT case_id, fix_strategy, fixed, error_message, gse_id
               FROM failure_cases
               WHERE pipeline_stage = ?
               ORDER BY created_at DESC""",
            (pipeline_stage,),
        )
        return [dict(row) for row in cursor.fetchall()]

    # === Clear All ===

    def clear_all(self) -> dict[str, int]:
        """Delete all strategy/run/plan data. Returns counts per table.
        Does NOT clear field_synonyms or gene_mappings."""
        tables = [
            "failure_cases", "strategies", "execution_steps",
            "batch_outputs", "field_mappings", "processing_plans",
            "inspect_reports", "gse_runs",
        ]
        counts: dict[str, int] = {}
        for table in tables:
            try:
                cursor = self._conn.execute(f"DELETE FROM {table}")
                counts[table] = cursor.rowcount
            except Exception:
                counts[table] = 0
        self._conn.commit()
        return counts

    def clear_failures(self) -> int:
        """Legacy: clear only failure_cases."""
        return self.clear_all()["failure_cases"]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ────────────────────────────────────────────────────────────────────
# Helper: reconstruct full plan JSON from graph traversal result
# ────────────────────────────────────────────────────────────────────

def _reconstruct_plan_from_graph(graph_row: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a ProcessingPlan-compatible JSON dict from Neo4j graph nodes.

    Args:
        graph_row: Result from RECONSTRUCT_STRATEGY_PLAN query.

    Returns:
        Dict suitable for ProcessingPlan.from_llm_response().
    """
    ps = graph_row.get("ps", {})
    plan: dict[str, Any] = {
        "gene_id_system": ps.get("gene_id_system", "symbol"),
        "normalize_genes": ps.get("normalize_genes", True),
        "drop_duplicate_genes": ps.get("drop_duplicate_genes", True),
        "normalization": ps.get("normalization", "raw_counts"),
        "is_raw_counts": ps.get("is_raw_counts", True),
        "batch_separation_strategy": ps.get("batch_separation_strategy", ""),
        "strategy_notes": ps.get("strategy_notes", ""),
        "confidence": ps.get("confidence", 0.0),
        "matrices": [],
        "batches": [],
        "field_mappings": [],
        "processing_code": "",
        "assembly_code": "",
    }

    # Matrices from ParseStrategy-level USES_MATRIX
    matrices = graph_row.get("matrices", []) or []
    batch_matrices = graph_row.get("batch_matrices", []) or []

    # Build batch -> file_keys mapping from BatchStrategy -> MatrixStrategy links
    all_matrix_nodes = set()
    for bm in (batch_matrices or []):
        if bm:
            all_matrix_nodes.add(bm.get("matrix_id", ""))

    # ParseStrategy-level matrices
    for m in (matrices or []):
        if m is None:
            continue
        all_matrix_nodes.add(m.get("matrix_id", ""))
        plan["matrices"].append({
            "file_key": m.get("file_key", ""),
            "format": m.get("format", ""),
            "orientation": m.get("orientation", "auto"),
            "transpose": m.get("transpose", False),
            "note": m.get("note", ""),
        })

    # Batches
    batches = graph_row.get("batches", []) or []
    for b in (batches or []):
        if b is None:
            continue
        batch_entry = {
            "gsm_id": b.get("gsm_id", ""),
            "batch_label": b.get("batch_label", ""),
            "cell_count": b.get("cell_count", 0),
            "barcode_prefix": b.get("barcode_prefix", ""),
            "note": b.get("note", ""),
            "reasoning": b.get("reasoning", ""),
            "defining_fields": b.get("defining_fields", []),
            "file_keys": [],
        }
        plan["batches"].append(batch_entry)

    # TODO: batch-file_keys linking requires a separate query;
    # for now file_keys come from matrices list which are shared

    # Processing code
    proc_codes = graph_row.get("processing_codes", []) or []
    for pc in (proc_codes or []):
        if pc and pc.get("code"):
            plan["processing_code"] = pc.get("code", "")
            break

    # Assembly code
    asm_codes = graph_row.get("assembly_codes", []) or []
    for ac in (asm_codes or []):
        if ac and ac.get("code"):
            plan["assembly_code"] = ac.get("code", "")
            break

    # Field mappings
    mappings = graph_row.get("field_mappings", []) or []
    for fm in (mappings or []):
        if fm is None:
            continue
        plan["field_mappings"].append({
            "raw_field": fm.get("raw_field", ""),
            "canonical": fm.get("canonical", ""),
            "source": fm.get("source", ""),
            "note": fm.get("note", ""),
        })

    return plan
