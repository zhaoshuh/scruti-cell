#!/bin/bash
set -e

export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY not set}"

GSES=(
    "GSE290746" "GSE269042" "GSE303943" "GSE284668"
    "GSE317309" "GSE328098" "GSE294571" "GSE284443"
    "GSE297632" "GSE312138" "GSE295600" "GSE276139"
    "GSE304864" "GSE295518" "GSE294490" "GSE285606"
)

DATA_CACHE="${SCRUTICELL_DATA_DIR:-$HOME/.scruticell/data}"
OUTPUT_DIR="./output"

for GSE in "${GSES[@]}"; do
    echo ""
    echo "========================================"
    echo "  Processing: $GSE"
    echo "========================================"

    echo "[1/3] Inspect..."
    scruticell inspect "$GSE" -o "$OUTPUT_DIR/$GSE" 2>&1 | tail -3

    if [ -d "$OUTPUT_DIR/$GSE/download" ]; then
        mkdir -p "$DATA_CACHE/$GSE"
        cp -r "$OUTPUT_DIR/$GSE/download/"* "$DATA_CACHE/$GSE/" 2>/dev/null || true
    fi

    echo "[2/3] Plan..."
    scruticell plan "$GSE" --report "$OUTPUT_DIR/$GSE/${GSE}_report.json" -o "$OUTPUT_DIR/$GSE" 2>&1 | tail -3

    echo "[3/3] Execute..."
    scruticell execute "$GSE" \
        --plan-file "$OUTPUT_DIR/$GSE/${GSE}_plan.json" \
        --report-file "$OUTPUT_DIR/$GSE/${GSE}_report.json" \
        -o "$OUTPUT_DIR/$GSE" --no-download --keep-data 2>&1 | tail -10
done

echo ""
echo "DONE! All ${#GSES[@]} GSEs processed."
