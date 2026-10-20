# The lighting-research capture set: one capture.ps1 run per row, serially.
# Season pinned to summer so shots compare with summer reference photos
# (the saved garage season otherwise applies). Settings stay a normal launch.
$S = "$PSScriptRoot\capture.ps1"
$runs = @(
    @('gasquet_h12_clear',   '/Game/OpenWorldTest',               12,   'Clear'),
    @('gasquet_h08_clear',   '/Game/OpenWorldTest',               8,    'Clear'),
    @('gasquet_h1730_clear', '/Game/OpenWorldTest',               17.5, 'Clear'),
    @('gasquet_h12_overcast','/Game/OpenWorldTest',               12,   'Overcast'),
    @('gasquet_h12_rain',    '/Game/OpenWorldTest',               12,   'Rain'),
    @('dealsgap_h12_clear',  '/Game/Areas/OpenWorld_deals_gap',    12,   'Clear'),
    @('dealsgap_h1730_clear','/Game/Areas/OpenWorld_deals_gap',    17.5, 'Clear'),
    @('sf_h12_clear',        '/Game/Areas/OpenWorld_sf_russian_hill', 12, 'Clear'),
    @('sf_h1730_clear',      '/Game/Areas/OpenWorld_sf_russian_hill', 17.5, 'Clear'),
    @('summit_h12_clear',    '/Game/Areas/OpenWorld_summit_county', 12,  'Clear')
)
foreach ($r in $runs) {
    & $S -Name $r[0] -Map $r[1] -Hour $r[2] -Weather $r[3] -ShotAt 70 -Extra '-odgo=8 -odseason=summer'
}
# garage / showroom: the boot menu itself (no -odgo, so no loading gate to validate)
& $S -Name garage_h12 -Hour 12 -Weather Clear -ShotAt 90 -Extra '-odseason=summer'
