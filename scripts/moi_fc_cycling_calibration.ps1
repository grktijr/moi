# moi_fczfc_sweep_with_calib.ps1
# 1. Run an I-H calibration at T_high (interactive: manual polarity switch required)
# 2. Apply that calibration to a sweep of fczfc runs

$ErrorActionPreference = "Stop"

# --------------------------------------------------------------------------
# Common parameters (shared between calibration and measurement)
# --------------------------------------------------------------------------
$OutRoot       = "D:\Users\Ren\20260731\pair4\Area3-10x"
$PSU           = "k6221_dc"
$Tags          = "fczfc"

$Binning       = @(2, 2)
$ROI           = @(800, 800, 800, 800)
$ExposureMs    = 100
$Navg          = 1600
$CoilOePerA    = 10.0

# Common temperatures
$THigh         = 9.0
$TLow          = 3.5

# --------------------------------------------------------------------------
# Calibration parameters
# --------------------------------------------------------------------------
$CalibTag        = "calib_$(Get-Date -Format yyyyMMdd_HHmm)"
$CalibOutDir     = Join-Path $OutRoot "calibration"
$CalibSaveDir    = $CalibOutDir
$CalibImaxPos    = 0.1    # A;
$CalibImaxNeg    = -0.1    # A;
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
    --navg         20 `
    --binning      $Binning `
    --roi          $ROI `
    --coil-oe-per-a $CoilOePerA `
    --max-poly-order 3 `
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
# --------------------------------------------------------------------------
# Step 2: Run fczfc sweeps at each T_low for every run config
# --------------------------------------------------------------------------
$TLowList = @(3.5, 6.0, 7.5, 7.6, 7.7, 7.8, 7.9)

$Runs = @(
    @{ FCField =  -1.0; Knots = @(-1.0,  -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =  -0.9; Knots = @(-0.9,  -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =  -0.8; Knots = @(-0.8,  -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =  -0.7; Knots = @(-0.7,  -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =  -0.6; Knots = @(-0.6,  -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =  -0.5; Knots = @(-0.5,  -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =  -0.4; Knots = @(-0.4,  -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =  -0.3; Knots = @(-0.3,  -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =  -0.2; Knots = @(-0.2,  -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =  -0.1; Knots = @(-0.1,  -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.0; Knots = @(0.0,   -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.1; Knots = @(0.1,   -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.2; Knots = @(0.2,   -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.3; Knots = @(0.3,   -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.4; Knots = @(0.4,   -0.42); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.5; Knots = @(0.5,   -0.42); Nums = @(2); Cycles = 1 }
)

$sweepAborted = $false

foreach ($TLow in $TLowList) {

    Write-Host "`n############################################" -ForegroundColor Magenta
    Write-Host "### T_low = $TLow K  ($($Runs.Count) runs)" -ForegroundColor Magenta
    Write-Host "############################################" -ForegroundColor Magenta

    foreach ($run in $Runs) {
        $FCField = $run.FCField
        $Knots   = $run.Knots
        $Nums    = $run.Nums
        $Cycles  = $run.Cycles

        $tagT = "{0}_FC{1:N2}Oe_T{2:N2}K" -f $Tags, $FCField, $TLow
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
            --calibration-file  $CalibrationFile `
            --scale-for-calibration

        if ($LASTEXITCODE -ne 0) {
            Write-Warning "FCZFC at T_low=$TLow K, FC=$FCField Oe failed with exit code $LASTEXITCODE; stopping sweep."
            $sweepAborted = $true
            break
        }
    }

    if ($sweepAborted) { break }
}

Write-Host "`n=== sweep complete ===" -ForegroundColor Green