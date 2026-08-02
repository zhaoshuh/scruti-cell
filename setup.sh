#!/bin/bash
set -e

echo "========================================"
echo "  Scrut-Cell Linux Setup"
echo "========================================"
echo ""

# 1. Check Python version
PYTHON=$(command -v python3.11 || command -v python3 || command -v python)
echo "[1/7] Checking Python..."
$PYTHON --version || { echo "Error: Python 3.11+ required"; exit 1; }

# 2. Install system dependencies
echo "[2/7] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv librdata-dev aria2 2>/dev/null || true

# 3. Create virtual environment
echo "[3/7] Creating virtual environment..."
$PYTHON -m venv venv
source venv/bin/activate

# 4. Install Python dependencies
echo "[4/7] Installing Python dependencies..."
pip install --upgrade pip -q
pip install -e ".[dev]" 2>&1 | tail -5

# 5. Configure environment variables
echo "[5/7] Setting up environment..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  Created .env — please edit it with your API keys"
fi

# 6. Create data directories
echo "[6/7] Creating data directories..."
mkdir -p ~/.scruticell/data
mkdir -p ~/.scruticell/cache/pdf

# 7. Run tests to verify
echo "[7/7] Running tests..."
python -m pytest tests/ -x --tb=short -q 2>&1 | tail -5

echo ""
echo "========================================"
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "    1. Edit .env to set your DEEPSEEK_API_KEY"
echo "    2. Run: source venv/bin/activate"
echo "    3. Run: scruticell --help"
echo "========================================"
