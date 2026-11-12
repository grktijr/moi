$base = "D:\Users\Ren\20261028\Sam_bridge_4\Area4-10x\freqsweep__7.2K__offs_30__amp_100mA_freqmax_100kHz_pulse"

moi-acquire fc_sample_acsweep --psu k6221_dc `
    --t-high 9 --t-low 7.2 --coil-oe-per-a 333.3 `
    --sample-axis freq --fc-field 30 --sample-amp 100 `
    --sample-values 100000 100000 100000 100000`
    --sample-dwell 0.1 `
    --sample-range-a 0.1 `
    --navg 400 --roi 600 800 1200 800 --binning 2 2 `
    --out "$base" --tag AmpSweep