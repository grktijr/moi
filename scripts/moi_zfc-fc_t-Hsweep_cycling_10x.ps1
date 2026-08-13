# moi_zfc_tsweep.ps1
$ErrorActionPreference = "Stop"

$Script  = "C:\Users\labuser\moi\moi_acquire.py"
$Python  = "C:\Users\labuser\miniconda3\envs\moi\python.exe"
$OutRoot = "D:\Users\Ren\20260604\pair2\Area2-10x"
$PSU     = "keysight"
$Tags    = "zfc"

$Runs = @(
    @{ THigh = 2.0; TLow = 2.0; Knots = @(0, 200, 0); Nums = @(70, 21) },
    @{ THigh = 9.0; TLow = 4.0; Knots = @(0, 150, 0); Nums = @(50, 16) },
    @{ THigh = 9.0; TLow = 5.0; Knots = @(0, 150, 0); Nums = @(50, 16) },
    @{ THigh = 9.0; TLow = 6.0; Knots = @(0, 100, 0); Nums = @(25, 11) },
    @{ THigh = 9.0; TLow = 7.0; Knots = @(0, 100, 0); Nums = @(25, 11) }
)

foreach ($run in $Runs) {
    $THigh = $run.THigh
    $TLow  = $run.TLow
    $Knots = $run.Knots
    $Nums  = $run.Nums

    $tagT = "{0}_T{1:N2}K" -f $Tags, $TLow
    $outT = Join-Path $OutRoot $tagT

    Write-Host "`n=== ZFC: T_high=$THigh K, T_low=$TLow K -> $outT ===" -ForegroundColor Cyan

    & $Python $Script zfc `
        --psu         $PSU `
        --out         $outT `
        --binning     2 2 `
        --roi         600 600 1200 1200 `
        --tag         $tagT `
        --t-high      $THigh `
        --t-low       $TLow `
        --field-knots $Knots `
        --field-nums  $Nums

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "ZFC run at T_low=$TLow failed with exit code $LASTEXITCODE; stopping sweep."
        break
    }
}

Write-Host "`n=== sweep complete ===" -ForegroundColor Green

# multi_H_fc.ps1
$base = "D:\Users\Ren\20260604\pair2\Area2-10x"

Write-Host "===== FC run =====" -ForegroundColor Cyan
python moi_acquire.py fc --psu keysight --t-high 9.0 --t-low 3.5 `
	--navg 400 --roi 400 400 1600 1600 --binning 2 2 `
    --fc-fields 0 1 2 5 10 20 50 100 --fc-cycles 9 `
	--out "$base\fc" --tag fc