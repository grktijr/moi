$base = "D:\Users\Ren\20261028\Sam_bridge_4\Area4-10x\dcpulse_7.2K_dc_-30Oe_currmax_-100mA"

moi-acquire fc_sample_dcpulse --psu k6221_dc --fc-field -30 `
  --t-high 9 --t-low 7.2 --coil-oe-per-a 333.3 `
  --sample-values -100 -100 -100 -100 -100 --sample-dwell 0.1 `
  --sample-range-a 0.1 `
  --navg 400 --roi 600 800 1200 800 --binning 2 2 `
  --out "$base" --tag dcpulse