"""Generates the runtime credits screen text (Arc 4.4 - Release settings
shell, next steps.md SS4.4/SS7.31).

Content is GENERATED, not hand-typed (per the task brief): this script walks
every content_src/**/manifest.json license record in the repo, plus a small
hand-maintained header block (title/author/engine credit) and the two
data-source attribution lines the pipeline's own fetch scripts document
(pipeline/fetch_data.py, pipeline/terrain_common.py, README.md's own
"Data & licensing" section), and writes one plain-UTF-8 text sidecar that
AODCreditsMenu loads at runtime (ODCreditsMenu.cpp's LoadCreditsText(),
FFileHelper::LoadFileToStringArray against Content/OpenDrive/Masks/
credits.txt - same Content/OpenDrive/Masks convention every other sidecar in
this project uses (centerline.bin, labels_*.bin, ambient_zones_*.bin), just
plain text instead of a packed binary format since there's no runtime need
to compute anything from it - see this task's own design guidance for why
plain text was chosen over inventing a new binary format for a screen that's
read top to bottom once).

Two manifest schemas exist in this repo and both are handled generically:
  1. content_src/textures/{building,ground,road}/manifest.json,
     content_src/audio/ambient/manifest.json: {"...", "<array_key>": [
       {"display_name"/"asset_id"/"role", "author"?, "source_url"?,
        "license", ...}, ...]}
  2. content_src/textures/flora/manifest.json: a flat dict keyed by
     filename, each value {"source"/"license"/"author"?}.
content_src/textures/water/manifest.json fits schema 1 with a single
procedurally-generated (no third-party) entry.

data/cars/*/LICENSE.txt (three placeholder/procedural car-mesh generators)
are walked too, per the task's "enumerate ALL license manifests in the
repo" instruction - each is a one-line "original work, no third-party
assets" statement, folded into a "Vehicles" section.

Deliberately NOT touched: data/cars/procedural/obj/cars_manifest.json -
out of scope by this session's own standing rule (never touch vehicle
physics or cars_manifest.json), and it carries no license/attribution data
anyway (it's the game's own per-car tuning table, not an asset-sourcing
record - the sibling LICENSE.txt files already cover the "where did the car
meshes come from" question with an "original, no third-party assets"
answer).

Usage:  python build_credits.py
Writes: OpenDriveUE/Content/OpenDrive/Masks/credits.txt
"""

import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
CONTENT_SRC = ROOT / "content_src"
CARS_DIR = ROOT / "data" / "cars"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
OUT_PATH = OUT_DIR / "credits.txt"

# Excluded from the manifest.json walk on purpose - see module docstring.
EXCLUDE_MANIFESTS = {"cars_manifest.json"}

GAME_TITLE = "OpenDrive"
AUTHOR = "Taylor Markham"


def site_from_url(url):
    """Best-effort human site name from a source/download URL, for entries
    that don't carry their own 'author' field (the ambientCG texture sets -
    the whole site is CC0/no-attribution-required per its own terms, see
    content_src/textures/*/manifest.json's _comment, but naming the site
    anyway reads better than a bare license string)."""
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    if "ambientcg" in host:
        return "ambientCG"
    if "polyhaven" in host:
        return "Poly Haven"
    if "freesound" in host:
        return None   # freesound entries always carry their own author field
    return host or None


def walk_manifest(path):
    """Yields (name, author, source, license) tuples from one manifest.json,
    handling both schemas documented in the module docstring."""
    data = json.loads(path.read_text(encoding="utf-8"))

    # schema 1: a list value under some key ("textures", "sounds", ...)
    array_key = None
    for k, v in data.items():
        if isinstance(v, list):
            array_key = k
            break

    if array_key is not None:
        for entry in data[array_key]:
            name = entry.get("display_name") or entry.get("asset_id") or entry.get("role") or "(unnamed)"
            author = entry.get("author") or site_from_url(
                entry.get("source_url") or entry.get("download_url")) or entry.get("generator") or "—"
            source = entry.get("source_url") or entry.get("download_url") or entry.get("generator") or "—"
            license_ = entry.get("license", "unknown license")
            yield name, author, source, license_
        return

    # schema 2: flat dict keyed by filename (content_src/textures/flora/manifest.json)
    if all(isinstance(v, dict) for v in data.values()):
        for filename, entry in data.items():
            author = entry.get("author") or site_from_url(entry.get("source")) or "—"
            source = entry.get("source", "—")
            license_ = entry.get("license", "unknown license")
            yield filename, author, source, license_
        return

    raise ValueError(f"{path}: unrecognized manifest.json schema")


def build_lines():
    lines = []

    def add(s=""):
        lines.append(s)

    # ---- hand-maintained header block ----
    add(GAME_TITLE)
    add(f"by {AUTHOR}")
    add("Built with Unreal Engine 5")
    add("Unreal(R) Engine, (C) 1998-2026 Epic Games, Inc. Unreal is a trademark or")
    add("registered trademark of Epic Games, Inc. in the United States of America and")
    add("elsewhere.")
    add("Menu font: Rajdhani, (C) 2014 Indian Type Foundry. SIL Open Font License 1.1")
    add("(Content/Slate/Fonts/OFL.txt).")
    add()
    add("=" * 60)
    add("DATA SOURCES")
    add("=" * 60)
    add()
    # OSM ODbL attribution is legally required - kept verbatim and prominent.
    # OSM data drives more than road geometry in this project (water
    # polygons, waterways, buildings, settlement classification, and place
    # labels all come from the same OSM extract), so the line covers map
    # data generally rather than singling out roads.
    add("Map data (roads, water, buildings, places): (C) OpenStreetMap contributors.")
    add("Licensed under the Open Database License (ODbL) v1.0.")
    add("https://www.openstreetmap.org/copyright")
    add("https://opendatacommons.org/licenses/odbl/1-0/")
    add("Building heights: Microsoft Global ML Building Footprints, (C) Microsoft.")
    add("Licensed under the Open Database License (ODbL) v1.0.")
    add("https://github.com/microsoft/GlobalMLBuildingFootprints")
    add()
    # DEM/imagery source - as documented by pipeline/terrain_common.py and
    # README.md's "Data & licensing" section (public domain; no attribution
    # clause found in USGS/AWS Terrain Tiles' own terms, unlike OSM's ODbL -
    # named factually rather than asserting a license claim not confirmed in
    # this repo's own sources; see this task's final report for the caveat).
    add("Elevation data: USGS 3DEP, via AWS Terrain Tiles (Terrarium format).")
    add("Imagery: USGS.")
    add("U.S. Geological Survey data - public domain.")
    add("Ground colour (Realistic style): Contains modified Copernicus Sentinel data")
    add("2023-2025. Sentinel-2 L2A via Element84 Earth Search on AWS.")
    add("Traffic volumes: FHWA Highway Performance Monitoring System (HPMS) - public domain.")
    add()

    # ---- content_src/**/manifest.json walk ----
    add("=" * 60)
    add("ART & AUDIO ASSETS")
    add("=" * 60)
    manifest_paths = sorted(CONTENT_SRC.glob("**/manifest.json"))
    coverage = {}
    for m in manifest_paths:
        if m.name in EXCLUDE_MANIFESTS:
            continue
        rel = m.relative_to(ROOT).as_posix()
        add()
        add(f"-- {rel} --")
        count = 0
        for name, author, source, license_ in walk_manifest(m):
            add(f"  {name}")
            add(f"    Author/Source: {author}")
            add(f"    URL: {source}")
            add(f"    License: {license_}")
            count += 1
        coverage[rel] = count

    # ---- data/cars/*/LICENSE.txt ----
    add()
    add("=" * 60)
    add("VEHICLES")
    add("=" * 60)
    license_paths = sorted(CARS_DIR.glob("*/LICENSE.txt"))
    for lp in license_paths:
        rel = lp.relative_to(ROOT).as_posix()
        text = lp.read_text(encoding="utf-8").strip()
        add()
        add(f"-- {rel} --")
        for line in text.splitlines():
            add(f"  {line}")
        coverage[rel] = 1

    add()
    add("=" * 60)
    add(f"Thanks for playing {GAME_TITLE}.")
    add("=" * 60)

    return lines, coverage, manifest_paths, license_paths


def main():
    lines, coverage, manifest_paths, license_paths = build_lines()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    OUT_PATH.write_text(text, encoding="utf-8")

    total_manifests = len([m for m in manifest_paths if m.name not in EXCLUDE_MANIFESTS])
    total_entries = sum(coverage.values())
    print(f"build_credits.py: wrote {OUT_PATH} ({len(lines)} lines, {len(text.encode('utf-8'))} bytes)")
    print(f"  {total_manifests} manifest.json + {len(license_paths)} LICENSE.txt covered, "
          f"{total_entries} total entries")
    for rel, count in coverage.items():
        print(f"    {rel}: {count} entr{'y' if count == 1 else 'ies'}")


if __name__ == "__main__":
    main()
