"""Field synonym management for the knowledge graph.

Loads seed data from config/field_synonyms.json and provides
lookup/update operations against the knowledge graph backend.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from scruticell.config import CONFIG_DIR
from scruticell.knowledge.graph import KnowledgeGraph, LocalKnowledgeBase


def load_seed_synonyms() -> list[dict[str, str]]:
    """Load the built-in field synonym mappings from config file."""
    seed_file = CONFIG_DIR / "field_synonyms.json"
    if seed_file.exists():
        return json.loads(seed_file.read_text(encoding="utf-8"))
    return []


def seed_knowledge_graph(kb: KnowledgeGraph | LocalKnowledgeBase) -> int:
    """Populate knowledge graph with built-in field synonym mappings.

    Returns the number of entries seeded.
    """
    synonyms = load_seed_synonyms()
    for entry in synonyms:
        kb.store_field_synonym(
            name=entry["name"],
            canonical_name=entry["canonical_name"],
            category=entry.get("category", ""),
            confidence=1.0,
            gse_id="seed",
        )
    return len(synonyms)


def resolve_field_name(
    raw_name: str, kb: Optional[KnowledgeGraph | LocalKnowledgeBase] = None
) -> str:
    """Map a raw GEO field name to its canonical form.

    First checks exact match in the built-in seed data, then falls
    back to knowledge graph fuzzy search if available.

    Returns the canonical name if found, otherwise returns the original.
    """
    # Fast path: check seed data
    seed = load_seed_synonyms()
    for entry in seed:
        if entry["name"].lower() == raw_name.lower().strip():
            return entry["canonical_name"]

    # Slow path: knowledge graph lookup
    if kb:
        results = kb.find_field_synonym(raw_name.lower().strip())
        if results:
            return results[0].get("canonical_name", raw_name)

    return raw_name


def batch_resolve_field_names(
    raw_names: list[str], kb: Optional[KnowledgeGraph | LocalKnowledgeBase] = None
) -> dict[str, str]:
    """Map multiple raw GEO field names to canonical forms."""
    return {name: resolve_field_name(name, kb) for name in raw_names}
