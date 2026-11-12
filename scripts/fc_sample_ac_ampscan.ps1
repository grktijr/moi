$base = "D:\Users\Ren\20261103\Sam_bridge_5\Area1-10x\freqsweep__6.7K__offs_-0.7__freq_30khz_ampmax_100mA_pulse"

moi-acquire fc_sample_acsweep --psu k6221_dc `
    --t-high 9 --t-low 6.7 --coil-oe-per-a 333.3 `
    --sample-axis amp --fc-field -0.7 --sample-freq 30000 `
    --sample-values 10 20 30 40 50 60 70 80 90 100`
    --sample-dwell 0.1 `
    --sample-range-a 0.1 `
    --navg 400 --roi 600 800 1200 800 --binning 2 2 `
    --out "$base" --tag AmpSweep