# single_fc.ps1
$base = "D:\Users\Ren\20261111\MIT-strip1\Area1-10x\single_fc\T-3.5K_dc--0.5"

Write-Host "===== FC run =====" -ForegroundColor Cyan
moi-acquire fc --psu k6221_dc --t-high 5.0 --t-low 3.5 --coil-oe-per-a 333.3 `
	--navg 400 --roi 200, 750, 2000, 900 --binning 2 2 `
    --fc-field -0.5 --fc-cycles 1 `
	--out "$base" --tag fc-10x