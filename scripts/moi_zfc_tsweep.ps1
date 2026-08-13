# moi_zfc_multiT_with_calib.ps1
# 1. Run an I-H calibration at T_high (interactive: manual polarity switch required)
# 2. Run ZFC sweeps at each T_low with per-run field knots/nums
$ErrorActionPreference = "Stop"

# --------------------------------------------------------------------------
# Common parameters (shared between calibration and measurement)
# --------------------------------------------------------------------------
$OutRoot       = "D:\Users\Ren\20260723\pair4\Area1-20x"
$PSU           = "hp"
$Tags          = "zfc"

$Binning       = @(2, 2)
$ROI           = @(500, 500, 1400, 1400)
$ExposureMs    = 100
$Navg          = 8
$CoilOePerA    = 333.3

# Common temperatures
$THigh         = 9.0

# --------------------------------------------------------------------------
# Calibration parameters
# --------------------------------------------------------------------------
$CalibTag        = "calib_$(Get-Date -Format yyyyMMdd_HHmm)"
$CalibOutDir     = Join-Path $OutRoot "calibration"
$CalibSaveDir    = $CalibOutDir
$CalibImaxPos    = 0.5
$CalibImaxNeg    = 0.2
$CalibNSteps     = 20
$CalibNavg       = 16

$CalibrationFile = Join-Path $CalibSaveDir "$CalibTag.json"

# --------------------------------------------------------------------------
# Per-run configuration: one entry per T_low, each with its own knots/nums
# --------------------------------------------------------------------------
$Runs = @(
    @{ TLow = 3.5; Knots = @(0, 150, 300, 0);    Nums = @(50, 30, 31) },
    @{ TLow = 6.0; Knots = @(0, 150, 0);         Nums = @(50, 16) },
    @{ TLow = 7.0; Knots = @(0, 100, 0);         Nums = @(50, 21) },
    @{ TLow = 7.6; Knots = @(0, 80, 0);          Nums = @(40, 17) },
    @{ TLow = 7.7; Knots = @(0, 60, 0);          Nums = @(30, 13) }
)

# --------------------------------------------------------------------------
# Step 1: Run calibration
# --------------------------------------------------------------------------
Write-Host "`n========================================" -ForegroundColor Yellow
Write-Host "STEP 1: CALIBRATION RUN" -ForegroundColor Yellow
Write-Host "  Output: $CalibOutDir" -ForegroundColor Yellow
Write-Host "  This run pauses for manual polarity switch. Be present." -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow

moi-calibrate `
    --psu            $PSU `
    --out            $CalibOutDir `
    --tag            $CalibTag `
    --t              $THigh `
    --I-max-pos      $CalibImaxPos `
    --I-max-neg      $CalibImaxNeg `
    --n-steps        $CalibNSteps `
    --exposure-ms    $ExposureMs `
    --navg           $CalibNavg `
    --binning        $Binning `
    --roi            $ROI `
    --coil-oe-per-a  $CoilOePerA `
    --save-to        $CalibSaveDir

if ($LASTEXITCODE -ne 0) {
    Write-Error "Calibration failed with exit code $LASTEXITCODE; aborting before measurement runs."
    exit 1
}

if (-not (Test-Path $CalibrationFile)) {
    Write-Error "Calibration JSON not found at $CalibrationFile after calibration run; aborting."
    exit 1
}

Write-Host "`n[calibration ok] file: $CalibrationFile" -ForegroundColor Green

# --------------------------------------------------------------------------
# Human checkpoint before measurement
# --------------------------------------------------------------------------
Write-Host "`n========================================" -ForegroundColor Yellow
Write-Host "STEP 2: ZFC MEASUREMENT SWEEP" -ForegroundColor Yellow
Write-Host "  Calibration file: $CalibrationFile" -ForegroundColor Yellow
Write-Host "  Runs planned: $($Runs.Count)" -ForegroundColor Yellow
foreach ($r in $Runs) {
    Write-Host ("  T={0} K  knots={1}  nums={2}" -f `
                $r.TLow, ($r.Knots -join ','), ($r.Nums -join ',')) -ForegroundColor Yellow
}
Write-Host "  Press Enter to proceed, or Ctrl+C to abort." -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Read-Host "Continue?"

# --------------------------------------------------------------------------
# Step 2: ZFC at each T_low with per-run knots/nums
# --------------------------------------------------------------------------
$sweepAborted = $false

foreach ($run in $Runs) {
    $TLow  = $run.TLow
    $Knots = $run.Knots
    $Nums  = $run.Nums

    $tagT = "{0}_T{1:N2}K" -f $Tags, $TLow
    $outT = Join-Path $OutRoot $tagT

    Write-Host "`n=== ZFC: T_high=$THigh K, T_low=$TLow K -> $outT ===" -ForegroundColor Cyan
    Write-Host "    knots=$($Knots -join ','), nums=$($Nums -join ',')" -ForegroundColor DarkCyan

    moi-acquire zfc `
        --psu                    $PSU `
        --out                    $outT `
        --tag                    $tagT `
        --t-high                 $THigh `
        --t-low                  $TLow `
        --field-knots            $Knots `
        --field-nums             $Nums `
        --binning                $Binning `
        --roi                    $ROI `
        --exposure-ms            $ExposureMs `
        --navg                   $Navg `
        --coil-oe-per-a          $CoilOePerA `
        --apply-calibration `
        --calibration-file       $CalibrationFile

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "ZFC at T_low=$TLow K failed with exit code $LASTEXITCODE; stopping sweep."
        $sweepAborted = $true
        break
    }
}

if ($sweepAborted) {
    Write-Host "`n=== sweep aborted ===" -ForegroundColor Red
    exit 1
}

Write-Host "`n=== sweep complete ===" -ForegroundColor Green