# multi_T_zfc.ps1
$base = "D:\Users\Ren\20260601\pair3\Area1-5x"

Write-Host "===== ZFC run 1 =====" -ForegroundColor Cyan
python moi_acquire.py zfc --psu keysight --t-high 2.0 --t-low 2.0 `
    --field-knots 0 200 0 --field-nums 40 21 `
    --out "$base\2.0 K" --tag 2.0K

Write-Host "===== ZFC run 2 =====" -ForegroundColor Cyan
python moi_acquire.py zfc --psu keysight --t-high 9.0 --t-low 6.0 `
    --field-knots 0 100 0 --field-nums 20 11 `
    --out "$base\calibration" --tag 6.0K

Write-Host "===== All done =====" -ForegroundColor Green