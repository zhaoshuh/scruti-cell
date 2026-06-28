"""Download utilities for GEO data retrieval."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import httpx

from scruticell.config import DOWNLOAD_CHUNK_SIZE, DOWNLOAD_RETRIES, DOWNLOAD_TIMEOUT

logger = logging.getLogger(__name__)


def download_file(
    url: str,
    output_path: Path,
    expected_hash: str = "",
    timeout: int = DOWNLOAD_TIMEOUT,
    retries: int = DOWNLOAD_RETRIES,
) -> Path:
    """Download a file with retry logic and optional hash verification.

    Args:
        url: URL to download.
        output_path: Local path to save the file.
        expected_hash: Optional SHA256 hex digest for verification.
        timeout: Timeout per request in seconds.
        retries: Maximum number of retry attempts.

    Returns:
        Path to the downloaded file.

    Raises:
        RuntimeError: If download fails after all retries.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(retries):
        try:
            logger.info("Downloading %s (attempt %d/%d)", url, attempt + 1, retries)
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with open(output_path, "wb") as f:
                        for chunk in response.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                            f.write(chunk)

            if expected_hash:
                actual_hash = sha256_file(output_path)
                if actual_hash != expected_hash:
                    logger.warning("Hash mismatch, retrying...")
                    continue

            logger.info("Downloaded %s -> %s", url, output_path)
            return output_path

        except Exception as e:
            logger.warning("Download attempt %d failed: %s", attempt + 1, e)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise RuntimeError(f"Failed to download {url} after {retries} attempts") from e

    raise RuntimeError(f"Failed to download {url}")


def sha256_file(filepath: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(DOWNLOAD_CHUNK_SIZE), b""):
            sha256.update(chunk)
    return sha256.hexdigest()
