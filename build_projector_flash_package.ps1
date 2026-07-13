param(
    [Parameter(Mandatory = $true)]
    [string]$BaseFirmware,
    [string]$OutputFirmware = ""
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv-pc\Scripts\python.exe"
$generator = Join-Path $root "tools\generate_fpp_patterns.py"
$patterns = Join-Path $root "generated_patterns_flash"
$packer = Join-Path $root "dlpc350_firmware_pack.exe"

if (-not $OutputFirmware) {
    $OutputFirmware = Join-Path $root "dist\PRO4500_patterns_firmware.bin"
}

if (-not (Test-Path -LiteralPath $BaseFirmware -PathType Leaf)) {
    throw "Base firmware was not found: $BaseFirmware"
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "PC Python environment was not found: $python"
}
if (-not (Test-Path -LiteralPath $packer -PathType Leaf)) {
    throw "Firmware packer was not found. Run build_native_control_panel.bat first."
}

$outputParent = Split-Path -Parent $OutputFirmware
if ($outputParent) {
    New-Item -ItemType Directory -Force -Path $outputParent | Out-Null
}

Write-Host "[flash-package] Generating native 912x1140 24-bit patterns..."
& $python $generator --output $patterns --width 912 --height 1140 --format bmp --rgb24
if ($LASTEXITCODE -ne 0) {
    throw "Native flash pattern generation failed with exit code $LASTEXITCODE."
}

Write-Host "[flash-package] Packing patterns into firmware..."
& $packer --base $BaseFirmware --patterns $patterns --output $OutputFirmware
if ($LASTEXITCODE -ne 0) {
    throw "Firmware packing failed with exit code $LASTEXITCODE."
}

Write-Host "[flash-package] READY: $OutputFirmware"
