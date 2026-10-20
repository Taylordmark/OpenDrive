# Diagnostic shots (NOT normal-launch looks): same Gasquet noon spot as batch.ps1's
# gasquet_h12_clear, each with one console override, to split "no light reaches
# the forest shade" from "tonemap crushes it".
$S = "$PSScriptRoot\capture.ps1"
$diag = @(
    @('diag_lightingonly', 'viewmode lightingonly'),             # lighting with white albedo
    @('diag_nolumengi',    'r.Lumen.DiffuseIndirect.Allow 0'),   # what Lumen GI adds
    @('diag_localexp',     'r.LocalExposure.ShadowContrastScale 0.4')  # shadow lift via local exposure
)
if ($args -notcontains 'round2' -and $args -notcontains 'round3') { foreach ($d in $diag) {
    & $S -Name $d[0] -Hour 12 -Weather Clear -ShotAt 70 -Extra '-odgo=8 -odseason=summer' -ExecExtra $d[1]
} }
# round 2 (after lightingonly/nolumengi showed Lumen adds ~nothing under the canopy)
$diag2 = @(
    @('diag_hitlighting', 'r.Lumen.HardwareRayTracing.LightingMode 2'),  # hit lighting: is the foliage surface cache black?
    @('diag_noshortao',   'r.Lumen.ScreenProbeGather.ShortRangeAO 0'),   # is short-range AO crushing the shade?
    @('diag_noscreentraces', 'r.Lumen.ScreenProbeGather.ScreenTraces 0')  # are screen traces re-sampling the black canopy?
)
if ($args -contains 'round2') { foreach ($d in $diag2) {
    & $S -Name $d[0] -Hour 12 -Weather Clear -ShotAt 70 -Extra '-odgo=8 -odseason=summer' -ExecExtra $d[1]
} }
# round 3 (Lumen ruled out): is any fill light there at all, and is it the sky light?
$diag3 = @(
    @('diag_indirectonly', 'ShowFlag.DirectLighting 0'),  # sun off: exposure lifts whatever fill exists
    @('diag_noskylight',   'ShowFlag.SkyLighting 0')      # sky light off: how much of the fill it is
)
if ($args -contains 'round3') { foreach ($d in $diag3) {
    & $S -Name $d[0] -Hour 12 -Weather Clear -ShotAt 70 -Extra '-odgo=8 -odseason=summer' -ExecExtra $d[1]
} }
