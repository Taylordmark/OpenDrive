"""Headless ground/terrain screenshot capture for the terrain realism research
(docs/terrain_realism_research_src.html). Uses only existing QA hooks:
-odpick=<town>, optional -odspawnx/-odspawny (cm, area frame), -odhour,
-odseason, -odshot=N, -ExecCmds="od.Weather Clear". Normal settings otherwise (traffic on,
scalability as a normal launch: the log shows "sp 100 sg 3 style 1"). One game launch per spot; the game is
killed once the screenshot lands. Run against the MAIN checkout (worktrees
have no Content):
    python pipeline/checks/capture_ground_spots.py <out_dir> summit_blueriver [more...] | all
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

UE = r"C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe"
PROJ = Path(r"C:\Users\taylo\Coding\OpenDrive\OpenDriveUE")
SHOTS = PROJ / "Saved" / "Screenshots" / "WindowsEditor"

# spot -> (area, -odpick town, extra args)
SPOTS = {
    "summit_blueriver": ("summit_county", "Blue River", []),
    "summit_winter": ("summit_county", "Blue River", ["-odseason=winter"]),
    "summit_dillon": ("summit_county", "Dillon", []),
    "green_valley": ("green_valley", "Agua Dulce", []),
    "oahu": ("oahu", "Kalaniana", []),
    "skagit": ("skagit_cascades", "Concrete", []),
}


def is_loading_screen(png):
    from PIL import Image   # loading screen = flat ink colour almost everywhere
    import numpy as np
    a = np.asarray(Image.open(png).convert("L").reduce(8), dtype=float)
    return a.std() < 12


def pngs():
    return set(SHOTS.glob("*.png")) if SHOTS.exists() else set()


def running(exe):
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"], capture_output=True, text=True)
    return exe in r.stdout


def other_ue_running():
    # zenserver (DDC) shuts down after its owning editor exits; a launch inside
    # that window hangs on "Waiting for ZenServer to be ready" forever
    return running("UnrealEditor.exe") or running("zenserver.exe")


def capture(name, out, shot_s=480, timeout_s=900):
    area, town, extra = SPOTS[name]
    while True:   # one game at a time (shared GPU, other sessions capture too)
        while other_ue_running():
            time.sleep(15)
        time.sleep(20)
        if not other_ue_running():
            break
    before = pngs()
    args = [UE, str(PROJ / "OpenDriveUE.uproject"), f"/Game/Areas/OpenWorld_{area}",
            "-game", "-windowed", "-resx=1920", "-resy=1080", "-log",
            f"-abslog={out / (name + '.log')}", f"-odpick={town}", "-odhour=13",
            f"-odshot={shot_s}", "-NoZenAutoLaunch",   # no Zen boot hang between launches
            "-ExecCmds=od.Weather Clear"]   # weather is a random climate roll; pin it so spots compare
    if not any(a.startswith("-odseason=") for a in extra):
        args.append("-odseason=summer")
    p = subprocess.Popen(args + extra)
    t0, got = time.time(), None
    while time.time() - t0 < timeout_s and p.poll() is None:
        time.sleep(2)
        new = pngs() - before
        logtxt = (out / (name + ".log")).read_text(errors="ignore") if (out / (name + ".log")).exists() else ""
        if new and "screenshot requested" in logtxt:   # ours, not another session's capture
            time.sleep(4)   # writer finishing / second hook
            got = max(new, key=lambda f: f.stat().st_mtime)
            break
    p.kill()
    if got and is_loading_screen(got):
        print(name, "-> loading screen, rejected", got, flush=True)
        got = None
    if got:
        shutil.copy(got, out / f"{name}.png")
    print(name, "->", got, flush=True)


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for n in (list(SPOTS) if sys.argv[2:] == ["all"] else sys.argv[2:]):
        capture(n, out)
