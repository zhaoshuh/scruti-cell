"""Default configuration for the Scrut-Cell pipeline."""

import os
from pathlib import Path

# Project root
ROOT_DIR = Path(__file__).parent.parent.parent

# Config directory with seed data
CONFIG_DIR = ROOT_DIR / "config"

# Data download cache directory (relative to project, overridable via SCRUTICELL_DATA_DIR env)
_DEFAULT_DATA = str(Path.home() / ".scruticell" / "data")
DATA_DIR = Path(os.environ.get("SCRUTICELL_DATA_DIR", _DEFAULT_DATA))

# Default output directory
OUTPUT_DIR = Path(os.environ.get("SCRUTICELL_OUTPUT_DIR", "output"))

# Neo4j defaults (for enterprise version)
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "scruticell"

# LLM defaults
LLM_MODEL = "deepseek-v4-pro"
LLM_TEMPERATURE = 0.0  # deterministic for pipeline
LLM_BASE_URL = "https://api.deepseek.com"
LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")

# NCBI API key for PubMed / E-utilities (10 req/s instead of 3)
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")

# Download settings
DOWNLOAD_TIMEOUT = 600  # seconds
DOWNLOAD_RETRIES = 3
DOWNLOAD_CHUNK_SIZE = 8192  # bytes for streaming
DOWNLOAD_CONNECT_TIMEOUT = 30  # seconds for initial connection
DOWNLOAD_STARTUP_TIMEOUT = 120  # seconds before first byte received
MAX_SUPPL_SIZE_GB = int(os.environ.get("SCRUTICELL_MAX_SUPPL_SIZE_GB", "10"))

# Download mode: "range" (HTTP 206 Partial Content, default) or "aria2c" (multi-connection)
# Override via SCRUTICELL_DOWNLOAD_MODE env variable
DOWNLOAD_MODE = os.environ.get("SCRUTICELL_DOWNLOAD_MODE", "range")

# HTTP Range download settings (simulating browser behavior with 206 Partial Content)
RANGE_DOWNLOAD_ENABLED = True         # use Range requests for resumable downloads
RANGE_CHUNK_SIZE = 16 * 1024 * 1024   # 16 MB per Range chunk
RANGE_MAX_CONNECTIONS = 3             # 1 = single-thread, >1 = concurrent

# aria2c multi-connection download settings
#   ARIA2C_MAX_CONNECTIONS (-x): max connections per server
#   ARIA2C_SPLIT           (-s): number of download splits
#   ARIA2C_MIN_SPLIT_SIZE  (-k): minimum split size (e.g. "10M")
#   ARIA2C_BROWSER_MODE:     inject BROWSER_HEADERS as --header / --user-agent
# All overridable via environment variables.
ARIA2C_MAX_CONNECTIONS = int(os.environ.get("SCRUTICELL_ARIA2C_CONNECTIONS", "8"))
ARIA2C_SPLIT = int(os.environ.get("SCRUTICELL_ARIA2C_SPLIT", "8"))
ARIA2C_MIN_SPLIT_SIZE = os.environ.get("SCRUTICELL_ARIA2C_MIN_SPLIT_SIZE", "10M")
ARIA2C_BROWSER_MODE = os.environ.get("SCRUTICELL_ARIA2C_BROWSER_MODE", "true").lower() != "false"

# Aspera high-speed download settings (fasp protocol, ~10x faster than HTTP)
# NOTE: NCBI discontinued public Aspera access for GEO data in 2024-2025.
# The key file and Aspera DNS endpoints are no longer available.
# Code is retained for future use or private Aspera servers.
ASPERA_ENABLED = False
ASPERA_HOST = "ftp-private.ncbi.nlm.nih.gov"
ASPERA_USER = "anonftp"
ASPERA_KEY_PATH = os.environ.get(
    "ASPERA_KEY_PATH",
    str(Path.home() / ".aspera/connect/etc/asperaweb_id_dsa.openssh"),
)
ASPERA_BINARY = os.environ.get("ASPERA_BINARY", "ascp")
ASPERA_SPEED_LIMIT = "100m"
ASPERA_TIMEOUT = 300

# Browser-mimicking headers for NCBI downloads
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8,"
              "application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Referer": "https://ftp.ncbi.nlm.nih.gov/geo/series/",
}

# Validation thresholds
CONFIDENCE_THRESHOLD_AUTO = 0.85  # auto-accept above this
CONFIDENCE_THRESHOLD_WARN = 0.60  # warn but proceed
# below 0.60 -> trigger reflexion

# Maximum matrix size for dense loading (before switching to sparse)
MAX_DENSE_CELLS = 100_000
MAX_DENSE_GENES = 30_000


def get_output_path(gse_id: str) -> Path:
    """Return the resolved output path for a GSE accession."""
    return (Path.cwd() / OUTPUT_DIR / gse_id).resolve()


def get_download_path(gse_id: str) -> Path:
    """Return the resolved download path for a GSE accession."""
    return get_output_path(gse_id) / "download"
