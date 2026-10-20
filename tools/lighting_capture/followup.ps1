# od.Weather (console) ramps over RampSeconds=90 and wetness builds over
# WetUpSeconds/precip (~400 s at Rain 0.45), unlike the menu's instant pick.
# Shoot weather after the ramp is done so the shot shows the settled state.
$S = "$PSScriptRoot\capture.ps1"
& $S -Name gasquet_h12_overcast_settled -Hour 12 -Weather Overcast -ShotAt 150 -Extra '-odgo=8 -odseason=summer'
& $S -Name gasquet_h12_rain_settled     -Hour 12 -Weather Rain     -ShotAt 480 -Extra '-odgo=8 -odseason=summer'
& "$PSScriptRoot\diag.ps1"
