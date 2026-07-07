"""Download utilities for GEO data retrieval with HTTP Range (206 Partial Content) support.

Simulates browser behavior when downloading from NCBI servers:
- Sends realistic browser headers (User-Agent, Accept, etc.)
- Uses HTTP Range requests for chunked, resumable downloads
- Handles 206 Partial Content responses
- Supports concurrent Range connections for faster downloads
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

import httpx

from scruticell.config import (
    BROWSER_HEADERS,
    DOWNLOAD_CHUNK_SIZE,
    DOWNLOAD_CONNECT_TIMEOUT,
    DOWNLOAD_RETRIES,
    DOWNLOAD_TIMEOUT,
    RANGE_CHUNK_SIZE,
    RANGE_MAX_CONNECTIONS,
)

logger = logging.getLogger(__name__)


def _probe_url(
    url: str,
    client: httpx.Client,
) -> tuple[int, str | None]:
    """Send HEAD request to check Accept-Ranges support and get file info.

    Returns (total_size, last_modified) or (0, None) on failure.
    """
    try:
        head_resp = client.head(url, headers=BROWSER_HEADERS)
        head_resp.raise_for_status()
        total = int(head_resp.headers.get("Content-Length", 0))
        last_modified = head_resp.headers.get("Last-Modified")
        accepts_ranges = head_resp.headers.get("Accept-Ranges", "").lower()
        if "bytes" not in accepts_ranges:
            logger.warning("Server does not support byte ranges for %s", url)
        return total, last_modified
    except Exception as e:
        logger.debug("HEAD probe failed for %s: %s", url, e)
        return 0, None


def _downloaded_bytes(filepath: Path) -> int:
    """Return the number of bytes already written to the file."""
    if filepath.exists():
        return filepath.stat().st_size
    return 0


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
            with httpx.Client(
                timeout=timeout, follow_redirects=True, headers=BROWSER_HEADERS,
            ) as client:
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


# ---------------------------------------------------------------------------
# Range-based download (simulates browser 206 Partial Content behavior)
# ---------------------------------------------------------------------------

def download_file_range(
    url: str,
    output_path: Path,
    expected_hash: str = "",
    timeout: int = DOWNLOAD_TIMEOUT,
    retries: int = DOWNLOAD_RETRIES,
    chunk_size: int = RANGE_CHUNK_SIZE,
    max_connections: int = RANGE_MAX_CONNECTIONS,
    headers: dict[str, str] | None = None,
) -> Path:
    """Download a file using HTTP Range requests (206 Partial Content).

    Simulates browser behaviour when downloading from NCBI / GEO servers:
    - Sends browser-like headers (User-Agent, Accept, Referer, etc.)
    - Probes server for Accept-Ranges and Content-Length via HEAD
    - Downloads in chunks using Range: bytes=<start>-<end>
    - Resumes interrupted downloads by checking existing file size
    - Uses If-Range header with Last-Modified for conditional requests
    - Supports concurrent Range connections for multi-threaded download

    Args:
        url: URL to download.
        output_path: Local path to save the file.
        expected_hash: Optional SHA256 hex digest for verification.
        timeout: Timeout per Range request in seconds.
        retries: Maximum number of retry attempts per chunk.
        chunk_size: Byte size of each Range chunk (default 4 MB).
        max_connections: Maximum concurrent Range connections.
        headers: Additional request headers (merged with BROWSER_HEADERS).

    Returns:
        Path to the downloaded file.

    Raises:
        RuntimeError: If download fails after all retries.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    req_headers = dict(BROWSER_HEADERS)
    if headers:
        req_headers.update(headers)

    for attempt in range(retries):
        try:
            logger.info(
                "Range-downloading %s (attempt %d/%d)", url, attempt + 1, retries
            )

            with httpx.Client(
                timeout=httpx.Timeout(timeout, connect=DOWNLOAD_CONNECT_TIMEOUT),
                follow_redirects=True,
                headers=req_headers,
            ) as client:
                # ── Step 1: probe server capabilities ──
                total_size, last_modified = _probe_url(url, client)

                # If HEAD failed, try GET to get Content-Range info
                if total_size == 0:
                    probe_resp = client.get(url, headers={"Range": "bytes=0-0"})
                    if probe_resp.status_code in (200, 206):
                        cr = probe_resp.headers.get("Content-Range", "")
                        if cr and "/" in cr:
                            total_size = _parse_total_from_content_range(cr)
                        if not total_size:
                            total_size = int(probe_resp.headers.get("Content-Length", 0))
                        last_modified = probe_resp.headers.get("Last-Modified") or last_modified
                    else:
                        probe_resp.raise_for_status()

                if total_size <= 0:
                    raise RuntimeError(
                        f"Could not determine file size for {url} (got {total_size})"
                    )

                logger.info(
                    "File size: %s bytes, Accept-Ranges: supported",
                    total_size,
                )

                # ── Step 2: check for existing partial download ──
                existing_bytes = _downloaded_bytes(output_path)

                if existing_bytes >= total_size:
                    logger.info("File already fully downloaded: %s", output_path)
                    if expected_hash:
                        actual_hash = sha256_file(output_path)
                        if actual_hash != expected_hash:
                            logger.warning("Hash mismatch, re-downloading...")
                            os.remove(output_path)
                            existing_bytes = 0
                        else:
                            return output_path
                    else:
                        return output_path

                if existing_bytes > 0:
                    logger.info(
                        "Resuming download from byte %d / %d", existing_bytes, total_size
                    )
                else:
                    # Create empty file for writing
                    output_path.touch()

                # ── Step 3: build Range chunk plan ──
                ranges_positions = _build_chunk_positions(
                    existing_bytes, total_size, chunk_size, max_connections
                )

                if not ranges_positions:
                    logger.info("No chunks to download, file is complete.")
                    return output_path

                logger.info(
                    "Downloading %d range chunks (%d connections)",
                    len(ranges_positions),
                    max_connections,
                )

                # ── Step 4: download chunks via Range requests ──
                _download_chunks_concurrent(
                    client=client,
                    url=url,
                    output_path=output_path,
                    ranges_positions=ranges_positions,
                    last_modified=last_modified,
                    total_size=total_size,
                    timeout=timeout,
                    max_connections=max_connections,
                    retries=retries,
                    req_headers=req_headers,
                )

            # ── Step 5: verify completeness ──
            if output_path.stat().st_size < total_size:
                raise RuntimeError(
                    f"Download incomplete: {output_path.stat().st_size} / {total_size} bytes"
                )

            if expected_hash:
                actual_hash = sha256_file(output_path)
                if actual_hash != expected_hash:
                    logger.warning("Hash mismatch, retrying...")
                    output_path.unlink()
                    continue

            logger.info(
                "Range-downloaded %s -> %s (%s bytes)",
                url,
                output_path,
                output_path.stat().st_size,
            )
            return output_path

        except Exception as e:
            logger.warning("Range download attempt %d failed: %s", attempt + 1, e)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise RuntimeError(
                    f"Failed to range-download {url} after {retries} attempts"
                ) from e

    raise RuntimeError(f"Failed to range-download {url}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_total_from_content_range(content_range: str) -> int:
    """Parse total size from Content-Range header like 'bytes 0-0/207278080'."""
    try:
        total_str = content_range.split("/")[-1].strip()
        if total_str == "*":
            return 0
        return int(total_str)
    except (ValueError, IndexError):
        return 0


def _build_chunk_positions(
    start_byte: int,
    total_size: int,
    chunk_size: int,
    max_connections: int,
) -> list[tuple[int, int]]:
    """Build a list of (start, end) byte positions for Range requests.

    Single-threaded: one Range request for all remaining bytes.
    Multi-threaded: split into fixed-size chunks.
    """
    remaining = total_size - start_byte
    if remaining <= 0:
        return []

    if max_connections == 1:
        return [(start_byte, total_size - 1)]

    positions: list[tuple[int, int]] = []
    current = start_byte
    while current < total_size:
        end = min(current + chunk_size - 1, total_size - 1)
        positions.append((current, end))
        current = end + 1
    return positions


def _download_single_range(
    client: httpx.Client,
    url: str,
    output_path: Path,
    start: int,
    end: int,
    last_modified: str | None,
    timeout: int,
    req_headers: dict[str, str] | None = None,
) -> bool:
    """Download a single byte range [start, end] and write to file at correct offset.

    Returns True on success, False on failure.
    """
    range_header = f"bytes={start}-{end}"
    req_hdrs = {**req_headers, "Range": range_header} if req_headers else {
        **BROWSER_HEADERS, "Range": range_header,
    }
    if last_modified:
        req_hdrs["If-Range"] = last_modified

    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=req_hdrs,
        ) as range_client:
            with range_client.stream("GET", url, headers=req_hdrs) as resp:
                if resp.status_code not in (200, 206):
                    logger.warning(
                        "Range request returned %d for %s (expected 206)",
                        resp.status_code,
                        range_header,
                    )
                    resp.raise_for_status()

                content_type = resp.headers.get("Content-Range", "")
                logger.debug(
                    "Got %s for Range %s -> %d bytes",
                    content_type,
                    range_header,
                    int(resp.headers.get("Content-Length", 0)),
                )

                # Stream chunks directly to file at the correct offset
                try:
                    bytes_written = 0
                    with open(output_path, "r+b") as f:
                        f.seek(start)
                        for chunk in resp.iter_bytes(262144):  # 256 KB
                            f.write(chunk)
                            bytes_written += len(chunk)
                        f.flush()
                    if bytes_written == 0:
                        logger.warning("Empty response body for Range %s", range_header)
                        return False
                    return True
                except OSError as e:
                    logger.error("Failed to write Range %s: %s", range_header, e)
                    return False

    except Exception as e:
        logger.warning("Range request %s failed: %s", range_header, e)
        return False


def _download_chunks_concurrent(
    client: httpx.Client,
    url: str,
    output_path: Path,
    ranges_positions: list[tuple[int, int]],
    last_modified: str | None,
    total_size: int,
    timeout: int,
    max_connections: int,
    retries: int,
    req_headers: dict[str, str] | None = None,
) -> None:
    """Download range chunks concurrently. Shows total progress across all threads."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    logger.info("Downloading %d MB with %d threads...",
                 total_size // (1024 * 1024), max_connections)

    # Progress monitoring thread
    progress_start = time.monotonic()
    existing = output_path.stat().st_size if output_path.exists() else 0
    last_bytes = existing
    last_report = progress_start
    stop_progress = threading.Event()

    def _monitor_progress():
        nonlocal last_bytes, last_report
        while not stop_progress.is_set():
            if output_path.exists():
                current = output_path.stat().st_size
                if current > last_bytes:
                    now = time.monotonic()
                    byte_diff = current - last_bytes
                    time_diff = max(now - last_report, 0.1)
                    speed_interval = byte_diff / time_diff / (1024 * 1024)
                    pct = current / total_size * 100
                    logger.info(
                        "Downloading: %d/%d MB (%.0f%%) %.1f MB/s",
                        current // (1024 * 1024),
                        total_size // (1024 * 1024),
                        min(pct, 100),
                        speed_interval,
                    )
                    last_bytes = current
                    last_report = now
            time.sleep(3)

    progress_thread = threading.Thread(target=_monitor_progress, daemon=True)
    progress_thread.start()

    try:
        failed_ranges: set[tuple[int, int]] = set()

        with ThreadPoolExecutor(max_workers=max_connections) as executor:
            futures = {}
            for start, end in ranges_positions:
                future = executor.submit(
                    _download_single_range,
                    client, url, output_path, start, end,
                    last_modified, timeout, req_headers,
                )
                futures[future] = (start, end)

            for future in as_completed(futures):
                start, end = futures[future]
                try:
                    success = future.result()
                    if not success:
                        failed_ranges.add((start, end))
                    else:
                        logger.debug("Range %d-%d downloaded", start, end)
                except Exception as e:
                    logger.warning("Range %d-%d error: %s", start, end, e)
                    failed_ranges.add((start, end))

        # Retry failed ranges sequentially
        for rp in sorted(failed_ranges):
            for retry_attempt in range(retries):
                logger.info(
                    "Retrying range %d-%d (%d/%d)",
                    rp[0], rp[1], retry_attempt + 1, retries,
                )
                if _download_single_range(
                    client, url, output_path, rp[0], rp[1], last_modified, timeout, req_headers,
                ):
                    failed_ranges.discard(rp)
                    break
                time.sleep(2 ** retry_attempt)

        if failed_ranges:
            raise RuntimeError(f"Failed to download {len(failed_ranges)} range chunks")
    finally:
        stop_progress.set()
        progress_thread.join(timeout=5)

    elapsed = time.monotonic() - progress_start
    final_speed = total_size / max(elapsed, 0.1) / (1024 * 1024)
    logger.info("Download complete: %d MB in %.0fs (%.1f MB/s avg)",
                 total_size // (1024 * 1024), elapsed, final_speed)


def sha256_file(filepath: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(DOWNLOAD_CHUNK_SIZE), b""):
            sha256.update(chunk)
    return sha256.hexdigest()
