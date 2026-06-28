"""Neo4j knowledge graph client for Scrut-Cell.

Provides connection management and high-level operations on the Neo4j
knowledge graph. For enterprise use with Neo4j; open-source version
falls back to SQLite-based local storage.
"""

from __future__ import annotations

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

    def find_best_strategies(
        self, organism: str, assay_type: str
    ) -> list[dict]:
        """Find highest-confidence parse strategies for similar datasets."""
        from scruticell.knowledge.schema import FIND_BEST_STRATEGY
        return self._run(FIND_BEST_STRATEGY, {
            "organism": organism,
            "assay_type": assay_type,
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
            CREATE TABLE IF NOT EXISTS gse_records (
                gse_id TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                organism TEXT DEFAULT '',
                assay_type TEXT DEFAULT '',
                n_cells INTEGER DEFAULT 0,
                n_genes INTEGER DEFAULT 0,
                n_samples INTEGER DEFAULT 0,
                pubmed_id TEXT DEFAULT '',
                processed_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS parse_strategies (
                strategy_id TEXT PRIMARY KEY,
                gse_id TEXT NOT NULL,
                gse_pattern TEXT DEFAULT '',
                strategy_json TEXT DEFAULT '{}',
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                avg_confidence REAL DEFAULT 1.0
            );
            CREATE TABLE IF NOT EXISTS failure_cases (
                case_id TEXT PRIMARY KEY,
                error_signature TEXT NOT NULL,
                gse_id TEXT DEFAULT '',
                pipeline_stage TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                fix_strategy TEXT DEFAULT '',
                fixed INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_failures_signature
                ON failure_cases(error_signature, pipeline_stage);
            CREATE INDEX IF NOT EXISTS idx_synonyms_canonical
                ON field_synonyms(canonical_name);
            CREATE INDEX IF NOT EXISTS idx_gene_mappings_symbol
                ON gene_mappings(target_symbol);
            CREATE INDEX IF NOT EXISTS idx_strategies_organism
                ON gse_records(organism, assay_type);
        """)
        # Migration: add columns if they don't exist
        try:
            cursor.execute("ALTER TABLE failure_cases ADD COLUMN success_count INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE failure_cases ADD COLUMN fail_count INTEGER DEFAULT 0")
        except Exception:
            pass
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

    def store_gse(self, gse_id: str, **kwargs) -> None:
        fields = ["gse_id", "title", "organism", "assay_type",
                   "n_cells", "n_genes", "n_samples", "pubmed_id"]
        values = [gse_id] + [kwargs.get(f, "") for f in fields[1:]]
        self._conn.execute(
            f"""INSERT INTO gse_records ({','.join(fields)})
                VALUES ({','.join(['?']*len(fields))})
                ON CONFLICT(gse_id) DO UPDATE SET
                title=excluded.title, organism=excluded.organism,
                assay_type=excluded.assay_type, n_cells=excluded.n_cells,
                n_genes=excluded.n_genes, n_samples=excluded.n_samples,
                pubmed_id=excluded.pubmed_id""",
            values,
        )
        self._conn.commit()

    def find_similar_gse(self, organism: str, assay_type: str, gse_id: str = "") -> list[dict]:
        cursor = self._conn.execute(
            """SELECT gse_id, title, n_cells, n_genes
               FROM gse_records
               WHERE organism = ? AND assay_type = ? AND gse_id <> ?
               ORDER BY processed_at DESC LIMIT 10""",
            (organism, assay_type, gse_id),
        )
        return [dict(row) for row in cursor.fetchall()]

    def store_failure_case(self, case_id: str, error_signature: str, gse_id: str,
                            pipeline_stage: str = "", error_message: str = "",
                            fix_strategy: str = "", fixed: bool = False,
                            success_count: int = 0, fail_count: int = 0) -> None:
        # Upsert with incremental counters for existing records
        existing = self._conn.execute(
            "SELECT success_count, fail_count FROM failure_cases WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        if existing:
            success_count += existing["success_count"]
            fail_count += existing["fail_count"]
        self._conn.execute(
            """INSERT OR REPLACE INTO failure_cases
               (case_id, error_signature, gse_id, pipeline_stage,
                error_message, fix_strategy, fixed, success_count, fail_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (case_id, error_signature, gse_id, pipeline_stage,
             error_message, fix_strategy, int(fixed), success_count, fail_count),
        )
        self._conn.commit()


    def find_similar_failures(self, error_signature: str, pipeline_stage: str = "") -> list[dict]:
        cursor = self._conn.execute(
            """SELECT case_id, fix_strategy, fixed, error_message, gse_id,
                      COALESCE(success_count, 0) AS success_count,
                      COALESCE(fail_count, 0) AS fail_count
               FROM failure_cases
               WHERE error_signature = ? AND pipeline_stage = ?
               ORDER BY success_count DESC, created_at DESC LIMIT 5""",
            (error_signature, pipeline_stage),
        )
        return [dict(row) for row in cursor.fetchall()]

    def find_all_by_stage(self, pipeline_stage: str) -> list[dict]:
        """Return all stored strategies for a given pipeline stage."""
        cursor = self._conn.execute(
            """SELECT case_id, fix_strategy, fixed, error_message, gse_id,
                      COALESCE(success_count, 0) AS success_count,
                      COALESCE(fail_count, 0) AS fail_count
               FROM failure_cases
               WHERE pipeline_stage = ?
               ORDER BY success_count DESC, created_at DESC""",
            (pipeline_stage,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def clear_failures(self) -> int:
        """Delete all failure case records. Returns count of deleted rows."""
        cursor = self._conn.execute("DELETE FROM failure_cases")
        self._conn.commit()
        return cursor.rowcount

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
