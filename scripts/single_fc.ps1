# single_fc.ps1
$base = "D:\Users\Ren\20260812\MIT-SR\Area11-10x\single_fc\-0.8 Oe - 3"

Write-Host "===== FC run =====" -ForegroundColor Cyan
moi-acquire fc --psu k6221_dc --t-high 9.0 --t-low 3.5 --coil-oe-per-a 10.0 `
	--navg 400 --roi 0 600 2400 1200 --binning 2 2 `
    --fc-fields -0.8 --fc-cycles 1 `
	--out "$base" --tag fc-10x