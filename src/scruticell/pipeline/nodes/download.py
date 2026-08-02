"""Download node: fetches GEO SOFT files and supplementary data via HTTPS.

Uses Aspera ascp (fasp protocol) for high-speed downloads when available,
with HTTP Range requests (206 Partial Content) as fallback.
"""

from __future__ import annotations

import logging
import os
import random
import subprocess
import time
from pathlib import Path
from typing import Any

import GEOparse

from scruticell.config import (
    ASPERA_BINARY,
    ASPERA_ENABLED,
    ASPERA_HOST,
    ASPERA_KEY_PATH,
    ASPERA_SPEED_LIMIT,
    ASPERA_TIMEOUT,
    ASPERA_USER,
    BROWSER_HEADERS,
    DOWNLOAD_MODE,
    RANGE_CHUNK_SIZE,
    RANGE_DOWNLOAD_ENABLED,
    RANGE_MAX_CONNECTIONS,
)
from scruticell.pipeline.state import PipelineState
from scruticell.utils.aria2c import download_file_aria2c
from scruticell.utils.download import download_file_range

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_ARIA2_ARGS = ["--max-connection-per-server=1", "--split=1",
               "--min-tls-version=TLSv1.2",
               "--console-log-level=error", "--summary-interval=0",
               "--check-certificate=false"]

_NEEDED_EXTENSIONS = frozenset({".h5ad", ".h5", ".mtx", ".csv", ".csv.gz",
    ".tsv", ".tsv.gz", ".rds", ".loom",
    ".tar.gz", ".tar", ".zip", ".gz",
})


def _backoff_sleep(attempt: int) -> None:
    base = min(2 ** attempt, 120)
    jitter = random.uniform(0, base * 0.3)
    time.sleep(base + jitter)


def _probe_suppl_size(url: str) -> int:
    """Quick HEAD probe to get file size. Returns bytes or 0 on failure."""
    try:
        import httpx
        with httpx.Client(timeout=10, follow_redirects=True,
                          headers=dict(BROWSER_HEADERS)) as client:
            resp = client.head(url)
            resp.raise_for_status()
            return int(resp.headers.get("Content-Length", 0))
    except Exception:
        return 0


# ──────────────────────────────────────────────
# HTTPS download with progress
# ──────────────────────────────────────────────

def _aria2_download(url: str, dest_dir: Path, filename: str) -> bool:
    """Download via aria2c (multi-connection). Falls back to httpx on repeated failure."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    if dest.exists():
        dest.unlink()
    cmd = [
        "aria2c", url,
        "-d", str(dest_dir),
        "-o", filename,
        *_ARIA2_ARGS,
    ]
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            subprocess.run(cmd, check=True, timeout=300)
            if dest.exists() and dest.stat().st_size > 0:
                logger.info("Downloaded: %s (%s bytes)", filename, dest.stat().st_size)
                return True
        except subprocess.CalledProcessError as e:
            logger.warning("aria2c attempt %d/%d for %s: exit %d",
                           attempt, _MAX_RETRIES, filename, e.returncode)
        except Exception as e:
            logger.warning("aria2c attempt %d/%d for %s: %s", attempt, _MAX_RETRIES, filename, e)
        if attempt < _MAX_RETRIES:
            _backoff_sleep(attempt)

    # Fallback: range-based HTTPS download (simulates browser 206 Partial Content)
    logger.info("aria2c all failed, falling back to range-download for %s", filename)
    try:
        download_file_range(
            url=url,
            output_path=dest,
            chunk_size=RANGE_CHUNK_SIZE,
            max_connections=RANGE_MAX_CONNECTIONS,
        )
        if dest.exists() and dest.stat().st_size > 0:
            logger.info("Range-downloaded: %s (%s bytes)", filename, dest.stat().st_size)
            return True
    except Exception as e:
        logger.error("Range-download fallback also failed for %s: %s", filename, e)

    logger.error("All %d aria2c attempts + range fallback failed for %s", _MAX_RETRIES, filename)
    return False


def _aspera_download(url: str, dest_dir: Path, filename: str) -> bool:
    """Download via Aspera ascp (fasp protocol, high-speed).

    NCBI supports Aspera downloads at ~100-300 Mbps vs HTTP at ~2-10 Mbps.
    Falls back silently if ascp is not installed or key file is missing.

    Returns True on success, False on failure.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    if dest.exists():
        dest.unlink()

    # Check if ascp is available
    ascp = ASPERA_BINARY
    try:
        subprocess.run([ascp, "--version"], capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("Aspera ascp not found, skip")
        return False

    # Check if key file exists
    key_path = Path(os.path.expandvars(ASPERA_KEY_PATH))
    if not key_path.exists():
        logger.debug("Aspera key not found at %s, skip", key_path)
        return False

    # Convert NCBI HTTPS URL to Aspera remote path
    # https://ftp.ncbi.nlm.nih.gov/geo/series/GSE334nnn/GSE334035/suppl/...
    # → /geo/series/GSE334nnn/GSE334035/suppl/...
    remote_path = url.replace("https://ftp.ncbi.nlm.nih.gov", "")

    cmd = [
        ascp,
        "-i", str(key_path),
        "-k1",                   # resume partially transferred files
        "-Tr",                   # no encryption (faster, data is public)
        "-l", ASPERA_SPEED_LIMIT,  # bandwidth cap
        f"{ASPERA_USER}@{ASPERA_HOST}:{remote_path}",
        str(dest_dir),
    ]

    try:
        logger.info("Downloading via Aspera: %s -> %s", filename, dest_dir)
        result = subprocess.run(cmd, timeout=ASPERA_TIMEOUT, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning("Aspera download failed (exit %d): %s", result.returncode,
                           result.stderr[:200] if result.stderr else "")
            return False
        if dest.exists() and dest.stat().st_size > 0:
            logger.info("Aspera downloaded: %s (%s bytes)", filename, dest.stat().st_size)
            return True
        return False
    except subprocess.TimeoutExpired:
        logger.warning("Aspera download timed out for %s", filename)
        return False
    except Exception as e:
        logger.warning("Aspera download error for %s: %s", filename, e)
        return False


def _needs_download(filename: str) -> bool:
    """Check if a supplementary file is one we need."""
    fname_lower = filename.lower()
    for ext in _NEEDED_EXTENSIONS:
        if fname_lower.endswith(ext):
            return True
    if fname_lower.endswith((".csv.gz", ".csv", ".tsv.gz", ".tsv", ".txt.gz", ".txt")):
        indicators = ["matrix", "count", "expr", "gene", "barcode", "mtx",
                      "metadata", "meta", "sample", "cell", "obs", "anno"]
        if any(indicator in fname_lower for indicator in indicators):
            return True
    return False


def _should_skip_gz_decompress(gz_file: Path) -> bool:
    """Check if a .gz file should NOT be decompressed.

    Skips decompression when:
    - It's a 10X companion file (barcodes/features/genes + matrix present)
    - The 10X matrix itself when companion files exist
    - Raw sequencing/alignment files (.fastq, .bam, .sam)
    - The decompressed sibling already exists
    """
    name = gz_file.name.lower()
    parent = gz_file.parent

    # Archive handled by tar.extractall
    if name.endswith((".tar.gz", ".tgz")):
        return True

    # Already decompressed
    if (parent / gz_file.stem).exists():
        return True

    # Raw data — never decompress
    if name.endswith((".fastq.gz", ".fq.gz", ".bam.gz", ".sam.gz")):
        return True

    # 10X companion files when matrix is present nearby
    if name.endswith(".tsv.gz") and any(
        f.name.lower().endswith(".mtx.gz") and "matrix" in f.name.lower()
        for f in parent.iterdir() if f.is_file()
    ):
        return True

    # 10X matrix when companion files exist nearby
    if "matrix" in name and name.endswith(".mtx.gz"):
        has_companion = any(
            tag in f.name.lower() and f.name.lower().endswith(".tsv.gz")
            for tag in ("barcodes", "features", "genes")
            for f in parent.iterdir() if f.is_file()
        )
        if has_companion:
            return True

    return False


# ──────────────────────────────────────────────
# Range-based download (primary method, simulates browser 206 Partial Content)
# ──────────────────────────────────────────────

def _range_download(url: str, dest_dir: Path, filename: str) -> bool:
    """Download supplementary file, preferring Aspera ascp over HTTP.

    Flow: Aspera (fasp) → HTTP Range (206) → aria2c
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename

    # 1. Try Aspera first (high-speed fasp protocol)
    if ASPERA_ENABLED:
        if _aspera_download(url, dest_dir, filename):
            return True

    # 2. Fall back to HTTP Range requests
    try:
        download_file_range(
            url=url,
            output_path=dest,
            chunk_size=RANGE_CHUNK_SIZE,
            max_connections=RANGE_MAX_CONNECTIONS,
        )
        if dest.exists() and dest.stat().st_size > 0:
            logger.info("Range-downloaded: %s (%s bytes)", filename, dest.stat().st_size)
            return True
    except Exception as e:
        logger.warning("Range-download failed for %s: %s, falling back to aria2c", filename, e)
        return _aria2_download(url, dest_dir, filename)


# ──────────────────────────────────────────────
# Extract supplementary file URLs from SOFT metadata
# ──────────────────────────────────────────────

def _collect_supplementary_urls(geo) -> list[tuple[str, str, str]]:
    """Collect supplementary file URLs from GSE and GSM metadata.

    Returns list of (entity_id, ftp_url, filename) tuples.
    Converts FTP urls to HTTPS for download.
    """
    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    def _extract(meta: dict, entity_id: str):
        for key_base in ("supplementary_file", "suppl_file"):
            # Check base key without index (e.g., "supplementary_file")
            if key_base in meta:
                urls = meta[key_base] if isinstance(meta[key_base], list) else [meta[key_base]]
                for url in urls:
                    url_str = str(url).strip()
                    if not url_str or url_str.upper() == "NONE":
                        continue
                    filename = url_str.split("/")[-1]
                    if filename in seen:
                        continue
                    if not _needs_download(filename) and not filename.lower().endswith(".txt"):
                        continue
                    seen.add(filename)
                    https_url = url_str.replace("ftp://ftp.ncbi.nlm.nih.gov/",
                                               "https://ftp.ncbi.nlm.nih.gov/")
                    results.append((entity_id, https_url, filename))
            # Then check numbered variants: supplementary_file_1, _2, ...
            i = 1
            while True:
                key = f"{key_base}_{i}"
                if key not in meta:
                    break
                urls = meta[key] if isinstance(meta[key], list) else [meta[key]]
                for url in urls:
                    url_str = str(url).strip()
                    if not url_str or url_str.upper() == "NONE":
                        continue
                    filename = url_str.split("/")[-1]
                    if filename in seen:
                        continue
                    if not _needs_download(filename):
                        continue
                    seen.add(filename)
                    https_url = url_str.replace("ftp://ftp.ncbi.nlm.nih.gov/",
                                               "https://ftp.ncbi.nlm.nih.gov/")
                    results.append((entity_id, https_url, filename))
                i += 1

    # GSE-level supplementary files only (RAW.tar contains all GSM data)
    gse_meta = getattr(geo, "metadata", {})
    gse_id = geo.name
    _extract(gse_meta, f"__GSE__{gse_id}")

    logger.info("Found %d supplementary files from GSE %s",
                len(results), gse_id)
    return results


# ──────────────────────────────────────────────
# Public helper: rebuild file_gsm_map from cached SOFT
# ──────────────────────────────────────────────

def _extract_supplementary_refs(gse_id: str) -> list[dict[str, str]]:
    """Extract supplementary file references from cached SOFT metadata.

    Used by the inspect command when --no-download is specified.
    Returns list of {filename, gsm} dicts.
    """
    from scruticell.config import get_download_path
    dest = get_download_path(gse_id)
    soft_gz_path = dest / f"{gse_id}_family.soft.gz"
    if not soft_gz_path.exists():
        logger.warning("SOFT file not cached for %s, cannot extract supplementary refs", gse_id)
        return []
    geo = GEOparse.get_GEO(filepath=str(soft_gz_path), silent=True)
    suppl_urls = _collect_supplementary_urls(geo)
    return [{"filename": filename, "gsm": entity_id.replace(f"__GSE__{gse_id}", gse_id)}
            for entity_id, _url, filename in suppl_urls]


# ──────────────────────────────────────────────
# Main download node
# ──────────────────────────────────────────────

def download_node(state: PipelineState) -> dict[str, Any]:
    """Download SOFT file and supplementary files for a GSE accession via HTTPS."""
    logger.info("Downloading data for %s", state.gse_id)

    gse_id = state.gse_id
    from scruticell.config import get_download_path
    download_dir = get_download_path(gse_id)
    download_dir.mkdir(parents=True, exist_ok=True)

    download_errors: list[str] = []
    supplementary_files: list[Path] = []
    file_gsm_map: dict[str, str] = {}

    # ── SOFT file: download with Aspera (preferred) or HTTP Range, then parse locally ──
    geo = None
    soft_gz_path = download_dir / f"{gse_id}_family.soft.gz"
    try:
        from scruticell.parsing.soft import _build_soft_url
        soft_url = _build_soft_url(gse_id)
        soft_filename = f"{gse_id}_family.soft.gz"

        # Try Aspera first for SOFT file, then selected download mode
        soft_ok = False
        if ASPERA_ENABLED:
            soft_ok = _aspera_download(soft_url, download_dir, soft_filename)
        if not soft_ok:
            if DOWNLOAD_MODE == "aria2c":
                soft_ok = download_file_aria2c(soft_url, soft_gz_path)
            else:
                download_file_range(url=soft_url, output_path=soft_gz_path)
                soft_ok = True

        if soft_ok:
            geo = GEOparse.get_GEO(filepath=str(soft_gz_path), silent=True)
    except OSError as e:
        logger.error("Network error downloading %s: %s", gse_id, e)
        return {"download_dir": download_dir, "soft_file": None,
                "supplementary_files": [], "download_errors": [f"Network error: {e}"],
                "file_gsm_map": {}}
    except Exception as e:
        logger.exception("Unexpected error downloading %s", gse_id)
        return {"download_dir": download_dir, "soft_file": None,
                "supplementary_files": [], "download_errors": [f"Unexpected error: {e}"],
                "file_gsm_map": {}}

    # Find SOFT file
    soft_file: Path | None = None
    soft_path = download_dir / f"{gse_id}_family.soft"
    soft_gz_path = download_dir / f"{gse_id}_family.soft.gz"
    if soft_path.exists():
        soft_file = soft_path
    elif soft_gz_path.exists():
        soft_file = soft_gz_path
    else:
        candidates = sorted(download_dir.glob(f"{gse_id}*.soft*"))
        if candidates:
            soft_file = candidates[0]
        else:
            download_errors.append("SOFT file not found after download")
    logger.info("SOFT file: %s", soft_file)

    # ── Supplementary files via HTTPS (extracted from SOFT metadata) ──
    suppl_urls: list[tuple[str, str, str]] = []
    if geo is not None:
        try:
            suppl_urls = _collect_supplementary_urls(geo)
        except Exception as e:
            logger.warning("Failed to extract supplementary URLs: %s", e)

    # ── Check total supplementary size, skip if too large ──
    from scruticell.config import MAX_SUPPL_SIZE_GB
    _max_suppl_size = MAX_SUPPL_SIZE_GB * 1024 * 1024 * 1024  # configurable GB limit
    suppl_total_size = 0
    for _entity_id, https_url, filename in suppl_urls:
        size = _probe_suppl_size(https_url)
        if size > 0:
            suppl_total_size += size
            logger.debug("Probed %s: %d bytes", filename, size)
    if suppl_total_size > _max_suppl_size:
        import click as _click
        gb = suppl_total_size / (1024 * 1024 * 1024)
        msg = (
            f"GSE {gse_id}: supplementary files too large "
            f"({gb:.1f} GB > {MAX_SUPPL_SIZE_GB} GB). Download skipped."
        )
        logger.error(msg)
        raise _click.Abort(msg)

    for entity_id, https_url, filename in suppl_urls:
        if DOWNLOAD_MODE == "aria2c":
            ok = download_file_aria2c(https_url, download_dir / filename)
        elif RANGE_DOWNLOAD_ENABLED:
            ok = _range_download(https_url, download_dir, filename)
        else:
            ok = _aria2_download(https_url, download_dir, filename)
        if ok:
            supplementary_files.append(download_dir / filename)
            if entity_id.startswith("__GSE__"):
                file_gsm_map[filename] = "__GSE__"
            else:
                file_gsm_map[filename] = entity_id

    logger.info("Downloaded %d supplementary files (from %d URLs)",
                len(supplementary_files), len(suppl_urls))

    # ── Auto-extract archives ──
    import gzip as _gzip
    import shutil as _shutil
    import tarfile as _tarfile
    import zipfile as _zipfile
    for sf in list(supplementary_files):
        sf_str = str(sf).lower()
        extracted_dir = sf.parent / f"{sf.stem}_extracted"
        try:
            if sf_str.endswith((".tar.gz", ".tgz", ".tar")) and not extracted_dir.exists():
                size_mb = sf.stat().st_size / (1024 * 1024)
                logger.info("Extracting archive (%d MB): %s", size_mb, sf.name)
                extracted_dir.mkdir(parents=True, exist_ok=True)
                with _tarfile.open(sf, "r:*") as tf:
                    tf.extractall(path=extracted_dir)
                file_count = 0
                for p in extracted_dir.rglob("*"):
                    if p.is_file():
                        supplementary_files.append(p)
                        file_count += 1
                logger.info("Extracted %d files from %s", file_count, sf.name)
            elif sf_str.endswith(".zip") and not extracted_dir.exists():
                size_mb = sf.stat().st_size / (1024 * 1024)
                logger.info("Extracting archive (%d MB): %s", size_mb, sf.name)
                extracted_dir.mkdir(parents=True, exist_ok=True)
                with _zipfile.ZipFile(sf, "r") as zf:
                    zf.extractall(path=extracted_dir)
                file_count = 0
                for p in extracted_dir.rglob("*"):
                    if p.is_file():
                        supplementary_files.append(p)
                        file_count += 1
                logger.info("Extracted %d files from %s", file_count, sf.name)
            elif sf_str.endswith(".gz") and not sf_str.endswith((".tar.gz", ".tgz")):
                if _should_skip_gz_decompress(sf):
                    logger.debug("Skipping decompress (companion/existing): %s", sf.name)
                    continue
                decompressed = sf.parent / sf.stem
                if not decompressed.exists():
                    size_mb = sf.stat().st_size / (1024 * 1024)
                    logger.info("Decompressing (%d MB): %s", size_mb, sf.name)
                    with _gzip.open(sf, "rb") as gz_in:
                        with open(decompressed, "wb") as raw_out:
                            _shutil.copyfileobj(gz_in, raw_out)
                    supplementary_files.append(decompressed)
        except Exception as exc:
            logger.warning("Extract failed %s: %s", sf.name, exc)

    logger.info("Total supplementary files after extraction: %d", len(supplementary_files))

    return {
        "download_dir": download_dir,
        "soft_file": soft_file,
        "supplementary_files": supplementary_files,
        "download_errors": download_errors,
        "file_gsm_map": file_gsm_map,
    }
