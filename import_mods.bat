@echo off
rem One-click mod import: pulls data\mods\{Cars,Trees,Rocks} over the stock
rem meshes of the same name (see ASSET-MODDING.md). Safe to re-run any time.
"C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" "%~dp0OpenDriveUE\OpenDriveUE.uproject" -run=pythonscript -script="%~dp0unreal\import_mods.py" -nullrhi -unattended -nosplash -nop4 -stdout
if errorlevel 1 (
    echo.
    echo Import FAILED - scroll up for the error. Common cause: the game or
    echo editor is still running ^(close it and re-run^).
) else (
    echo.
    echo Mods imported. Launch the game to see your edits.
)
pause
