import numpy as np

from road_mesh_common import OVERPASS_RAMP_GRADE, OVERPASS_SEPARATION_M, lift_overpasses


def seg(e, n, h, struct=None, layer=0):
    s = np.r_[0, np.cumsum(np.hypot(np.diff(e), np.diff(n)))]
    k = len(e)
    st = np.zeros(k, bool) if struct is None else struct
    return {"e": np.asarray(e, float), "n": np.asarray(n, float), "s": s, "h": np.full(k, h, float),
            "is_struct_s": st, "is_tunnel_s": np.zeros(k, bool), "layer_s": [layer] * k}


def test_bridge_lifted_over_road_with_gentle_ramps():
    x = np.arange(-500, 501, 5.0)
    br = np.abs(x) <= 15
    upper = seg(x, np.zeros_like(x), 100.0, br, layer=1)
    lower = seg(np.zeros_like(x), x, 100.5)
    h, over, lifted, rows, skipped = lift_overpasses([upper, lower])
    assert skipped == 0 and len(rows) == 1
    assert abs(rows[0][5] - OVERPASS_SEPARATION_M) < 0.05      # exactly the separation
    assert not lifted[1].any()                                  # the road below stays put
    assert over[0].sum() == br.sum() and not over[1].any()
    assert np.abs(np.diff(h[0]) / 5.0).max() <= OVERPASS_RAMP_GRADE + 1e-6
    assert h[0][0] == 100.0 and h[0][-1] == 100.0               # ramps end back on the old profile


def test_ramp_continues_through_a_node_but_never_onto_the_road_below():
    x = np.arange(-20, 101, 5.0)
    upper = seg(x, np.zeros_like(x), 100.0, np.abs(x) <= 15, layer=1)
    upper["node_s"] = [("w", 0.0), ("a", upper["s"][-1])]
    xx = np.arange(100, 401, 5.0)
    beyond = seg(xx, np.zeros_like(xx), 100.0)                  # continues the upper road past node a
    beyond["node_s"] = [("a", 0.0), ("b", beyond["s"][-1])]
    y = np.arange(-300, 301, 5.0)
    lower = seg(np.zeros_like(y), y, 100.0)
    lower["node_s"] = [("a", 0.0), ("c", lower["s"][-1])]     # contrived: shares node a
    h, _, lifted, rows, _ = lift_overpasses([upper, beyond, lower])
    assert abs(rows[0][5] - OVERPASS_SEPARATION_M) < 0.05
    assert abs(h[0][-1] - h[1][0]) < 1e-9                       # no step at the node
    assert lifted[1][0] and not lifted[1][-1] and not lifted[2].any()


def test_non_bridge_upper_and_at_grade_are_left_alone():
    x = np.arange(-200, 201, 5.0)
    cut = seg(np.zeros_like(x), x, 95.0, layer=-1)              # underpass cut, upper road untagged
    road = seg(x, np.zeros_like(x), 100.0)
    h, _, lifted, rows, skipped = lift_overpasses([road, cut])
    assert skipped == 1 and not rows and not any(m.any() for m in lifted)
    h, _, lifted, rows, skipped = lift_overpasses([road, seg(np.zeros_like(x), x, 100.0)])
    assert skipped == 0 and not rows


def test_road_crossing_just_past_a_short_bridge_way_joins_the_deck():
    x = np.arange(-300, 301, 5.0)
    upper = seg(x, np.zeros_like(x), 100.0, np.abs(x) <= 5)            # OSM bridge way stops short
    upper["layer_s"] = list((np.abs(x) <= 5).astype(int))
    y = np.arange(-200, 201, 5.0)
    under = seg(np.zeros_like(y), y, 100.0)
    beside = seg(np.full_like(y, 30.0), y, 100.0)                       # crosses 30 m on, untagged
    h, over, lifted, rows, _ = lift_overpasses([upper, under, beside])
    k = int(np.argmin(np.abs(x - 30)))
    assert h[0][k] - 100.0 > OVERPASS_SEPARATION_M - 0.1 and over[0][k]
    assert not lifted[1].any() and not lifted[2].any()
