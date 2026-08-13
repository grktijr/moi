# zfc_EuSe.ps1
$base = "D:\Users\Ren\20260526\EuSe-white\Area1-10x"

Write-Host "===== ZFC run =====" -ForegroundColor Cyan
python moi_acquire.py zfc --psu keysight --t-high 20.0 --t-low 3.5 `
    --field-knots 0 200 --field-nums 21 `
    --out "$base\zfc" --tag EuSe

Write-Host "===== All done =====" -ForegroundColor Green