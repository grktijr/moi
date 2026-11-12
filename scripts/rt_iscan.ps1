# rt_iscan.ps1 — R-T measured at a series of sample currents.
# Each current gets its own moi-rt run (CSV + PNG), so you can compare how the
# R-T curve / transition shifts with measurement current.
$ErrorActionPreference = "Stop"

$OutRoot = "D:\Users\Ren\20261029\Sam_bridge_5\rt"

# --- sample currents to sweep (mA) ---
$ISampleList = @(1)

# --- fixed R-T parameters ---
$TStart   = 3.5     # K
$TEnd     = 8.5    # K
$TStep    = 0.1     # K setpoint increment
$RampRate = 0.5     # K/min
$RevSettle = 0.3    # s after each +/-I reversal
$Both     = $true   # up+down each run? set $false for single up-sweep
$Nplc     = 5.0
$Compliance = 20.0

# --- pre-flight: cryostat reachable? (same pattern as your FC orchestrators) ---
Write-Host "Checking cryostat TCP 192.168.0.2:7773 ..." -ForegroundColor DarkCyan
$probe = Test-NetConnection -ComputerName 192.168.0.2 -Port 7773 -WarningAction SilentlyContinue
if (-not $probe.TcpTestSucceeded) {
    Write-Warning "Cryostat not reachable on 192.168.0.2:7773 : aborting."
    exit 1
}

# --- human checkpoint ---
Write-Host "`n========================================" -ForegroundColor Yellow
Write-Host "R-T CURRENT SCAN" -ForegroundColor Yellow
Write-Host "  Currents (mA): $($ISampleList -join ', ')" -ForegroundColor Yellow
Write-Host "  T sweep: $TStart -> $TEnd K, step $TStep K at $RampRate K/min" -ForegroundColor Yellow
Write-Host "  Direction: $(if ($Both) {'up + down'} else {'up only'})" -ForegroundColor Yellow
Write-Host "  Runs: $($ISampleList.Count)" -ForegroundColor Yellow
Write-Host "  Press Enter to proceed, Ctrl+C to abort." -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Read-Host "Continue?"

# --- the scan ---
$aborted = $false
$done = 0
$total = $ISampleList.Count

foreach ($I in $ISampleList) {

    $tag = "rt_I{0:N2}mA" -f $I
    $out = Join-Path $OutRoot ("rt_" + $tag)

    $done++
    Write-Host "`n===== [$done/$total] R-T at I = $I mA -> $out =====" -ForegroundColor Cyan

    $cmd = @(
        "--t-start", $TStart, "--t-end", $TEnd,
        "--i-sample", $I,
        "--t-step", $TStep, "--ramp-rate", $RampRate,
        "--reversal-settle", $RevSettle,
        "--compliance", $Compliance,
        "--voltmeter-nplc", $Nplc,
        "--out", $out, "--tag", $tag
    )
    if ($Both) { $cmd += "--both" }

    moi-rt @cmd

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "R-T at I=$I mA failed (exit $LASTEXITCODE); stopping scan."
        $aborted = $true
        break
    }
}

if ($aborted) {
    Write-Host "`n===== scan aborted =====" -ForegroundColor Red
    exit 1
}

Write-Host "`n===== all $total R-T runs complete =====" -ForegroundColor Green