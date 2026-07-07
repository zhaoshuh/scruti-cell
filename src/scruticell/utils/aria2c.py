"""Dedicated aria2c download module with full browser header simulation.

Supports multi-connection split downloading while emulating browser behavior
via custom User-Agent and HTTP headers. Falls back to HTTP Range download
when aria2c is unavailable or fails repeatedly.

Design:
  - Converts BROWSER_HEADERS to aria2c --header / --user-agent args
  - Multi-connection splitting (-x, -s, -k) for large files
  - Resumable downloads (-c) with retry + backoff
  - Falls back to download_file_range on exhaustion
  - Zero overhead: logs warnings but never aborts the pipeline
"""

from __future__ import annotations

import logging
import random
import subprocess
import threading
import time
from pathlib import Path

import httpx

from scruticell.config import (
    ARIA2C_BROWSER_MODE,
    ARIA2C_MAX_CONNECTIONS,
    ARIA2C_MIN_SPLIT_SIZE,
    ARIA2C_SPLIT,
    BROWSER_HEADERS,
    RANGE_CHUNK_SIZE,
    RANGE_MAX_CONNECTIONS,
)

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5


def _backoff_sleep(attempt: int) -> None:
    base = min(2 ** attempt, 60)
    jitter = random.uniform(0, base * 0.3)
    time.sleep(base + jitter)


def _build_browser_header_args() -> list[str]:
    """Convert BROWSER_HEADERS dict to aria2c --header and --user-agent args.

    aria2c treats --user-agent separately from --header, so we split
    the User-Agent key from the rest.
    """
    args: list[str] = []
    for key, value in BROWSER_HEADERS.items():
        if key.lower() == "user-agent":
            args.extend(["--user-agent", value])
        else:
            args.extend(["--header", f"{key}: {value}"])
    return args


def _check_aria2c_available() -> bool:
    """Check whether aria2c is installed and reachable."""
    try:
        result = subprocess.run(
            ["aria2c", "--version"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _probe_content_length(url: str) -> int:
    """Send HEAD request to get Content-Length for progress reporting.

    Returns total_size in bytes, or 0 if the probe fails.
    """
    try:
        with httpx.Client(
            timeout=httpx.Timeout(30, connect=15),
            follow_redirects=True,
            headers=dict(BROWSER_HEADERS),
        ) as client:
            head_resp = client.head(url)
            head_resp.raise_for_status()
            total = int(head_resp.headers.get("Content-Length", 0))
            if total > 0:
                logger.debug("Probed file size for %s: %d bytes", url, total)
            return total
    except Exception as exc:
        logger.debug("HEAD probe failed for %s: %s", url, exc)
        return 0


def download_file_aria2c(
    url: str,
    output_path: Path,
    max_connections: int = ARIA2C_MAX_CONNECTIONS,
    split: int = ARIA2C_SPLIT,
    min_split_size: str = ARIA2C_MIN_SPLIT_SIZE,
    continue_download: bool = True,
    timeout: int = 600,
    max_retries: int = _MAX_RETRIES,
) -> bool:
    """Download a file using aria2c with browser header simulation and multi-connection splitting.

    Builds the aria2c command with browser-mimicking headers (User-Agent,
    Referer, Accept, etc.), multi-connection splitting for faster downloads,
    and resumable support. Falls back to HTTP Range download on failure.

    Args:
        url: URL to download.
        output_path: Local path to save the file.
        max_connections: Max connections per server (-x). Default from config.
        split: Number of split chunks (-s). Default from config.
        min_split_size: Minimum split size (-k), e.g. "10M". Default from config.
        continue_download: Resume partial downloads (-c). Default True.
        timeout: Subprocess timeout in seconds per attempt.
        max_retries: Maximum number of aria2c retry attempts before fallback.

    Returns:
        True on success, False on failure after all retries and fallback.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filename = output_path.name

    if not _check_aria2c_available():
        logger.warning("aria2c not available, falling back to range download for %s", filename)
        return _fallback_range_download(url, output_path)

    # ── Probe remote file size for progress reporting ──
    total_size = _probe_content_length(url)
    if total_size > 0:
        logger.info("File size: %d MB, aria2c download starting", total_size // (1024 * 1024))

    browser_args: list[str] = []
    if ARIA2C_BROWSER_MODE:
        browser_args = _build_browser_header_args()
        logger.debug("aria2c browser mode enabled with %d header args", len(browser_args))

    # ── Progress monitoring daemon thread ──
    initial_bytes = output_path.stat().st_size if output_path.exists() else 0
    progress_start = time.monotonic()
    last_bytes = initial_bytes
    last_report = progress_start
    stop_progress = threading.Event()

    def _monitor_progress() -> None:
        nonlocal last_bytes, last_report
        while not stop_progress.is_set():
            if output_path.exists():
                current = output_path.stat().st_size
                if current > last_bytes:
                    now = time.monotonic()
                    speed_interval = (
                        (current - last_bytes) / max(now - last_report, 0.1)
                        / (1024 * 1024)
                    )
                    if total_size > 0:
                        pct = current / total_size * 100
                        logger.info(
                            "Downloading: %d/%d MB (%.0f%%) %.1f MB/s",
                            current // (1024 * 1024),
                            total_size // (1024 * 1024),
                            min(pct, 100),
                            speed_interval,
                        )
                    else:
                        logger.info(
                            "Downloading: %d/? MB %.1f MB/s",
                            current // (1024 * 1024),
                            speed_interval,
                        )
                    last_bytes = current
                    last_report = now
            time.sleep(2)

    progress_thread = threading.Thread(target=_monitor_progress, daemon=True)
    progress_thread.start()

    # ── Download with retries ──
    succeeded = False
    try:
        for attempt in range(1, max_retries + 1):
            cmd = [
                "aria2c",
                url,
                "-d", str(output_path.parent),
                "-o", filename,
                "-x", str(max_connections),
                "-s", str(split),
                "-k", min_split_size,
                "--min-tls-version=TLSv1.2",
                "--console-log-level=error",
                "--summary-interval=0",
                "--check-certificate=false",
            ]
            if continue_download:
                cmd.append("-c")
            if browser_args:
                cmd.extend(browser_args)

            try:
                logger.info(
                    "aria2c downloading: %s (attempt %d/%d, x=%d, s=%d, k=%s)",
                    filename, attempt, max_retries, max_connections, split, min_split_size,
                )
                result = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)

                if (
                    result.returncode == 0
                    and output_path.exists()
                    and output_path.stat().st_size > 0
                ):
                    succeeded = True
                    break

                stderr_snippet = (result.stderr or "")[:200]
                logger.warning(
                    "aria2c attempt %d/%d for %s: exit=%d, stderr=%s",
                    attempt, max_retries, filename, result.returncode, stderr_snippet,
                )
            except subprocess.TimeoutExpired:
                logger.warning(
                    "aria2c attempt %d/%d for %s: timeout (%ds)",
                    attempt, max_retries, filename, timeout,
                )
            except Exception as exc:
                logger.warning(
                    "aria2c attempt %d/%d for %s: %s",
                    attempt, max_retries, filename, exc,
                )

            if attempt < max_retries:
                _backoff_sleep(attempt)
    finally:
        stop_progress.set()
        progress_thread.join(timeout=5)

    # ── Final summary ──
    if succeeded:
        final_size = output_path.stat().st_size
        elapsed = time.monotonic() - progress_start
        downloaded = final_size - initial_bytes
        avg_speed = downloaded / max(elapsed, 0.1) / (1024 * 1024)
        logger.info(
            "Download complete: %d MB in %.0fs (%.1f MB/s avg)",
            final_size // (1024 * 1024), elapsed, avg_speed,
        )
        return True

    logger.info(
        "aria2c all %d attempts failed for %s, falling back to range download",
        max_retries, filename,
    )
    return _fallback_range_download(url, output_path)


def _fallback_range_download(url: str, output_path: Path) -> bool:
    """Fall back to HTTP Range (206 Partial Content) download.

    Uses the same browser-simulating Range download as the "range" mode.
    """
    try:
        from scruticell.utils.download import download_file_range
        download_file_range(
            url=url,
            output_path=output_path,
            chunk_size=RANGE_CHUNK_SIZE,
            max_connections=RANGE_MAX_CONNECTIONS,
        )
        if output_path.exists() and output_path.stat().st_size > 0:
            logger.info("Range-downloaded (fallback): %s", output_path.name)
            return True
    except Exception as exc:
        logger.error(
            "Range download fallback also failed for %s: %s", output_path.name, exc,
        )
    return False
