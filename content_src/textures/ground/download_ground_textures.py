"""Fetches the CC0 ground PBR texture sets listed in manifest.json from
ambientCG (https://ambientcg.com, no login, whole-site CC0 1.0 license - see
https://ambientcg.com/about) and extracts each asset's Color/NormalGL/
Roughness/AmbientOcclusion PNGs into its own subfolder next to this script.

Sibling of content_src/textures/road/download_road_textures.py (same
reasoning, same pattern) - Phase 3a ground-visual overhaul, see
unreal/apply_ground_material.py. The six 2K sets extract to ~350-450 MB
total, a bad fit for this git repo (see repo .gitignore -
content_src/textures/ is ignored). This script + manifest.json ARE
committed, so re-fetching the exact same bytes is a one-command,
deterministic step for anyone who needs the actual texture files rather
than just their provenance.

Usage:  python download_ground_textures.py [--force]
    --force   re-download+re-extract even if the asset's folder already
              has files (default: skip an asset whose folder is non-empty)
"""

import io
import json
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen, Request

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"


def download_one(entry, force):
    out_dir = HERE / Path(entry["local_dir"]).name
    if out_dir.exists() and any(out_dir.iterdir()) and not force:
        print(f"  {entry['asset_id']}: already present at {out_dir}, skipping (--force to redo)")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    url = entry["download_url"]
    print(f"  {entry['asset_id']}: downloading {url}")
    req = Request(url, headers={"User-Agent": "OpenDrive-pipeline/1.0"})
    with urlopen(req, timeout=120) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        # Only extract the texture maps this project actually uses - skip
        # the .blend/.mtlx/.tres/.usdc/preview-png ambientCG also bundles.
        wanted_suffixes = (
            "_Color.png", "_NormalGL.png", "_Roughness.png", "_AmbientOcclusion.png",
        )
        extracted = 0
        for name in zf.namelist():
            if name.endswith(wanted_suffixes):
                target = out_dir / Path(name).name
                target.write_bytes(zf.read(name))
                extracted += 1
    print(f"    -> {extracted} map(s) -> {out_dir}")


def main():
    force = "--force" in sys.argv
    manifest = json.loads(MANIFEST.read_text())
    print(f"Fetching {len(manifest['textures'])} texture set(s) from ambientCG "
          f"(CC0, see manifest.json for per-asset source URLs)")
    for entry in manifest["textures"]:
        download_one(entry, force)
    print("Done.")


if __name__ == "__main__":
    main()
