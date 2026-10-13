"""The garage setups' wheel geometry, read from the PRESETS table in
unreal/apply_tuning_presets.py (the one source; it imports unreal, so it is
parsed, not imported). Fields left out of a preset take the ODTuningPreset
C++ default (Street)."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "unreal" / "apply_tuning_presets.py"
DEFAULTS = dict(tire_diameter_scale=1.0, ride_height_delta=0.0, front_tire_width_scale=1.0,
                rear_tire_width_scale=1.0, camber_deg=0.0, steer_lock_scale=1.0)


def setups():
    """{name: {field: float}} in garage order."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    table = next(n.value for n in tree.body
                 if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "PRESETS")
    out = {}
    for key, call in zip(table.keys, table.values):
        kw = {a.arg: ast.literal_eval(a.value) for a in call.keywords if a.arg in DEFAULTS}
        out[key.value] = {f: float(kw.get(f, d)) for f, d in DEFAULTS.items()}
    return out


if __name__ == "__main__":
    s = setups()
    assert list(s)[0] == "Street" and s["Street"] == DEFAULTS, s["Street"]
    assert s["Offroad"]["tire_diameter_scale"] == 1.12 and s["Track"]["ride_height_delta"] == -3.0
    for k, v in s.items():
        print(k, v)
