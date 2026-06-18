# single_fc.ps1
$base = "D:\Users\Ren\20260614\pair2\Area4-50x"

Write-Host "===== FC run =====" -ForegroundColor Cyan
moi-acquire fc --psu hp --t-high 9.0 --t-low 3.5 `
	--navg 400 --roi 0 0 2400 2400 --binning 2 2 `
    --fc-fields 0 --fc-cycles 1 `
	--out "$base\fc-unplugged_coil" --tag fc-50x