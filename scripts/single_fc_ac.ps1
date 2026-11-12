# single_fc.ps1
$base = "D:\Users\Ren\20261012\Sam_bridge_4\Area3-10x\single_fc_ac\T-6.0K__offs--0.9__amp-1.0__freq-50"

Write-Host "===== FC run =====" -ForegroundColor Cyan
moi-acquire fc --psu k6221_ac --t-high 8.0 --t-low 6.0 --coil-oe-per-a 333.3 `
	--navg 400 --roi 600, 800, 1200, 800 --binning 2 2 `
    --fc-field -0.9 --fc-cycles 1 `
	--ac-amplitude 1.0 --ac-frequency 50 --ac-shape SIN `
	--out "$base"