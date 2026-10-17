# Headless lighting/sky QA capture (research tool, no game changes).
# One game run per shot: boots a map, starts driving (-odgo / -odpick), forces
# hour + weather via the existing -odhour / od.Weather hooks, grabs one -odshot
# frame, then kills only its own process.
#   capture.ps1 -Name noon_gasquet -Map /Game/OpenWorldTest -Hour 12 -Weather Clear -Extra '-odgo=8'
# Weather: Clear|Overcast|Rain|HeavyRain|Fog|... ("-" = leave Auto).
# Runs the MAIN checkout's project (worktrees don't carry Content/).
param(
    [Parameter(Mandatory)] [string]$Name,
    [string]$Map = '/Game/OpenWorldTest',
    [double]$Hour = 12,
    [string]$Weather = 'Clear',
    [string]$Extra = '-odgo=8',
    [int]$ShotAt = 45,
    [string]$ExecExtra = '',
    [string]$Out = "$PSScriptRoot\out"
)
$UE = 'C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe'
$Root = 'C:\Users\taylo\Coding\OpenDrive\OpenDriveUE'
$Shots = "$Root\Saved\Screenshots\WindowsEditor"
New-Item -ItemType Directory -Force $Out, $Shots | Out-Null
$exec = 'r.ScreenPercentage 100'
if ($ExecExtra) { $exec += ",$ExecExtra" }   # diagnostic shots only (e.g. viewmode lightingonly)
if ($Weather -ne '-') { $exec = "od.Weather $Weather,$exec" }
# another session's game/editor run would share the GPU and skew both: wait it out
while (Get-Process UnrealEditor -ErrorAction SilentlyContinue | Where-Object { $_.Handles -gt 0 }) { Start-Sleep -Seconds 10 }
$before = @(Get-ChildItem $Shots -Filter *.png).Count
$argLine = "`"$Root\OpenDriveUE.uproject`" $Map -game -windowed -resx=1600 -resy=900 -log LOG=cap_$Name.log " +
    "-odhour=$Hour -odshot=$ShotAt -odperf=5 `"-ExecCmds=$exec`" -nosplash -unattended -NoZenAutoLaunch $Extra"
$p = Start-Process -FilePath $UE -ArgumentList $argLine -PassThru
$deadline = (Get-Date).AddSeconds($ShotAt + 600)   # -odshot counts from first world tick; boot is minutes
while ((Get-Date) -lt $deadline -and -not $p.HasExited) {
    Start-Sleep -Seconds 1
    if (@(Get-ChildItem $Shots -Filter *.png).Count -gt $before) { Start-Sleep -Seconds 6; break }
}
if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
Start-Sleep -Seconds 2
$new = Get-ChildItem $Shots -Filter *.png | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (@(Get-ChildItem $Shots -Filter *.png).Count -gt $before -and $new) { Move-Item $new.FullName "$Out\$Name.png" -Force }
Copy-Item "$Root\Saved\Logs\cap_$Name.log" $Out -ErrorAction SilentlyContinue
# valid only if the shot came after the loading gate released normally (not the
# failsafe, not mid-stream); record the settings the run actually used
$log = Get-Content "$Out\cap_$Name.log" -ErrorAction SilentlyContinue
$rel = ($log | Select-String 'LOADSCREEN (failsafe )?released' | Select-Object -First 1)
$shot = ($log | Select-String 'screenshot requested' | Select-Object -First 1)
$perf = ($log | Select-String 'ODPerf: .* sp \d+ sg \d+ style \d+' | Select-Object -Last 1)
$ok = $rel -and $shot -and $rel.LineNumber -lt $shot.LineNumber -and $rel.Line -notmatch 'failsafe'
$settings = if ($perf) { ($perf.Line -replace '.*\| (\d+x\d+ sp .*)$', '$1') } else { '?' }
"$Name -> " + $(if (-not (Test-Path "$Out\$Name.png")) { 'MISSING' } elseif ($ok) { 'ok' } else { 'INVALID (shot before loading gate / failsafe)' }) +
    " | gate: " + $(if ($rel) { $rel.Line -replace '.*LOADSCREEN ', '' } else { 'none' }) + " | settings: $settings"
