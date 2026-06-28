"""Enterprise license management for Scrut-Cell.

Provides feature gating between open-source (MIT) and enterprise versions:
- Open-source: LocalKnowledgeBase (SQLite), no Neo4j, no LLM features
- Enterprise: KnowledgeGraph (Neo4j), LLM reflexion, audit SLA, priority support

License validation uses a simple key-based system. Enterprise features
are unlocked when a valid SCRUTICELL_LICENSE_KEY is set.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_LICENSE_KEY_ENV = "SCRUTICELL_LICENSE_KEY"
_LICENSE_SALT = "scruticell_enterprise_2026"


class LicenseManager:
    """Manages feature availability based on license tier."""

    def __init__(self):
        self._key = os.environ.get(_LICENSE_KEY_ENV, "")
        self._features = self._resolve_features()

    @property
    def tier(self) -> str:
        """Current license tier: 'oss' or 'enterprise'."""
        if self._validate_key(self._key):
            return "enterprise"
        return "oss"

    @property
    def neo4j_enabled(self) -> bool:
        return self._features.get("neo4j", False)

    @property
    def llm_enabled(self) -> bool:
        return self._features.get("llm", False)

    @property
    def audit_enabled(self) -> bool:
        return self._features.get("audit", True)

    @property
    def reflexion_enabled(self) -> bool:
        return self._features.get("reflexion", True)

    @property
    def mcts_enabled(self) -> bool:
        return self._features.get("mcts", self.tier == "enterprise")

    def check(self, feature: str) -> bool:
        """Check if a specific feature is enabled."""
        return self._features.get(feature, False)

    def _resolve_features(self) -> dict[str, bool]:
        is_ent = self.tier == "enterprise"
        return {
            "neo4j": is_ent,
            "llm": is_ent,
            "audit": True,
            "reflexion": True,
            "mcts": is_ent,
        }

    def _validate_key(self, key: str) -> bool:
        if not key or len(key) < 16:
            return False
        try:
            parts = key.split("-")
            if len(parts) < 4:
                return False
            expected = hashlib.sha256(
                f"{_LICENSE_SALT}:{parts[0]}".encode()
            ).hexdigest()[:12]
            return parts[1] == expected
        except Exception:
            return False

    def generate_license_key(self, client_id: str) -> str:
        """Generate an enterprise license key for a client.

        This is for license provisioning; not used in normal operation.
        """
        signature = hashlib.sha256(
            f"{_LICENSE_SALT}:{client_id}".encode()
        ).hexdigest()[:12]
        return f"{client_id}-{signature}-ENT-SCRUTICELL"


_license_manager: LicenseManager | None = None


def get_license() -> LicenseManager:
    """Get the global license manager instance."""
    global _license_manager
    if _license_manager is None:
        _license_manager = LicenseManager()
    return _license_manager


def is_enterprise() -> bool:
    return get_license().tier == "enterprise"
