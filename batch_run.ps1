$key = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
$env:DEEPSEEK_API_KEY = $key

$gses = @(
    "GSE290746","GSE269042","GSE303943","GSE284668",
    "GSE317309","GSE328098","GSE294571","GSE284443",
    "GSE297632","GSE312138","GSE295600","GSE276139",
    "GSE304864","GSE295518","GSE294490","GSE285606"
)
$dataCache = "C:\Users\Administrator\.scruticell\data"

foreach ($gse in $gses) {
    Write-Host "`n========================================"
    Write-Host "  Processing: $gse"
    Write-Host "========================================"
    
    # Inspect
    Write-Host "[1/3] Inspect..."
    New-Item -ItemType Directory -Path "E:\Scruti-Cell\batch\$gse" -Force | Out-Null
    & "E:\Scruti-Cell\venv\Scripts\scruticell.exe" inspect $gse -o "E:\Scruti-Cell\batch\$gse" 2>&1 | Out-Null
    
    # Copy data files to cache
    if (Test-Path "E:\Scruti-Cell\$gse") {
        Copy-Item "E:\Scruti-Cell\$gse\*" -Destination "$dataCache\$gse\" -Recurse -Force -ErrorAction SilentlyContinue
    }
    
    # Plan
    Write-Host "[2/3] Plan..."
    & "E:\Scruti-Cell\venv\Scripts\scruticell.exe" plan $gse --report "E:\Scruti-Cell\batch\$gse\$($gse)_report.json" -o "E:\Scruti-Cell\batch\$gse" 2>&1 | Out-Null
    
    # Execute
    Write-Host "[3/3] Execute..."
    $result = & "E:\Scruti-Cell\venv\Scripts\scruticell.exe" execute $gse --plan-file "E:\Scruti-Cell\batch\$gse\$($gse)_plan.json" --report-file "E:\Scruti-Cell\batch\$gse\$($gse)_report.json" -o "E:\Scruti-Cell\batch\$gse" --no-download --keep-data 2>&1 | Out-String
    
    if ($result -match "SUCCESS") {
        $cells = if ($result -match "Cells:\s+(\d+)") { [int]$Matches[1] } else { "?" }
        $genes = if ($result -match "Genes:\s+(\d+)") { [int]$Matches[1] } else { "?" }
        $batches = if ($result -match "Batches:\s+(\d+)") { [int]$Matches[1] } else { "?" }
        Write-Host "  SUCCESS: $cells cells, $genes genes, $batches batches" -ForegroundColor Green
    } else {
        Write-Host "  FAILED" -ForegroundColor Red
    }
}

Write-Host "`nDONE! All 16 GSEs processed."
