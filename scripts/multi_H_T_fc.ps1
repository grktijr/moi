# moi_fc_smallmag_multiT.ps1
# 1. Run an I-H calibration at T_high (uses bipolar 6221; no manual polarity swap).
# 2. Run FC multi-field sweeps at each T_low with the calibration applied and scaled.
$ErrorActionPreference = "Stop"

# --------------------------------------------------------------------------
# Common parameters
# --------------------------------------------------------------------------
$OutRoot     = "D:\Users\Ren\20260812\MIT-SR\Area11-10x"
$PSU         = "k6221_dc"
$Tag         = "fc"

$Binning     = @(2, 2)
$ROI         = @(0, 600, 2400, 1200)
$ExposureMs  = 100
$Navg        = 400
$CoilOePerA  = 10.0          # small magnet calibration

# Common temperatures
$THigh       = 9.0

# --------------------------------------------------------------------------
# Calibration parameters
# --------------------------------------------------------------------------
$CalibTag        = "calib_$(Get-Date -Format yyyyMMdd_HHmm)"
$CalibOutDir     = Join-Path $OutRoot "calibration"
$CalibSaveDir    = $CalibOutDir
$CalibImaxPos    =  0.1     # A;  ~+0.6 Oe at 10 Oe/A (covers +FC range with margin)
$CalibImaxNeg    = -0.1     # A;  signed for bipolar 6221; covers -FC range
$CalibNSteps     = 20
$CalibNavg       = 20         # lighter averaging OK for calibration

$CalibrationFile = Join-Path $CalibSaveDir "$CalibTag.json"

# --------------------------------------------------------------------------
# FC run parameters (per-T_low sweep of anchor fields)
# --------------------------------------------------------------------------
$TLowList    = @(3.5, 8.5)

$FCStart     = -1.0     # Oe
$FCEnd       =  1.0     # Oe
$FCCount     = 201       # number of anchors, inclusive of both endpoints
$FCCycles    = 1

# Generate evenly-spaced field anchors (once; same at every T_low)
if ($FCCount -lt 2) {
    $FCFields = @($FCStart)
} else {
    $FCFields = 0..($FCCount - 1) | ForEach-Object {
        $FCStart + ($FCEnd - $FCStart) * $_ / ($FCCount - 1)
    }
}

# --------------------------------------------------------------------------
# Step 1: Calibration
# --------------------------------------------------------------------------
Write-Host "`n========================================" -ForegroundColor Yellow
Write-Host "STEP 1: CALIBRATION RUN" -ForegroundColor Yellow
Write-Host "  T_high: $THigh K" -ForegroundColor Yellow
Write-Host "  I range: $CalibImaxNeg to $CalibImaxPos A" -ForegroundColor Yellow
Write-Host "  Steps per polarity: $CalibNSteps" -ForegroundColor Yellow
Write-Host "  Output: $CalibOutDir" -ForegroundColor Yellow
Write-Host "  Bipolar PSU: no manual polarity swap needed" -ForegroundColor Yellow
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
    --max-poly-order 3 `
    --save-to        $CalibSaveDir

if ($LASTEXITCODE -ne 0) {
    Write-Error "Calibration failed with exit code $LASTEXITCODE; aborting before measurement runs."
    exit 1
}

if (-not (Test-Path $CalibrationFile)) {
    Write-Error "Calibration JSON not found at $CalibrationFile; aborting."
    exit 1
}

Write-Host "`n[calibration ok] file: $CalibrationFile" -ForegroundColor Green

# --------------------------------------------------------------------------
# Human checkpoint before measurement
# --------------------------------------------------------------------------
Write-Host "`n========================================" -ForegroundColor Yellow
Write-Host "STEP 2: FC MEASUREMENT SWEEPS" -ForegroundColor Yellow
Write-Host "  Calibration: $CalibrationFile" -ForegroundColor Yellow
Write-Host "  T_low list: $($TLowList -join ', ') K" -ForegroundColor Yellow
Write-Host "  FC anchors: $FCStart -> $FCEnd Oe in $FCCount steps" -ForegroundColor Yellow
Write-Host "    $($FCFields | ForEach-Object { '{0:N3}' -f $_ })" -ForegroundColor DarkYellow
Write-Host "  Cycles per anchor: $FCCycles" -ForegroundColor Yellow
Write-Host "  Coil: $CoilOePerA Oe/A" -ForegroundColor Yellow
Write-Host "  Total FC runs: $($TLowList.Count)" -ForegroundColor Yellow
Write-Host "  Press Enter to proceed, or Ctrl+C to abort." -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Read-Host "Continue?"

# --------------------------------------------------------------------------
# Step 2: FC multi-field at each T_low
# --------------------------------------------------------------------------
$sweepAborted = $false

foreach ($TLow in $TLowList) {

    $tagT = "{0}_T{1:N2}K" -f $Tag, $TLow
    $outT = Join-Path $OutRoot ("fc-multifield_" + $tagT)

    Write-Host "`n=== FC: T_high=$THigh K, T_low=$TLow K -> $outT ===" -ForegroundColor Cyan
    Write-Host "    $FCCount anchors from $FCStart to $FCEnd Oe" -ForegroundColor DarkCyan

    moi-acquire fc `
        --psu                    $PSU `
        --out                    $outT `
        --tag                    $tagT `
        --t-high                 $THigh `
        --t-low                  $TLow `
        --fc-fields              $FCFields `
        --fc-cycles              $FCCycles `
        --binning                $Binning `
        --roi                    $ROI `
        --exposure-ms            $ExposureMs `
        --navg                   $Navg `
        --coil-oe-per-a          $CoilOePerA `
        --apply-calibration `
        --calibration-file       $CalibrationFile `
        --scale-for-calibration

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "FC at T_low=$TLow K failed with exit code $LASTEXITCODE; stopping sweep."
        $sweepAborted = $true
        break
    }
}

if ($sweepAborted) {
    Write-Host "`n=== sweep aborted ===" -ForegroundColor Red
    exit 1
}

Write-Host "`n=== all sweeps complete ===" -ForegroundColor Green