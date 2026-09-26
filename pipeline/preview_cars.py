"""Zero-dependency live previewer for pipeline/make_car_meshes.py.

Serves pipeline/viewer/viewer.html plus the generated OBJ/texture assets
under data/cars/procedural/, and watches make_car_meshes.py's mtime in a
background thread - on change it reruns the generator as a subprocess and
records stdout/stderr/exit code so a syntax error shows up in the browser
(via /status) instead of failing silently. Stdlib only.

Also serves the other world assets read-only (no rebuild/hot-reload): flora
+ rock OBJs/textures under /files/, and /biomes - BiomeRecipeTable parsed
from ODForestManager.cpp - for the viewer's Flora/Rocks/Biomes tabs.

Usage:
    python pipeline/preview_cars.py [--port 8123]
"""

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "pipeline"
GENERATOR = PIPELINE / "make_car_meshes.py"
VIEWER_HTML = PIPELINE / "viewer" / "viewer.html"
OBJ_DIR = ROOT / "data" / "cars" / "procedural" / "obj"
TEX_DIR = ROOT / "data" / "cars" / "procedural" / "textures"
PALETTE_PNG = TEX_DIR / "T_CarPalette2.png"
MANIFEST_JSON = OBJ_DIR / "cars_manifest.json"
STAGE_DIR = ROOT / "data" / "stages" / "tail_of_the_dragon"
FOREST_CPP = ROOT / "OpenDriveUE" / "Source" / "OpenDriveUE" / "ODForestManager.cpp"
# /files/<key>/<basename> - read-only static dirs for the non-car assets.
FILE_DIRS = {
    "flora": STAGE_DIR / "obj",                     # make_tree_meshes.py
    "rocks": ROOT / "data" / "flora" / "obj",       # make_rock_meshes.py
    "tex": STAGE_DIR / "textures",                  # bark/foliage atlas/detail
    "rocktex": ROOT / "content_src" / "textures" / "ground" / "Rock030",
}

# --------------------------------------------------------------- shared state
_lock = threading.Lock()
_state = {
    "last_build_time": None,   # ISO-ish string, wall clock of last attempt
    "exit_code": None,
    "output": "",
    "building": False,
    "obj_mtimes": {},          # name -> mtime float
}


def _obj_mtimes():
    mtimes = {}
    if OBJ_DIR.exists():
        for p in sorted(OBJ_DIR.glob("*.obj")):
            try:
                mtimes[p.name] = p.stat().st_mtime
            except OSError:
                pass
    return mtimes


def run_build():
    """Run the generator once, capturing output; update shared state."""
    with _lock:
        _state["building"] = True
    try:
        proc = subprocess.run(
            [sys.executable, str(GENERATOR)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        exit_code = proc.returncode
    except Exception as exc:  # subprocess launch failure, timeout, etc.
        output = f"preview_cars.py: failed to run generator: {exc!r}"
        exit_code = -1

    with _lock:
        _state["last_build_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _state["exit_code"] = exit_code
        _state["output"] = output
        _state["building"] = False
        _state["obj_mtimes"] = _obj_mtimes()

    status = "OK" if exit_code == 0 else f"FAILED ({exit_code})"
    print(f"[preview_cars] rebuild {status} at {_state['last_build_time']}")


def watcher_thread(poll_seconds=0.5):
    last_mtime = None
    try:
        last_mtime = GENERATOR.stat().st_mtime
    except OSError:
        pass
    while True:
        time.sleep(poll_seconds)
        try:
            mtime = GENERATOR.stat().st_mtime
        except OSError:
            continue
        if last_mtime is None or mtime != last_mtime:
            last_mtime = mtime
            run_build()


def asset_list():
    """Flora = stage OBJs that ship an .mtl (skips road_*/calib/legacy
    tree_green); rocks = every rock OBJ. Standard tier names only - the
    viewer's Detail toggle picks the _high/_ultra variants itself."""
    std = lambda p: not p.stem.endswith(("_high", "_ultra"))
    flora = sorted(p.stem for p in FILE_DIRS["flora"].glob("*.obj")
                   if p.with_suffix(".mtl").exists() and std(p))
    rocks = sorted(p.stem for p in FILE_DIRS["rocks"].glob("*.obj") if std(p))
    return {"flora": flora, "rocks": rocks}


def biome_recipes():
    """Parse BiomeRecipeTable straight out of ODForestManager.cpp so the
    viewer's biome scenes can't drift from what the game scatters. Each row:
    /* Name */ {{7 species}, {2 shrub}, tree, bush, rock, scrub[, floor]}."""
    text = FOREST_CPP.read_text(encoding="utf-8")
    table = text[text.index("BiomeRecipeTable[] = {"):]
    table = table[:table.index("};")]
    out = []
    for name, species, shrub, rest in re.findall(
            r"/\*\s*(\w+)\s*\*/\s*\{\{([^}]*)\},\s*\{([^}]*)\},([^}]*)\}", table):
        nums = lambda t: [float(x.strip().rstrip("f")) for x in t.split(",") if x.strip()]
        r = nums(rest) + [0.0]
        out.append({"name": name, "species": nums(species), "shrub": nums(shrub),
                    "tree": r[0], "bush": r[1], "rock": r[2], "scrub": r[3],
                    "floor": r[4]})
    return out


# ------------------------------------------------------------------- server
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".obj": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
    ".mtl": "text/plain; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "PreviewCars/1.0"

    def log_message(self, fmt, *args):
        # Keep console output focused on build status, not request spam.
        pass

    def _send_bytes(self, data, content_type, status=200, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_file(self, path: Path, content_type):
        try:
            data = path.read_bytes()
        except OSError:
            self._send_bytes(b"not found", "text/plain; charset=utf-8", 404)
            return
        self._send_file_type = content_type
        self._send_bytes(data, content_type)

    def _send_json(self, obj, status=200):
        data = json.dumps(obj).encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status)

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path == "/" or path == "/index.html":
            self._send_file(VIEWER_HTML, "text/html; charset=utf-8")
            return

        if path == "/status":
            with _lock:
                snapshot = dict(_state)
            snapshot["manifest_available"] = MANIFEST_JSON.exists()
            self._send_json(snapshot)
            return

        if path == "/manifest":
            if MANIFEST_JSON.exists():
                self._send_file(MANIFEST_JSON, "application/json; charset=utf-8")
            else:
                self._send_json({}, 404)
            return

        if path.startswith("/obj/"):
            name = path[len("/obj/"):]
            # Only allow flat basenames ending in .obj - no path traversal.
            if "/" in name or "\\" in name or not name.endswith(".obj"):
                self._send_bytes(b"bad request", "text/plain; charset=utf-8", 400)
                return
            self._send_file(OBJ_DIR / name, "text/plain; charset=utf-8")
            return

        if path == "/assets":
            self._send_json(asset_list())
            return

        if path == "/biomes":
            try:
                self._send_json(biome_recipes())
            except (OSError, ValueError) as exc:
                self._send_json({"error": repr(exc)}, 500)
            return

        if path.startswith("/files/"):
            key, _, name = path[len("/files/"):].partition("/")
            ext = Path(name).suffix.lower()
            if (key not in FILE_DIRS or not name or "/" in name or "\\" in name
                    or ".." in name or ext not in _CONTENT_TYPES):
                self._send_bytes(b"bad request", "text/plain; charset=utf-8", 400)
                return
            self._send_file(FILE_DIRS[key] / name, _CONTENT_TYPES[ext])
            return

        if path == "/textures/T_CarPalette2.png":
            self._send_file(PALETTE_PNG, "image/png")
            return

        self._send_bytes(b"not found", "text/plain; charset=utf-8", 404)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8123)
    args = parser.parse_args()

    if not VIEWER_HTML.exists():
        print(f"[preview_cars] WARNING: {VIEWER_HTML} does not exist yet")

    # Build once at startup so the OBJs are fresh before we ever serve them.
    run_build()

    t = threading.Thread(target=watcher_thread, daemon=True)
    t.start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[preview_cars] watching {GENERATOR}")
    print(f"[preview_cars] open http://localhost:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
