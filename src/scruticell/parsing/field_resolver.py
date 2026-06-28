"""Field name resolver: maps raw GEO field names to canonical forms.

Uses the knowledge graph (Neo4j or local SQLite) for accumulated mappings,
with fallback to built-in seed data and LLM-based inference for novel fields.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from scruticell.knowledge.field_synonyms import (
    batch_resolve_field_names,
    resolve_field_name,
    seed_knowledge_graph,
)
from scruticell.knowledge.graph import KnowledgeGraph, LocalKnowledgeBase

logger = logging.getLogger(__name__)


class FieldResolver:
    """Resolves raw GEO field names to canonical standardized names.

    Three-tier resolution:
    1. Built-in seed data (config/field_synonyms.json)
    2. Accumulated knowledge graph lookups
    3. LLM-based inference for completely novel fields (future)
    """

    def __init__(
        self,
        kb: Optional[KnowledgeGraph | LocalKnowledgeBase] = None,
    ):
        self.kb = kb

    def resolve(self, raw_field: str) -> str:
        """Map a single raw field name to canonical form."""
        return resolve_field_name(raw_field, self.kb)

    def resolve_batch(self, raw_fields: list[str]) -> dict[str, str]:
        """Map multiple raw field names to canonical forms."""
        return batch_resolve_field_names(raw_fields, self.kb)

    def learn_new_synonym(
        self, raw_name: str, canonical_name: str, gse_id: str = ""
    ) -> None:
        """Teach the resolver a new field mapping (stored in KB)."""
        if self.kb:
            self.kb.store_field_synonym(
                name=raw_name, canonical_name=canonical_name, gse_id=gse_id
            )
