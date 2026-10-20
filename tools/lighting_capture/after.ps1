# Re-shoot of the audit set after the lighting fixes (main @ 8b7ecb6+), same
# spots, hours and settings as batch.ps1 / followup.ps1, names suffixed _fix.
$S = "$PSScriptRoot\capture.ps1"
$X = '-odgo=8 -odseason=summer'
$runs = @(
    @('gasquet_h12_clear',   '/Game/OpenWorldTest',                    12,   'Clear',    70),
    @('gasquet_h08_clear',   '/Game/OpenWorldTest',                    8,    'Clear',    70),
    @('gasquet_h1730_clear', '/Game/OpenWorldTest',                    17.5, 'Clear',    70),
    @('gasquet_h12_overcast_settled', '/Game/OpenWorldTest',           12,   'Overcast', 150),
    @('gasquet_h12_rain_settled',     '/Game/OpenWorldTest',           12,   'Rain',     480),
    @('dealsgap_h12_clear',  '/Game/Areas/OpenWorld_deals_gap',        12,   'Clear',    70),
    @('dealsgap_h1730_clear','/Game/Areas/OpenWorld_deals_gap',        17.5, 'Clear',    70),
    @('sf_h12_clear',        '/Game/Areas/OpenWorld_sf_russian_hill',  12,   'Clear',    70),
    @('sf_h1730_clear',      '/Game/Areas/OpenWorld_sf_russian_hill',  17.5, 'Clear',    70),
    @('summit_h12_clear',    '/Game/Areas/OpenWorld_summit_county',    12,   'Clear',    70)
)
foreach ($r in $runs) {
    if ($args.Count -and $args -notcontains $r[0]) { continue }   # optional: only the named shots
    & $S -Name "$($r[0])_fix" -Map $r[1] -Hour $r[2] -Weather $r[3] -ShotAt $r[4] -Extra $X
}
