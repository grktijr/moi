$base = "D:\Users\Ren\20261012\Sam_bridge_4\Area3-10x\ampsweep__7.3K__offs_-0.5__freq_100_ampmax_5-2"

moi-acquire fc_acsweep --psu k6221_ac `
    --t-high 8 --t-low 7.3 --coil-oe-per-a 333.3 `
    --axis amp --offset -0.5 --freq 100 `
    --values 0.1 0.5 1 2 3 4 5 `
    --ac-dwell 5 `
    --wave-range-a 0.01 `
    --cool-amp-a 1e-6 `
    --navg 400 --roi 600 800 1200 800 --binning 2 2 `
    --out "$base" --tag AmpSweep