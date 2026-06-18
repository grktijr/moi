# moi_zfc_tsweep.ps1
$ErrorActionPreference = "Stop"

$OutRoot = "D:\Users\Ren\20260615\pair2\Area3-50x"
$PSU     = "hp"
$Tags    = "zfc"

$Runs = @(
    @{ THigh = 2.0; TLow = 2.0; Knots = @(0, 5, 0); Nums = @(10, 11) }
#    @{ THigh = 9.0; TLow = 4.0; Knots = @(0, 150, 0); Nums = @(50, 16) },
#    @{ THigh = 9.0; TLow = 5.0; Knots = @(0, 150, 0); Nums = @(50, 16) },
#    @{ THigh = 9.0; TLow = 6.0; Knots = @(0, 100, 0); Nums = @(25, 11) },
#    @{ THigh = 9.0; TLow = 7.0; Knots = @(0, 100, 0); Nums = @(25, 11) }
)

foreach ($run in $Runs) {
    $THigh = $run.THigh
    $TLow  = $run.TLow
    $Knots = $run.Knots
    $Nums  = $run.Nums

    $tagT = "{0}_T{1:N2}K" -f $Tags, $TLow
    $outT = Join-Path $OutRoot $tagT

    Write-Host "`n=== ZFC: T_high=$THigh K, T_low=$TLow K -> $outT ===" -ForegroundColor Cyan

    moi-acquire zfc `
        --psu         $PSU `
        --out         $outT `
        --tag         $tagT `
        --t-high      $THigh `
        --t-low       $TLow `
        --field-knots $Knots `
        --field-nums  $Nums `
        --navg        100 `
        --binning     2 2 `
        --roi         400 400 1600 1600

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "ZFC run at T_low=$TLow failed with exit code $LASTEXITCODE; stopping sweep."
        break
    }
}

Write-Host "`n=== sweep complete ===" -ForegroundColor Green