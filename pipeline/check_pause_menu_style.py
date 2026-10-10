"""Pause menu must look like the main menu (2026-10-09): drawn with the shared
menu painter (Rajdhani via FMenuPaint), before and instead of the in-game HUD.
Fails if the pause block in ODRaceHUD.cpp uses the engine font, or if it no
longer returns early (the HUD would draw on top of / under it again).

Usage:  python pipeline/check_pause_menu_style.py
"""
import pathlib
import sys

src = (pathlib.Path(__file__).resolve().parents[1]
       / "OpenDriveUE/Source/OpenDriveUE/ODRaceHUD.cpp").read_text(encoding="utf-8")
start = src.index("for (TActorIterator<AODPauseMenu>")
end = src.index("\n\t}\n", start)
block = src[start:end]
bad = [w for w in ("CrispFont", "GetLargeFont", "DrawPlaceLabel", "FCanvasTextItem") if w in block]
ok = not bad and "return;" in block and src.index("// ---- race timer") > end
print("pause block:", "OK" if ok else f"FAIL {bad or 'not drawn before the HUD / no early return'}")
sys.exit(0 if ok else 1)
