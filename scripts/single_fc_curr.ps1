# single_fc.ps1
$base = "D:\Users\Ren\20261110\Sam_bridge_5\Area1-10x\single_fc_curr\T-3.5K_dc--0.7_ac-30000hz-10mA"

Write-Host "===== FC run =====" -ForegroundColor Cyan
moi-acquire fc --psu k6221_dc --t-high 9.0 --t-low 3.5 --coil-oe-per-a 333.3 `
	--navg 400 --roi 600, 800, 1200, 800 --binning 2 2 `
    --fc-field -0.7 `
	--sample-current --sample-amp-ma 10 --sample-freq 30000 --sample-shape SIN `
	--out "$base" --tag fc-10x