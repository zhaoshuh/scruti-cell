"""Default configuration for the Scrut-Cell pipeline."""

import os
from pathlib import Path

# Project root
ROOT_DIR = Path(__file__).parent.parent.parent

# Config directory with seed data
CONFIG_DIR = ROOT_DIR / "config"

# Data download cache directory (relative to project, overridable via SCRUTICELL_DATA_DIR env)
DATA_DIR = Path(os.environ.get("SCRUTICELL_DATA_DIR", "output"))

# Default output directory
OUTPUT_DIR = Path.cwd()

# Neo4j defaults (for enterprise version)
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "scruticell"

# LLM defaults
LLM_MODEL = "deepseek-chat"
LLM_TEMPERATURE = 0.0  # deterministic for pipeline
LLM_BASE_URL = "https://api.deepseek.com"
LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")

# NCBI API key for PubMed / E-utilities (10 req/s instead of 3)
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "ca56372f2342212a2b20466e97f1d3c52907")

# Download settings
DOWNLOAD_TIMEOUT = 600  # seconds
DOWNLOAD_RETRIES = 3
DOWNLOAD_CHUNK_SIZE = 8192

# Validation thresholds
CONFIDENCE_THRESHOLD_AUTO = 0.85  # auto-accept above this
CONFIDENCE_THRESHOLD_WARN = 0.60  # warn but proceed
# below 0.60 -> trigger reflexion

# Maximum matrix size for dense loading (before switching to sparse)
MAX_DENSE_CELLS = 100_000
MAX_DENSE_GENES = 30_000
