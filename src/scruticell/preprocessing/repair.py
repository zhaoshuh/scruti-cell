"""Repair loop for LLM-generated code when verification fails.

Provides run_with_repair() — a generic retry-with-error-context template.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from scruticell.preprocessing.models import VerificationReport

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2


def run_with_repair(
    code_gen_fn: Callable[[str], Any],
    verify_fn: Callable[[Any], VerificationReport],
    fallback_result: Any = None,
    max_retries: int = _MAX_RETRIES,
) -> tuple[Any, VerificationReport, int]:
    """Execute code-gen → verify → repair loop.

    Args:
        code_gen_fn: function that takes error_context string and returns
                     (code, result) tuple. Called with "" first, then with
                     failure summary for retries.
        verify_fn: function that takes result and returns VerificationReport
        fallback_result: returned if all retries exhausted
        max_retries: maximum repair attempts (default 2)

    Returns:
        (result, final_verification_report, attempts_used)
    """
    for attempt in range(max_retries + 1):
        if attempt == 0:
            result = code_gen_fn("")
        else:
            error_context = verification.failure_summary()
            if not error_context:
                error_context = "Verification failed with no specific error details"
            logger.info("Repair attempt %d/%d: %s", attempt, max_retries, error_context[:100])
            result = code_gen_fn(error_context)

        verification = verify_fn(result) if result else VerificationReport(
            passed=False,
            checks=[model_check("empty", "error", "code_gen_fn returned None/empty")],
        )
        verification.repair_attempts = attempt

        if verification.passed:
            return result, verification, attempt

    # All retries exhausted
    verification.fallback = True
    logger.warning("Repair exhausted after %d attempts, using fallback", max_retries + 1)
    return fallback_result, verification, max_retries + 1


def model_check(type_: str, severity: str, detail: str):
    """Shortcut for creating a VerificationCheck."""
    from scruticell.preprocessing.models import VerificationCheck
    return VerificationCheck(type=type_, severity=severity, detail=detail)
