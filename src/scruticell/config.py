"""Default configuration for the Scrut-Cell pipeline.

Configuration precedence (highest wins):
  1. Environment variables (explicit overrides, e.g. DEEPSEEK_API_KEY)
  2. YAML config files: ~/.config/scruticell/config.yaml (global) then ./scruticell.yaml (project)
  3. Built-in defaults below

Run `scruticell config` to see the effective configuration.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

# Project root
ROOT_DIR = Path(__file__).parent.parent.parent

# Config directory with seed data
CONFIG_DIR = ROOT_DIR / "config"

_GLOBAL_CONFIG_PATH = Path.home() / ".config" / "scruticell" / "config.yaml"
_PROJECT_CONFIG_PATH = ROOT_DIR / "scruticell.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _load_yaml() -> dict:
    cfg: dict = {}
    for path in (_GLOBAL_CONFIG_PATH, _PROJECT_CONFIG_PATH):
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                data = None
            if isinstance(data, dict):
                _deep_merge(cfg, data)
    return cfg


_cfg = _load_yaml()


def _resolve(keys, default=None, env=None):
    """Resolve a config value: env override > YAML > default."""
    if env:
        val = os.environ.get(env)
        if val is not None and val != "":
            return val
    node = _cfg
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    if node is None:
        return default
    if isinstance(node, str) and node.startswith("~"):
        node = str(Path(node).expanduser())
    return node


def _resolve_int(keys, default, env=None):
    val = _resolve(keys, default=default, env=env)
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _resolve_bool(keys, default, env=None):
    val = _resolve(keys, default=default, env=env)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return default


# Data download cache directory (relative to project, overridable via SCRUTICELL_DATA_DIR env)
_DEFAULT_DATA = str(Path.home() / ".scruticell" / "data")
DATA_DIR = Path(_resolve(("paths", "data_dir"), default=_DEFAULT_DATA, env="SCRUTICELL_DATA_DIR"))

# Default output directory
OUTPUT_DIR = Path(_resolve(("paths", "output_dir"), default="output", env="SCRUTICELL_OUTPUT_DIR"))

# Neo4j defaults (for enterprise version)
NEO4J_URI = _resolve(("neo4j", "uri"), default="bolt://localhost:7687")
NEO4J_USER = _resolve(("neo4j", "user"), default="neo4j")
NEO4J_PASSWORD = _resolve(("neo4j", "password"), default="scruticell")

# LLM defaults
LLM_MODEL = _resolve(("llm", "model"), default="deepseek-v4-pro", env="LLM_MODEL")
LLM_TEMPERATURE = float(_resolve(("llm", "temperature"), default="0.0"))
LLM_BASE_URL = _resolve(("llm", "base_url"), default="https://api.deepseek.com", env="LLM_BASE_URL")
LLM_API_KEY = (
    _resolve(("llm", "api_key"), default="", env=None)
    or os.environ.get("DEEPSEEK_API_KEY")
    or os.environ.get("OPENAI_API_KEY")
)
# LLM enabled for plan generation (false = knowledge-base matching only)
LLM_ENABLED = _resolve_bool(("llm", "enabled"), default=True)

# NCBI API key for PubMed / E-utilities (10 req/s instead of 3)
NCBI_API_KEY = _resolve(("ncbi_api_key",), default="", env="NCBI_API_KEY")

# Knowledge-base strategy reuse toggle
KB_STRATEGY_REUSE = _resolve_bool(("kb", "strategy_reuse"), default=True)

# Pipeline behavior
LITERATURE_ENABLED = _resolve_bool(("pipeline", "literature_enabled"), default=True)
MAX_RETRIES = _resolve_int(("pipeline", "max_retries"), default=3)
REFLECTION_ENABLED = _resolve_bool(("pipeline", "reflection_enabled"), default=True)
KEEP_DATA = _resolve_bool(("pipeline", "keep_data"), default=False)
DOWNLOAD_ENABLED = _resolve_bool(("pipeline", "download_enabled"), default=True)

# Preprocessing behavior
LLM_ATTRIBUTION_ENABLED = _resolve_bool(("preprocess", "llm_attribution_enabled"), default=True)

# Download settings
DOWNLOAD_TIMEOUT = _resolve_int(("download", "timeout"), default=600)
DOWNLOAD_RETRIES = _resolve_int(("download", "retries"), default=3)
DOWNLOAD_CHUNK_SIZE = _resolve_int(("download", "chunk_size"), default=8192)
DOWNLOAD_CONNECT_TIMEOUT = _resolve_int(("download", "connect_timeout"), default=30)
DOWNLOAD_STARTUP_TIMEOUT = _resolve_int(("download", "startup_timeout"), default=120)
MAX_SUPPL_SIZE_GB = _resolve_int(("download", "max_suppl_size_gb"), default=10,
                                 env="SCRUTICELL_MAX_SUPPL_SIZE_GB")

# Download mode: "range" (HTTP 206 Partial Content, default) or "aria2c" (multi-connection)
DOWNLOAD_MODE = _resolve(("download", "mode"), default="range", env="SCRUTICELL_DOWNLOAD_MODE")

# HTTP Range download settings (simulating browser behavior with 206 Partial Content)
RANGE_DOWNLOAD_ENABLED = _resolve_bool(("download", "range_enabled"), default=True)
RANGE_CHUNK_SIZE = _resolve_int(("download", "range_chunk_size_mb"), default=16) * 1024 * 1024
RANGE_MAX_CONNECTIONS = _resolve_int(("download", "range_max_connections"), default=3)

# aria2c multi-connection download settings
ARIA2C_MAX_CONNECTIONS = _resolve_int(("download", "aria2c_max_connections"), default=8,
                                      env="SCRUTICELL_ARIA2C_CONNECTIONS")
ARIA2C_SPLIT = _resolve_int(("download", "aria2c_split"), default=8, env="SCRUTICELL_ARIA2C_SPLIT")
ARIA2C_MIN_SPLIT_SIZE = _resolve(("download", "aria2c_min_split_size"), default="10M",
                                 env="SCRUTICELL_ARIA2C_MIN_SPLIT_SIZE")
ARIA2C_BROWSER_MODE = _resolve_bool(("download", "aria2c_browser_mode"), default=True,
                                    env="SCRUTICELL_ARIA2C_BROWSER_MODE")

# Aspera high-speed download settings (fasp protocol, ~10x faster than HTTP)
# NOTE: NCBI discontinued public Aspera access for GEO data in 2024-2025.
# The key file and Aspera DNS endpoints are no longer available.
# Code is retained for future use or private Aspera servers.
ASPERA_ENABLED = _resolve_bool(("download", "aspera_enabled"), default=False)
ASPERA_HOST = _resolve(("download", "aspera_host"), default="ftp-private.ncbi.nlm.nih.gov")
ASPERA_USER = _resolve(("download", "aspera_user"), default="anonftp")
ASPERA_KEY_PATH = _resolve(
    ("download", "aspera_key_path"),
    default=str(Path.home() / ".aspera/connect/etc/asperaweb_id_dsa.openssh"),
    env="ASPERA_KEY_PATH",
)
ASPERA_BINARY = _resolve(("download", "aspera_binary"), default="ascp")
ASPERA_SPEED_LIMIT = _resolve(("download", "aspera_speed_limit"), default="100m")
ASPERA_TIMEOUT = _resolve_int(("download", "aspera_timeout"), default=300)

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
CONFIDENCE_THRESHOLD_AUTO = float(_resolve(("validation", "confidence_auto"), default="0.85"))
CONFIDENCE_THRESHOLD_WARN = float(_resolve(("validation", "confidence_warn"), default="0.60"))
# below 0.60 -> trigger reflexion

# Maximum matrix size for dense loading (before switching to sparse)
MAX_DENSE_CELLS = _resolve_int(("matrices", "max_dense_cells"), default=100_000)
MAX_DENSE_GENES = _resolve_int(("matrices", "max_dense_genes"), default=30_000)

# Logging
VERBOSE = _resolve_bool(("logging", "verbose"), default=False)


def get_output_path(gse_id: str) -> Path:
    """Return the resolved output path for a GSE accession."""
    return (Path.cwd() / OUTPUT_DIR / gse_id).resolve()


def get_download_path(gse_id: str) -> Path:
    """Return the resolved download path for a GSE accession."""
    return get_output_path(gse_id) / "download"
