"""verify_asset_licenses.check_license (2026-10-09) passes the licenses
PLAN-VISUALS.md V5 allows and fails the ones it rules out.

Run:  python pipeline/tests/test_verify_asset_licenses.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_asset_licenses import check_license

for ok in ["CC0", "CC0 1.0 (ambientCG)", "Creative Commons 0 (CC0 1.0) - freesound.org",
           "CC-BY 4.0", "CC BY 4.0", "Public domain",
           "CC0 (procedural, no external asset)",
           "Original procedural content, no third-party assets."]:
    assert check_license(ok) is None, ok

for bad in ["CC-BY-NC 4.0", "CC BY-SA 4.0", "CC-BY-ND", "Non-commercial use only",
            "Fab Standard License", "Megascans", "Free for personal use",
            "Royalty-free", "CC0 but editorial use only", "", "unknown license"]:
    assert check_license(bad) is not None, bad

print("ok")
