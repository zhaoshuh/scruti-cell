"""Download node: fetches GEO SOFT files and supplementary data via HTTPS."""

from __future__ import annotations

import logging
import random
import subprocess
import time
from pathlib import Path
from typing import Any

import GEOparse

from scruticell.pipeline.state import PipelineState

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_ARIA2_ARGS = ["--max-connection-per-server=3", "--split=3", "--min-split-size=1M",
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
            subprocess.run(cmd, check=True, timeout=3600)
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

    # Fallback: single-connection HTTPS via httpx
    logger.info("aria2c all failed, falling back to httpx for %s", filename)
    try:
        import httpx
        with httpx.Client(timeout=600, verify=False, follow_redirects=True) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=1048576):
                        f.write(chunk)
        if dest.exists() and dest.stat().st_size > 0:
            logger.info("Downloaded via httpx: %s (%s bytes)", filename, dest.stat().st_size)
            return True
    except Exception as e:
        logger.error("httpx fallback also failed for %s: %s", filename, e)

    logger.error("All %d aria2c attempts + httpx fallback failed for %s", _MAX_RETRIES, filename)
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
    dest = str(Path.cwd() / "output" / gse_id / "download")
    geo = GEOparse.get_GEO(geo=gse_id, destdir=dest, silent=True)
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
    download_dir = Path.cwd() / "output" / gse_id / "download"
    download_dir.mkdir(parents=True, exist_ok=True)

    download_errors: list[str] = []
    supplementary_files: list[Path] = []
    file_gsm_map: dict[str, str] = {}

    # ── SOFT file + supplementary URLs via GEOparse ──
    geo = None
    try:
        geo = GEOparse.get_GEO(geo=gse_id, destdir=str(download_dir), silent=True)
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

    for entity_id, https_url, filename in suppl_urls:
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
                logger.info("Extracting archive: %s", sf.name)
                extracted_dir.mkdir(parents=True, exist_ok=True)
                with _tarfile.open(sf, "r:*") as tf:
                    tf.extractall(path=extracted_dir)
                for p in extracted_dir.rglob("*"):
                    if p.is_file():
                        supplementary_files.append(p)
            elif sf_str.endswith(".zip") and not extracted_dir.exists():
                logger.info("Extracting archive: %s", sf.name)
                extracted_dir.mkdir(parents=True, exist_ok=True)
                with _zipfile.ZipFile(sf, "r") as zf:
                    zf.extractall(path=extracted_dir)
                for p in extracted_dir.rglob("*"):
                    if p.is_file():
                        supplementary_files.append(p)
            elif sf_str.endswith(".gz") and not sf_str.endswith((".tar.gz", ".tgz")):
                # Single-file gzip (e.g., .h5ad.gz, .csv.gz) — decompress to sibling without .gz
                decompressed = sf.parent / sf.stem
                if not decompressed.exists():
                    logger.info("Decompressing: %s", sf.name)
                    with _gzip.open(sf, "rb") as gz_in:
                        with open(decompressed, "wb") as raw_out:
                            _shutil.copyfileobj(gz_in, raw_out)
                    supplementary_files.append(decompressed)
        except Exception as exc:
            logger.warning("Extract failed %s: %s", sf.name, exc)

    return {
        "download_dir": download_dir,
        "soft_file": soft_file,
        "supplementary_files": supplementary_files,
        "download_errors": download_errors,
        "file_gsm_map": file_gsm_map,
    }
