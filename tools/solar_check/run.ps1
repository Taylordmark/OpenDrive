# Builds and runs solar_check.cpp with the VS toolchain (exit code = failures).
$vs = & "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" -latest -property installationPath
$out = Join-Path $env:TEMP "solar_check"
New-Item -ItemType Directory -Force $out | Out-Null
cmd /c "`"$vs\VC\Auxiliary\Build\vcvars64.bat`" >nul && cl /nologo /EHsc /W4 /Fo$out\ /Fe$out\solar_check.exe `"$PSScriptRoot\solar_check.cpp`" >nul && $out\solar_check.exe"
exit $LASTEXITCODE
