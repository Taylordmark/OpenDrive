"""Forest collider slots (2026-10-09, Taylor: "most of [the trees and rocks]
in summit county were not causing collisions").

AODForestManager::UpdateCollision used to re-deal its pooled capsules by
distance rank every frame and move them without teleport, so the capsule
for the tree in front of the car was usually flying in from another tree
(a kinematic target sweep). It now keeps a slot on its obstacle while that
obstacle stays in the nearest-N set. This mirrors that matching on a car
driving through a dense random forest and checks: every wanted obstacle has
a slot each frame, and no slot ever leaves an obstacle that is still wanted.

Usage:  python pipeline/check_collision_slots.py
"""
import random

POOL = 96
RADIUS = 6000.0


def update(pool_at, obstacles, car):
    near = sorted((o for o in obstacles if (o[0] - car[0]) ** 2 + (o[1] - car[1]) ** 2 < RADIUS ** 2),
                  key=lambda o: (o[0] - car[0]) ** 2 + (o[1] - car[1]) ** 2)[:POOL]
    kept = [False] * len(near)
    free = []
    for s, at in enumerate(pool_at):
        hit = near.index(at) if at in near else -1
        if hit >= 0 and not kept[hit]:
            kept[hit] = True
        else:
            free.append(s)
    f = 0
    for i, o in enumerate(near):
        if not kept[i]:
            pool_at[free[f]] = o
            f += 1
    for s in free[f:]:
        pool_at[s] = None
    return near


def main():
    rng = random.Random(7)
    obstacles = [(rng.uniform(-30000, 30000), rng.uniform(-30000, 30000)) for _ in range(40000)]
    pool_at = [None] * POOL
    car = [-20000.0, 0.0]
    moves, steals = 0, 0
    for _ in range(600):                       # 20 s at 30 fps, 30 m/s, across the forest
        car[0] += 100.0
        before = list(pool_at)
        near = update(pool_at, obstacles, car)
        assert set(near) == {a for a in pool_at if a is not None}, "every wanted obstacle holds a slot"
        for s in range(POOL):
            if before[s] != pool_at[s]:
                moves += 1
                steals += before[s] in near     # left an obstacle that is still wanted
    assert steals == 0, f"{steals} slots left an obstacle still in the nearest set"
    print(f"PASS: {moves} slot moves over 600 frames, none off a still-wanted obstacle")


if __name__ == "__main__":
    main()
