"""Fetches the CC0 ambient-audio loops listed in manifest.json from
freesound.org's public preview CDN (no login required - see manifest.json's
_comment for why the PREVIEW rendition is fetched rather than each sound's
login-gated original file) into raw/ next to this script.

Same content_src/<category>/ convention as content_src/textures/ground/
download_ground_textures.py (download script + manifest.json ARE committed,
fetched audio is NOT - see repo .gitignore).

Usage:  python download_ambient_audio.py [--force]
    --force   re-download even if the target file already exists (default:
              skip a sound whose file is already present)
"""

import json
import sys
from pathlib import Path
from urllib.request import urlopen, Request

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"
ROOT = HERE.parent.parent.parent   # content_src/audio/ambient -> repo root


def download_one(entry, force):
    out_path = ROOT / entry["local_file"]
    if out_path.exists() and not force:
        print(f"  {entry['role']}: already present at {out_path}, skipping (--force to redo)")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    url = entry["preview_url"]
    print(f"  {entry['role']}: downloading {url}")
    req = Request(url, headers={"User-Agent": "OpenDrive-pipeline/1.0"})
    with urlopen(req, timeout=60) as resp:
        data = resp.read()
    out_path.write_bytes(data)
    print(f"    -> {len(data)} bytes -> {out_path}")


def main():
    force = "--force" in sys.argv
    manifest = json.loads(MANIFEST.read_text())
    print(f"Fetching {len(manifest['sounds'])} ambient loop(s) from freesound.org "
          f"(CC0 previews, see manifest.json for per-asset source URLs/authors)")
    for entry in manifest["sounds"]:
        download_one(entry, force)
    print("Done.")


if __name__ == "__main__":
    main()
