# moi_fc_with_calibration.ps1
# Minimal pipeline: calibration → fc measurement with calibrated H output

$ErrorActionPreference = "Stop"

# --------------------------------------------------------------------------
# Common parameters — shared between calibration and measurement
# --------------------------------------------------------------------------
$OutRoot     = "D:\Users\Ren\20260617\test"
$PSU         = "hp"

$Binning     = @(2, 2)
$ROI         = @(400, 400, 1600, 1600)
$ExposureMs  = 100
$Navg        = 400
$CoilOePerA  = 333.3

# --------------------------------------------------------------------------
# Step 1: Calibration (interactive — be present for polarity switch)
# --------------------------------------------------------------------------
$CalibTag        = "calib_$(Get-Date -Format yyyyMMdd_HHmm)"
$CalibOutDir     = Join-Path $OutRoot "calibration"
$CalibTempK      = 9.0          # above Tc
$CalibImaxPos    = 0.1          # ~133 Oe positive — past your 100 Oe FC range
$CalibImaxNeg    = 0.1         # ~50 Oe negative — stay clear of extinction
$CalibNSteps     = 25

$CalibrationFile = Join-Path $CalibOutDir "$CalibTag.json"

Write-Host "`n========================================" -ForegroundColor Yellow
Write-Host "STEP 1: CALIBRATION (interactive)" -ForegroundColor Yellow
Write-Host "  Be present for manual polarity switch." -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow

moi-calibrate `
    --psu            $PSU `
    --out            $CalibOutDir `
    --tag            $CalibTag `
    --save-to        $CalibOutDir `
    --t              $CalibTempK `
    --I-max-pos      $CalibImaxPos `
    --I-max-neg      $CalibImaxNeg `
    --n-steps        $CalibNSteps `
    --exposure-ms    $ExposureMs `
    --navg           8 `
    --binning        $Binning `
    --roi            $ROI `
    --coil-oe-per-a  $CoilOePerA

if ($LASTEXITCODE -ne 0) {
    Write-Error "Calibration failed (exit code $LASTEXITCODE); aborting."
    exit 1
}

if (-not (Test-Path $CalibrationFile)) {
    Write-Error "Calibration JSON not found at $CalibrationFile; aborting."
    exit 1
}

Write-Host "`n[calibration ok] $CalibrationFile" -ForegroundColor Green

# --------------------------------------------------------------------------
# Step 2: FC measurement with calibration applied
# --------------------------------------------------------------------------
$FcTag       = "fc_$(Get-Date -Format yyyyMMdd_HHmm)"
$FcOutDir    = Join-Path $OutRoot $FcTag

$FcTHigh     = 9.0
$FcTLow      = 3.5
$FcFields    = @(10)
$FcCycles    = 1

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "STEP 2: FC MEASUREMENT" -ForegroundColor Cyan
Write-Host "  Fields: $FcFields Oe" -ForegroundColor Cyan
Write-Host "  Cycles per field: $FcCycles" -ForegroundColor Cyan
Write-Host "  Output: $FcOutDir" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

moi-acquire fc `
    --psu                    $PSU `
    --out                    $FcOutDir `
    --tag                    $FcTag `
    --t-high                 $FcTHigh `
    --t-low                  $FcTLow `
    --fc-fields              $FcFields `
    --fc-cycles              $FcCycles `
    --binning                $Binning `
    --roi                    $ROI `
    --exposure-ms            $ExposureMs `
    --navg                   $Navg `
    --coil-oe-per-a          $CoilOePerA `
    --apply-calibration `
    --calibration-file       $CalibrationFile `
    --scale-for-calibration

if ($LASTEXITCODE -ne 0) {
    Write-Error "FC measurement failed (exit code $LASTEXITCODE)."
    exit 1
}

Write-Host "`n=== pipeline complete ===" -ForegroundColor Green
Write-Host "Calibration: $CalibrationFile" -ForegroundColor Green
Write-Host "Measurement: $FcOutDir" -ForegroundColor Green