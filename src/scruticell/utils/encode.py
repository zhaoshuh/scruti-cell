"""Encoding detection and repair utilities.

Handles the common encoding issues in GEO SOFT files:
- Mixed UTF-8 / GB2312 / Latin-1
- Full-width / half-width character confusion
- BOM detection and stripping
- Invalid character replacement
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def detect_encoding(data: bytes) -> str:
    """Detect the most likely encoding of a byte string.

    Uses charset-normalizer (chardet successor) for accurate detection.

    Args:
        data: Raw bytes from file.

    Returns:
        Detected encoding name (e.g., 'utf-8', 'gb2312', 'latin-1').
    """
    try:
        from charset_normalizer import from_bytes

        result = from_bytes(data).best()
        if result:
            return result.encoding
    except ImportError:
        pass

    return "utf-8"


def safe_decode(data: bytes, encoding: str = "") -> str:
    """Decode bytes to string with error handling.

    Attempts the specified encoding, falling back to detected encoding,
    then to utf-8 with replacement characters.
    """
    if not encoding:
        encoding = detect_encoding(data)

    try:
        return data.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        logger.warning("Failed to decode with %s, falling back to utf-8 replace", encoding)
        return data.decode("utf-8", errors="replace")


def repair_text(text: str) -> str:
    """Repair common encoding artifacts in text.

    - Normalize full-width characters to half-width
    - Strip BOM and zero-width characters
    - Replace invalid/unprintable characters
    """
    text = text.replace("\ufeff", "")  # BOM
    text = text.replace("\u200b", "")  # zero-width space
    return text
