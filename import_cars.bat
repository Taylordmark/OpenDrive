@echo off
rem One-click car re-import: pulls data\cars\procedural\obj\SM_Car_*.obj into
rem the game (see CAR-MODDING.md). Safe to re-run any time.
"C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" "%~dp0OpenDriveUE\OpenDriveUE.uproject" -run=pythonscript -script="%~dp0unreal\import_car_meshes.py" -nullrhi -unattended -nosplash -nop4 -stdout
if errorlevel 1 (
    echo.
    echo Import FAILED - scroll up for the error. Common cause: the game or
    echo editor is still running ^(close it and re-run^).
) else (
    echo.
    echo Cars imported. Launch the game to see your edits.
)
pause
