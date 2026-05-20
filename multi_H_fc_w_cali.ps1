# multi_H_fc_w_cali.ps1
$base = "D:\Users\Ren\20260520\6SqHs\Area1-5x"

Write-Host "===== FC run =====" -ForegroundColor Cyan
python moi_acquire.py fc --psu keithley --t-high 9.0 --t-low 3.5 `
	--navg 100 --fc-fields 2 5 10 20 50 100 0 1 --fc-cycles 9 `
	--out "$base\fc" --tag fc

if ($LASTEXITCODE -ne 0) {
    Write-Host "FC run failed (exit code $LASTEXITCODE); skipping ZFC" -ForegroundColor Red
    exit 1
}

Write-Host "===== ZFC run =====" -ForegroundColor Cyan
python moi_acquire.py zfc --psu keithley --t-high 9.0 --t-low 9.0 `
    --field-knots 0 150 --field-nums 76 `
    --out "$base\calibration" --tag cali

Write-Host "===== All done =====" -ForegroundColor Green