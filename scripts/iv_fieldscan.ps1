# iv_fieldscan.ps1 - field-cooled I-V at a series of fields.
# For each field: field-cool (apply field above Tc, cool through Tc in field),
# then run the I-V at the measurement T. The new moi-iv sweeps TWO zero-origin
# branches per run (0 -> +Imax and 0 -> -Imax), each from the virgin SC state,
# so +Ic and -Ic are reached without a prior opposite-polarity excursion. One
# moi-iv run per field, each producing its own CSV + PNG, so you can compare
# how the I-V (and critical current) shifts with the field-cooled field.
#
# Runs unattended: no human "press Enter" checkpoint.
$ErrorActionPreference = "Stop"

$OutRoot = "D:\Users\Ren\20261103\Sam_bridge_5\iv_fc"

# --- fields to sweep (Oe) ---
$FieldList = @(30)

# --- fixed field-cool + I-V parameters ---
$PSU      = "k6221_dc"   # magnet, GPIB 30
$Coil     = 333.3        # Oe/A (10 = small magnet, 333 = big magnet)
$THigh    = 9.0          # above-Tc field-apply temperature (K)
$TMeas    = 6.7          # I-V measurement temperature (K)
$IMax     = 100.0        # per-branch sweep amplitude: 0 -> +/-IMax (mA)
$IStep    = 1.0          # coarse current step (mA)
$Settle   = 0.3          # s after each current before reading V (continuous mode)
$Compliance = 10.0       # sample-source compliance (V)
$Nplc     = 5.0
$ProbeMode = "2-probe"
$SampleVisa = "GPIB0::29::INSTR"

# --- pulsed current (off by default = continuous settle mode as before) ---
# Pulse to I, read V, return to 0 between points to cut self-heating.
$Pulsed     = $False
$PulseWidth = 0.05       # s to settle after applying I
$Dwell      = 2.0        # s at 0 between pulses (pulsed mode)

# --- auto-rescan around the Ic jump (new in moi-iv; off by default) ---
# Coarse sweep detects the V jump (dV > VJump between consecutive points),
# then re-sweeps a +/- window around it at a fine step; the fine points
# REPLACE the coarse ones in that window. At IStep=1 mA this pins Ic far
# tighter than the coarse grid without paying fine-step time everywhere.
$AutoRescan   = $False
$VJump        = 5e-2     # V, jump threshold between consecutive coarse points
$RescanWindow = 1        # rescan +/- this many coarse steps around the jump
$IStepFine    = 0.0      # mA fine step (0 = let moi-iv default = IStep/10)

# --- current-limit sanity check (|field/coil| must fit the magnet range) ---
# Small magnet on 100 mA range reaches ~1 Oe; big magnet ~33 Oe.
$MagLimitA = 0.6       # 6221 100 mA range + 5% headroom
foreach ($B in $FieldList) {
    $Ia = [math]::Abs($B / $Coil)
    if ($Ia -gt $MagLimitA) {
        Write-Warning ("Field {0} Oe needs {1:N4} A (> {2} A limit at coil {3} Oe/A). Aborting." -f $B, $Ia, $MagLimitA, $Coil)
        exit 1
    }
}

# --- pre-flight: cryostat reachable (automated, no human input) ---
Write-Host "Checking cryostat TCP 192.168.0.2:7773 ..." -ForegroundColor DarkCyan
$probe = Test-NetConnection -ComputerName 192.168.0.2 -Port 7773 -WarningAction SilentlyContinue
if (-not $probe.TcpTestSucceeded) {
    Write-Warning "Cryostat not reachable on 192.168.0.2:7773 - aborting."
    exit 1
}

# --- banner (no checkpoint; scan starts immediately) ---
Write-Host "`n========================================" -ForegroundColor Yellow
Write-Host "FIELD-COOLED I-V FIELD SCAN (unattended)" -ForegroundColor Yellow
Write-Host ("  Fields (Oe): {0}" -f ($FieldList -join ', ')) -ForegroundColor Yellow
Write-Host ("  Field-cool: apply at T_high={0} K, cool to T_meas={1} K, coil {2} Oe/A" -f $THigh, $TMeas, $Coil) -ForegroundColor Yellow
Write-Host ("  I sweep: two branches 0 -> +/-{0} mA, coarse step {1} mA" -f $IMax, $IStep) -ForegroundColor Yellow
if ($Pulsed)     { Write-Host ("  pulsed: width={0} s, dwell={1} s" -f $PulseWidth, $Dwell) -ForegroundColor Yellow }
if ($AutoRescan) { Write-Host ("  auto-rescan: v-jump={0} V, window=+/-{1} coarse steps" -f $VJump, $RescanWindow) -ForegroundColor Yellow }
Write-Host ("  Runs: {0}" -f $FieldList.Count) -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow

# --- the scan ---
$aborted = $false
$done = 0
$total = $FieldList.Count

foreach ($B in $FieldList) {

    $tag = "iv_fc_B{0:N2}Oe_T{1:N2}K" -f $B, $TMeas
    $out = Join-Path $OutRoot $tag

    $done++
    Write-Host ("`n===== [{0}/{1}] field-cooled I-V: B={2} Oe -> {3} =====" -f $done, $total, $B, $out) -ForegroundColor Cyan

    $cmd = @(
        "--temp", $TMeas,
        "--i-max", $IMax, "--i-step", $IStep,
        "--settle", $Settle, "--compliance", $Compliance,
        "--voltmeter-nplc", $Nplc, "--probe-mode", $ProbeMode,
        "--sample-visa", $SampleVisa,
        "--out", $out, "--tag", $tag
    )
    # Pulsed current (optional).
    if ($Pulsed) {
        $cmd += @("--pulsed", "--pulse-width", $PulseWidth, "--dwell", $Dwell)
    }
    # Auto-rescan around Ic (optional).
    if ($AutoRescan) {
        $cmd += @("--auto-rescan", "--v-jump", $VJump, "--rescan-window", $RescanWindow)
        if ($IStepFine -gt 0.0) { $cmd += @("--i-step-fine", $IStepFine) }
    }
    # Engage the field-cool front-end only for nonzero field; B=0 is a plain
    # zero-field-cooled I-V (no magnet needed, but still cool to T_meas).
    if ([math]::Abs($B) -gt 0.0) {
        $cmd += @("--psu", $PSU, "--field", $B, "--t-high", $THigh, "--coil-oe-per-a", $Coil)
    }

    moi-iv @cmd

    if ($LASTEXITCODE -ne 0) {
        Write-Warning ("field-cooled I-V at B={0} Oe failed (exit {1}); stopping scan." -f $B, $LASTEXITCODE)
        $aborted = $true
        break
    }
}

if ($aborted) {
    Write-Host "`n===== scan aborted =====" -ForegroundColor Red
    exit 1
}

Write-Host "`n===== all $total field-cooled I-V runs complete =====" -ForegroundColor Green
