# Deploy Reorder AI API to Azure App Service (no local Docker needed).
# Usage (from inventory-ai folder):
#   powershell -ExecutionPolicy Bypass -File deploy\azure-appservice.ps1
#
# Optional env overrides before run:
#   $env:AZ_RG = "WecommPos"
#   $env:AZ_LOCATION = "eastus2"
#   $env:AZ_APP = "reorder-ai-wecomm-api"
#   $env:AZ_PLAN = "reorder-ai-plan"

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Rg       = if ($env:AZ_RG) { $env:AZ_RG } else { "WecommPos" }
$Location = if ($env:AZ_LOCATION) { $env:AZ_LOCATION } else { "eastus2" }
$AppName  = if ($env:AZ_APP) { $env:AZ_APP } else { "reorder-ai-wecomm-api" }
$PlanName = if ($env:AZ_PLAN) { $env:AZ_PLAN } else { "reorder-ai-plan" }

Write-Host "Root:     $Root"
Write-Host "RG:       $Rg"
Write-Host "App:      $AppName"
Write-Host "Plan:     $PlanName"
Write-Host "Location: $Location"

$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$plan = az appservice plan show -g $Rg -n $PlanName 2>$null
$ErrorActionPreference = $prevEap
if (-not $plan) {
    Write-Host "Creating App Service plan $PlanName (B1)..."
    az appservice plan create -g $Rg -n $PlanName --sku B1 --is-linux -l $Location | Out-Null
}

$ErrorActionPreference = "Continue"
$app = az webapp show -g $Rg -n $AppName 2>$null
$ErrorActionPreference = $prevEap
if (-not $app) {
    Write-Host "Creating Web App $AppName..."
    az webapp create -g $Rg -n $AppName --plan $PlanName --runtime "PYTHON:3.11" | Out-Null
}

az webapp config set -g $Rg -n $AppName --startup-file "python -m uvicorn api.main:app --host 0.0.0.0 --port 8000" | Out-Null

az webapp config appsettings set -g $Rg -n $AppName --settings `
    SCM_DO_BUILD_DURING_DEPLOYMENT=true `
    WEBSITES_PORT=8000 `
    FORECAST_STORE_USE_BATCH=1 `
    FORECAST_STORE_USE_LIVE_SQL=1 `
    DETECT_ORDER_USE_LIVE_SQL=1 `
    SKU_UPLIFT_ENABLED=1 `
    UPLIFT_ENABLED=0 `
    ADS_LOOKBACK_DAYS=90 | Out-Null

$EnvFile = Join-Path $Root ".env"
if (Test-Path $EnvFile) {
    Write-Host "Loading DB settings from .env into App Settings..."
    $pairs = @{}
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $i = $line.IndexOf("=")
            if ($i -ge 1) {
                $k = $line.Substring(0, $i).Trim()
                $v = $line.Substring($i + 1).Trim().Trim('"').Trim("'")
                $pairs[$k] = $v
            }
        }
    }

    $dbHost = $pairs["WECOMM_DB_HOST"]
    if (-not $dbHost) { $dbHost = $pairs["DB_HOST"] }
    if (-not $dbHost -or $dbHost -eq "127.0.0.1" -or $dbHost -eq "localhost") {
        $dbHost = "wecomm.postgres.database.azure.com"
        Write-Host "Local tunnel detected - setting DB_HOST=$dbHost"
        Write-Host "IMPORTANT: Azure Postgres firewall must allow Azure services."
    }
    az webapp config appsettings set -g $Rg -n $AppName --settings `
        "DB_HOST=$dbHost" `
        "DB_PORT=5432" `
        "DB_SSLMODE=require" | Out-Null

    foreach ($key in @("DB_DATABASE", "DB_USERNAME", "DB_PASSWORD", "DB_CONNECTION", "TENANT_SCHEMA")) {
        if ($pairs.ContainsKey($key) -and $pairs[$key]) {
            az webapp config appsettings set -g $Rg -n $AppName --settings "$key=$($pairs[$key])" | Out-Null
        }
    }
} else {
    Write-Host "No .env found - set DB_* app settings in Azure Portal after deploy."
}

$Staging = Join-Path $env:TEMP "reorder-ai-deploy"
if (Test-Path $Staging) { Remove-Item $Staging -Recurse -Force }
New-Item -ItemType Directory -Path $Staging | Out-Null

foreach ($d in @("api", "config", "core", "database", "v2")) {
    Copy-Item (Join-Path $Root $d) (Join-Path $Staging $d) -Recurse
}
Copy-Item (Join-Path $Root "requirements.txt") (Join-Path $Staging "requirements.txt")
New-Item -ItemType Directory -Path (Join-Path $Staging "data\forecast_store") -Force | Out-Null
$Fs = Join-Path $Root "data\forecast_store"
if (Test-Path $Fs) {
    Copy-Item (Join-Path $Fs "*") (Join-Path $Staging "data\forecast_store") -Force
}

$Zip = Join-Path $env:TEMP "reorder-ai-api.zip"
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Compress-Archive -Path (Join-Path $Staging "*") -DestinationPath $Zip -Force
Write-Host "Zip: $Zip"

Write-Host "Deploying zip..."
az webapp deploy -g $Rg -n $AppName --src-path $Zip --type zip | Out-Null

$HostName = az webapp show -g $Rg -n $AppName --query defaultHostName -o tsv
$Url = "https://$HostName"
Write-Host ""
Write-Host "============================================"
Write-Host " API LIVE URL (give this to your TL)"
Write-Host " $Url"
Write-Host " Docs:   $Url/docs"
Write-Host " Health: $Url/api/health"
Write-Host " Detect: POST $Url/api/detect-order"
Write-Host "============================================"
Write-Host "Reuse next time: `$env:AZ_APP='$AppName'"
