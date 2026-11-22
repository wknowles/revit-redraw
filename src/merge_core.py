# -*- coding: utf-8 -*-
"""Redraw merge core: wall-level comparison and clustering.

Pure Python. No Revit types, no Dynamo, no imports beyond the standard
library - so the whole plan can be unit tested offline.

The merge compares at *wall* level, not at room level. A stand's geometry is
just the walls around it, and a wall shared by two stands is one wall, so
comparing walls is what lets a cluster of connected stands merge as one
change instead of several conflicting ones.

Vocabulary
----------
wall record  a dict with
               'points'  list of point lists (one per curve), each point a
                         (x, y, z) tuple in Revit internal feet
               'ref'     the Revit object behind it, opaque to this module
stand record a dict with
               'number'  the Room Number
               'walls'   the ids of the Main walls bounding it
"""

# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def resample(points, spacing):
    """Split a polyline into points roughly `spacing` apart."""
    if len(points) < 2:
        return list(points)
    out = []
    for index in range(len(points) - 1):
        start = points[index]
        end = points[index + 1]
        length = distance(start, end)
        steps = max(1, int((length / spacing) + 0.999999))
        for step in range(steps):
            factor = float(step) / steps
            out.append((start[0] + (end[0] - start[0]) * factor,
                        start[1] + (end[1] - start[1]) * factor,
                        start[2] + (end[2] - start[2]) * factor))
    out.append(points[-1])
    return out


def distance(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def point_to_polyline(point, polyline):
    """Shortest distance from a point to a polyline."""
    best = None
    for index in range(len(polyline) - 1):
        a = polyline[index]
        b = polyline[index + 1]
        abx = b[0] - a[0]
        aby = b[1] - a[1]
        abz = b[2] - a[2]
        squared = abx * abx + aby * aby + abz * abz
        if squared < 1e-18:
            candidate = distance(point, a)
        else:
            t = ((point[0] - a[0]) * abx + (point[1] - a[1]) * aby
                 + (point[2] - a[2]) * abz) / squared
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            closest = (a[0] + abx * t, a[1] + aby * t, a[2] + abz * t)
            candidate = distance(point, closest)
        if best is None or candidate < best:
            best = candidate
    return best if best is not None else 1e99


def point_to_curve(point, curve_points, spacing):
    """Shortest distance from a point to a curve, sampled at `spacing`."""
    best = None
    for polyline in curve_points:
        candidate = point_to_polyline(point, resample(polyline, spacing))
        if best is None or candidate < best:
            best = candidate
    return best if best is not None else 1e99


def curve_distance(a, b, spacing):
    """How far two boundaries are from each other.

    The maximum distance in both directions, so a curve that only partly
    overlaps another still scores badly.
    """
    if not a or not b:
        return 1e99
    pa = [point for polyline in a for point in resample(polyline, spacing)]
    pb = [point for polyline in b for point in resample(polyline, spacing)]
    if not pa or not pb:
        return 1e99
    worst = 0.0
    for point in pa:
        value = point_to_curve(point, b, spacing)
        if value > worst:
            worst = value
    for point in pb:
        value = point_to_curve(point, a, spacing)
        if value > worst:
            worst = value
    return worst


def straight_ends(wall):
    """The two ends of a wall, or None when the wall is not a straight run.

    Almost every wall in a floor plan is straight, and two straight walls are
    the same wall exactly when their ends agree. Checking the ends is O(1)
    instead of sampling and comparing every point.
    """
    polys = wall['points']
    if len(polys) != 1:
        return None
    poly = polys[0]
    if len(poly) != 2:
        return None
    return poly[0], poly[1]


def wall_distance(left, right, tol, spacing):
    """Distance between two walls, in feet.

    Straight walls are compared by their ends, which is exact and cheap.
    Anything else falls back to the sampled comparison.
    """
    ends_left = straight_ends(left)
    ends_right = straight_ends(right)
    if ends_left is not None and ends_right is not None:
        def gap(a, b):
            return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))
        direct = max(gap(ends_left[0], ends_right[0]), gap(ends_left[1], ends_right[1]))
        crossed = max(gap(ends_left[0], ends_right[1]), gap(ends_left[1], ends_right[0]))
        return min(direct, crossed)
    return curve_distance(left['points'], right['points'], spacing)


# --------------------------------------------------------------------------
# wall matching
# --------------------------------------------------------------------------


def midpoint(points):
    """Centre of a wall's bounding box, in plan."""
    xs = [p[0] for polyline in points for p in polyline]
    ys = [p[1] for polyline in points for p in polyline]
    if not xs:
        return (0.0, 0.0)
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def cell_of(point, cell):
    return (int(point[0] // cell), int(point[1] // cell))


def cell_size(tol):
    """Grid cell used to find candidate walls.

    A wall that has not moved has an identical midpoint and is always in the
    same cell. A wall that has moved further than the cell has left the
    neighbourhood, but it has also moved further than the tolerance, so it was
    never going to match.
    """
    return max(tol * 8.0, 1.0)


def build_buckets(walls, cell):
    """Group wall indices by the grid cell of their midpoint."""
    buckets = {}
    for index, wall in enumerate(walls):
        key = cell_of(midpoint(wall['points']), cell)
        buckets.setdefault(key, []).append(index)
    return buckets


def find_matches(wall, walls, buckets, tol, spacing, cell):
    """Indices of the walls that match `wall` within the tolerance.

    Unlike match_walls this is not one to one, so a wall that appears twice in
    the reference set - a shared wall, which every neighbour lists - still
    matches.
    """
    found = []
    key = cell_of(midpoint(wall['points']), cell)
    seen = set()
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for index in buckets.get((key[0] + dx, key[1] + dy), []):
                if index in seen:
                    continue
                seen.add(index)
                if wall_distance(wall, walls[index], tol, spacing) <= tol:
                    found.append(index)
    return found


def match_walls(left, right, tol, spacing):
    """Match two sets of walls by position.

    `left` and `right` are lists of wall records with a 'points' key. Returns
    a dict with:

      'matched'   list of (left_index, right_index, distance)
      'only_left' indices of left walls with no counterpart
      'only_right' indices of right walls with no counterpart

    Matching is one to one and greedy by increasing distance, so the closest
    pair wins.
    """
    cell = cell_size(tol)
    buckets = build_buckets(right, cell)

    candidates = []
    for left_index, wall in enumerate(left):
        for right_index in find_matches(wall, right, buckets, tol, spacing, cell):
            value = wall_distance(wall, right[right_index], tol, spacing)
            candidates.append((value, left_index, right_index))
    candidates.sort()

    used_left = set()
    used_right = set()
    matched = []
    for value, left_index, right_index in candidates:
        if left_index in used_left or right_index in used_right:
            continue
        used_left.add(left_index)
        used_right.add(right_index)
        matched.append((left_index, right_index, value))

    only_left = [i for i in range(len(left)) if i not in used_left]
    only_right = [i for i in range(len(right)) if i not in used_right]
    return {'matched': matched, 'only_left': only_left, 'only_right': only_right}


# --------------------------------------------------------------------------
# clustering
# --------------------------------------------------------------------------


def find_components(stand_ids, adjacency):
    """Group stands that share a wall.

    `adjacency` maps a stand id to the set of stand ids it touches. The result
    is a list of components, each a list of stand ids. Components are found
    with a breadth first walk, so no explicit graph library is needed.
    """
    remaining = set(stand_ids)
    components = []
    while remaining:
        seed = remaining.pop()
        group = [seed]
        queue = [seed]
        while queue:
            current = queue.pop()
            for neighbour in adjacency.get(current, ()):  # noqa: B007
                if neighbour in remaining:
                    remaining.discard(neighbour)
                    group.append(neighbour)
                    queue.append(neighbour)
        components.append(sorted(group))
    components.sort()
    return components


def adjacency_from_wall_usage(wall_to_stands):
    """Turn a wall -> stands map into a stand -> stands adjacency map."""
    adjacency = {}
    for stands in wall_to_stands.values():
        unique = sorted(set(stands))
        for stand in unique:
            neighbours = adjacency.setdefault(stand, set())
            for other in unique:
                if other != stand:
                    neighbours.add(other)
    return adjacency
