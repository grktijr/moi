# moi_fczfc_sweep_with_calib.ps1
# 1. Run an I-H calibration at T_high (interactive: manual polarity switch required)
# 2. Apply that calibration to a sweep of fczfc runs

$ErrorActionPreference = "Stop"

# --------------------------------------------------------------------------
# Common parameters (shared between calibration and measurement)
# --------------------------------------------------------------------------
$OutRoot       = "D:\Users\Ren\20260623\pair2\Area5-20x\negative"
$PSU           = "hp"
$Tags          = "fczfc"

$Binning       = @(2, 2)
$ROI           = @(800, 800, 800, 800)
$ExposureMs    = 100
$Navg          = 400
$CoilOePerA    = 10.0

# Common temperatures
$THigh         = 9.0
$TLow          = 3.5

# Will be set after calibration completes; referenced in measurement loop
$CalibrationFile = "D:\Users\Ren\20260623\pair2\Area5-20x\negative\calibration\calib_20260623_1838.json"


# --------------------------------------------------------------------------
# Step 2: Run fczfc sweep with the just-created calibration
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Step 2: Run fczfc sweeps at each T_low for every run config
# --------------------------------------------------------------------------
$TLowList = @(3.5)

$Runs = @(
    @{ FCField =   0.0; Knots = @(0.0,   0.75); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.1; Knots = @(0.1,   0.75); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.2; Knots = @(0.2,   0.75); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.3; Knots = @(0.3,   0.75); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.5; Knots = @(0.5,   0.75); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.6; Knots = @(0.6,   0.74); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.7; Knots = @(0.7,   0.75); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.8; Knots = @(0.8,   0.75); Nums = @(2); Cycles = 1 },
    @{ FCField =   0.9; Knots = @(0.9,   0.75); Nums = @(2); Cycles = 1 },
    @{ FCField =   1.0; Knots = @(1.0,   0.75); Nums = @(2); Cycles = 1 },
    @{ FCField =   1.2; Knots = @(1.2,   0.75); Nums = @(2); Cycles = 1 },
    @{ FCField =   1.5; Knots = @(1.5,   0.74); Nums = @(2); Cycles = 1 },
    @{ FCField =   2.0; Knots = @(2.0,   0.74); Nums = @(2); Cycles = 1 }
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
            --coil-oe-per-a     $CoilOePerA 
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