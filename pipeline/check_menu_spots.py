"""Print each baked area's menu vista pick and why (2026-10-16), without
writing menu_spots.json: build_menu_spots --dry per area, its PICK / WHY /
SKIP lines only.

Run:  python pipeline/check_menu_spots.py [area ...]   (default: every baked area)"""
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    script = Path(__file__).with_name("build_menu_spots.py")
    out = subprocess.run([sys.executable, str(script), "--dry", *(sys.argv[1:] or ["--all"])],
                         capture_output=True, text=True, encoding="utf-8")
    for line in out.stdout.splitlines():
        if line.lstrip().startswith(("PICK", "WHY", "SKIP")):
            print(line)
    if out.returncode:
        print(out.stderr, file=sys.stderr)
        sys.exit(out.returncode)
