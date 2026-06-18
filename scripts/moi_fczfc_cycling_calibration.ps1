# moi_fczfc_sweep_with_calib.ps1
# 1. Run an I-H calibration at T_high (interactive: manual polarity switch required)
# 2. Apply that calibration to a sweep of fczfc runs

$ErrorActionPreference = "Stop"

# --------------------------------------------------------------------------
# Common parameters (shared between calibration and measurement)
# --------------------------------------------------------------------------
$OutRoot       = "D:\Users\Ren\20260617\pair2\Area5-20x"
$PSU           = "hp"
$Tags          = "fczfc"

$Binning       = @(2, 2)
$ROI           = @(400, 400, 1600, 1600)
$ExposureMs    = 100
$Navg          = 400
$CoilOePerA    = 333.3

# Common temperatures
$THigh         = 9.0
$TLow          = 3.5

# --------------------------------------------------------------------------
# Calibration parameters
# --------------------------------------------------------------------------
$CalibTag        = "calib_$(Get-Date -Format yyyyMMdd_HHmm)"
$CalibOutDir     = Join-Path $OutRoot "calibration"
$CalibSaveDir    = $CalibOutDir
$CalibImaxPos    = 0.4    # A; covers ~133 Oe positive (more than 100 Oe sweep range)
$CalibImaxNeg    = 0.2    # A; covers ~-133 Oe negative
$CalibNSteps     = 25     # points per polarity pass

# Will be set after calibration completes; referenced in measurement loop
$CalibrationFile = Join-Path $CalibSaveDir "$CalibTag.json"

# --------------------------------------------------------------------------
# Step 1: Run calibration
# --------------------------------------------------------------------------
Write-Host "`n========================================" -ForegroundColor Yellow
Write-Host "STEP 1: CALIBRATION RUN" -ForegroundColor Yellow
Write-Host "  Output: $CalibOutDir" -ForegroundColor Yellow
Write-Host "  This run pauses for manual polarity switch. Be present." -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow

moi-calibrate `
    --psu          $PSU `
    --out          $CalibOutDir `
    --tag          $CalibTag `
    --t            $THigh `
    --I-max-pos    $CalibImaxPos `
    --I-max-neg    $CalibImaxNeg `
    --n-steps      $CalibNSteps `
    --exposure-ms  $ExposureMs `
    --navg         8 `
    --binning      $Binning `
    --roi          $ROI `
    --coil-oe-per-a $CoilOePerA `
    --save-to      $CalibSaveDir

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
# Optional: pause for human verification before measurement starts
# --------------------------------------------------------------------------
Write-Host "`n========================================" -ForegroundColor Yellow
Write-Host "STEP 2: MEASUREMENT SWEEP" -ForegroundColor Yellow
Write-Host "  Calibration file: $CalibrationFile" -ForegroundColor Yellow
Write-Host "  Press Enter to proceed, or Ctrl+C to abort." -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Read-Host "Continue?"

# --------------------------------------------------------------------------
# Step 2: Run fczfc sweep with the just-created calibration
# --------------------------------------------------------------------------
$Runs = @(
    @{ FCField =   0.0; Knots = @(0,   0); Nums = @(2); Cycles = 9 },
    @{ FCField =   1.0; Knots = @(1,   0); Nums = @(2); Cycles = 9 },
    @{ FCField =   2.0; Knots = @(2,   0); Nums = @(2); Cycles = 9 },
    @{ FCField =   5.0; Knots = @(5,   0); Nums = @(2); Cycles = 9 },
    @{ FCField =  10.0; Knots = @(10,  0); Nums = @(2); Cycles = 9 },
    @{ FCField =  20.0; Knots = @(20,  0); Nums = @(2); Cycles = 9 },
    @{ FCField =  50.0; Knots = @(50,  0); Nums = @(2); Cycles = 9 },
    @{ FCField = 100.0; Knots = @(100, 0); Nums = @(2); Cycles = 9 }
)

foreach ($run in $Runs) {
    $FCField = $run.FCField
    $Knots   = $run.Knots
    $Nums    = $run.Nums
    $Cycles  = $run.Cycles

    $tagT = "{0}_FC{1:N1}Oe_T{2:N2}K" -f $Tags, $FCField, $TLow
    $outT = Join-Path $OutRoot $tagT

    Write-Host "`n=== FCZFC: T_high=$THigh K, T_low=$TLow K, FC=$FCField Oe, cycles=$Cycles -> $outT ===" -ForegroundColor Cyan

    moi-acquire fczfc `
        --psu               $PSU `
        --out               $outT `
        --tag               $tagT `
        --t-high            $THigh `
        --t-low             $TLow `
        --fc-field          $FCField `
        --field-knots       $Knots `
        --field-nums        $Nums `
        --fczfc-cycles      $Cycles `
        --binning           $Binning `
        --roi               $ROI `
        --exposure-ms       $ExposureMs `
        --navg              $Navg `
        --coil-oe-per-a     $CoilOePerA `
        --apply-calibration `
        --calibration-file  $CalibrationFile

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "FCZFC at FC=$FCField Oe failed with exit code $LASTEXITCODE; stopping sweep."
        break
    }
}

Write-Host "`n=== sweep complete ===" -ForegroundColor Green