# -*- coding: utf-8 -*-
"""Unit tests for src/merge_core.py. Pure Python, no Revit.

    python3 src/test_merge_core.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import merge_core as core


def wall(x1, y1, x2, y2, tag=None):
    """A straight wall record from (x1,y1) to (x2,y2)."""
    return {'points': [[(float(x1), float(y1), 0.0), (float(x2), float(y2), 0.0)]], 'tag': tag}


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


FAILURES = []


def check(condition, message):
    if not condition:
        FAILURES.append(message)


# --------------------------------------------------------------------------
def test_curve_distance():
    a = wall(0, 0, 10, 0)
    b = wall(0, 0, 10, 0)
    check(approx(core.curve_distance(a['points'], b['points'], 0.25), 0.0),
          'identical curves should be zero distance apart')

    moved = wall(0, 0.5, 10, 0.5)
    check(approx(core.curve_distance(a['points'], moved['points'], 0.25), 0.5),
          'a curve moved 0.5 should be 0.5 apart')

    # a wall that only partly overlaps must score badly in both directions
    shorter = wall(0, 0, 5, 0)
    value = core.curve_distance(a['points'], shorter['points'], 0.25)
    check(value > 4.0, 'a half length overlap should score badly, got %s' % value)

    # a perpendicular wall meeting in the middle
    crossing = wall(5, -5, 5, 5)
    value = core.curve_distance(a['points'], crossing['points'], 0.25)
    check(value > 4.0, 'a crossing wall should not match, got %s' % value)


# --------------------------------------------------------------------------
def test_match_walls_unchanged():
    main = [wall(0, 0, 10, 0), wall(10, 0, 10, 10)]
    redraw = [wall(0, 0, 10, 0), wall(10, 0, 10, 10)]
    result = core.match_walls(main, redraw, 0.03, 0.25)
    check(len(result['matched']) == 2, 'both walls should match, got %s' % result['matched'])
    check(result['only_left'] == [] and result['only_right'] == [],
          'nothing should be left over')


def test_match_walls_moved():
    main = [wall(0, 0, 10, 0), wall(10, 0, 10, 10)]
    redraw = [wall(0, 0, 10, 0), wall(10, 0, 12, 10)]
    result = core.match_walls(main, redraw, 0.03, 0.25)
    check(len(result['matched']) == 1, 'only the unchanged wall should match')
    check(result['only_left'] == [1], 'the moved Main wall should be unmatched')
    check(result['only_right'] == [1], 'the new Redraw wall should be unmatched')


def test_match_walls_new_and_removed():
    main = [wall(0, 0, 10, 0)]
    redraw = [wall(0, 0, 10, 0), wall(20, 0, 30, 0)]
    result = core.match_walls(main, redraw, 0.03, 0.25)
    check(len(result['matched']) == 1, 'the shared wall should match')
    check(result['only_right'] == [1], 'the new Redraw wall should be unmatched')


def test_match_walls_is_one_to_one():
    main = [wall(0, 0, 10, 0), wall(0, 0.001, 10, 0.001)]
    redraw = [wall(0, 0, 10, 0)]
    result = core.match_walls(main, redraw, 0.03, 0.25)
    check(len(result['matched']) == 1, 'two Main walls must not both match one Redraw wall')
    check(result['only_left'] == [1], 'the second Main wall should be unmatched')


def test_match_walls_long_move_is_not_matched():
    # a wall that moved further than the tolerance must not match, even though
    # the grid still puts it in a neighbouring cell
    main = [wall(0, 0, 10, 0)]
    redraw = [wall(0, 3, 10, 3)]
    result = core.match_walls(main, redraw, 0.03, 0.25)
    check(result['matched'] == [], 'a wall 3 feet away must not match')


def test_match_walls_many():
    main = [wall(i * 12, 0, i * 12 + 10, 0) for i in range(40)]
    redraw = list(main)
    redraw[17] = wall(17 * 12 + 2, 0, 17 * 12 + 12, 0)
    result = core.match_walls(main, redraw, 0.03, 0.25)
    check(len(result['matched']) == 39, '39 of 40 walls should match, got %d'
          % len(result['matched']))
    check(result['only_left'] == [17] and result['only_right'] == [17],
          'the moved wall should be the only one on each side')


# --------------------------------------------------------------------------
def test_find_components():
    #   A | B      C
    #   ---+---   ---
    #   D | E      (F not placed)
    adjacency = {
        'A': set(['B', 'D']),
        'B': set(['A', 'E']),
        'D': set(['A', 'E']),
        'E': set(['B', 'D']),
        'C': set(),
    }
    components = core.find_components(['A', 'B', 'C', 'D', 'E'], adjacency)
    check(len(components) == 2, 'expected two components, got %s' % components)
    check(components[0] == ['A', 'B', 'D', 'E'],
          'the cluster should be A,B,D,E - got %s' % components[0])
    check(components[1] == ['C'], 'C should be alone, got %s' % components[1])


def test_adjacency_from_wall_usage():
    usage = {
        10: ['A', 'B'],
        11: ['B', 'C'],
        12: ['D'],
    }
    adjacency = core.adjacency_from_wall_usage(usage)
    check(adjacency['A'] == set(['B']), 'A should touch only B, got %s' % adjacency['A'])
    check(adjacency['B'] == set(['A', 'C']), 'B should touch A and C, got %s' % adjacency['B'])
    check(adjacency['D'] == set(), 'D touches nothing, got %s' % adjacency['D'])


def test_adjacency_chain_is_one_cluster():
    # a row of six stands sharing walls is a single cluster
    usage = {}
    for index in range(5):
        usage[100 + index] = [str(index), str(index + 1)]
    adjacency = core.adjacency_from_wall_usage(usage)
    components = core.find_components([str(i) for i in range(6)], adjacency)
    check(len(components) == 1, 'a row of stands is one cluster, got %s' % components)


# --------------------------------------------------------------------------
def test_find_matches_allows_a_shared_wall_to_match_twice():
    # a shared wall is listed by both neighbours, so the reference set holds it
    # twice; both Main walls must still find it
    shared = wall(0, 0, 10, 0)
    reference = [shared, shared, wall(30, 0, 40, 0)]
    buckets = core.build_buckets(reference, core.cell_size(0.03))
    hits = core.find_matches(shared, reference, buckets, 0.03, 0.25, core.cell_size(0.03))
    check(sorted(hits) == [0, 1],
          'the shared wall should match both entries, got %s' % hits)


def test_find_matches_respects_the_tolerance():
    reference = [wall(0, 0, 10, 0)]
    buckets = core.build_buckets(reference, core.cell_size(0.03))
    cell = core.cell_size(0.03)
    near = wall(0, 0.01, 10, 0.01)
    far = wall(0, 1.0, 10, 1.0)
    check(core.find_matches(near, reference, buckets, 0.03, 0.25, cell) == [0],
          'a wall inside the tolerance should match')
    check(core.find_matches(far, reference, buckets, 0.03, 0.25, cell) == [],
          'a wall outside the tolerance should not match')


def test_match_walls_uses_the_same_buckets():
    main = [wall(i * 12, 0, i * 12 + 10, 0) for i in range(20)]
    redraw = [wall(i * 12, 0, i * 12 + 10, 0) for i in range(20)]
    result = core.match_walls(main, redraw, 0.03, 0.25)
    check(len(result['matched']) == 20,
          'all 20 walls should match, got %d' % len(result['matched']))


# --------------------------------------------------------------------------
def test_wall_distance_straight_walls():
    a = wall(0, 0, 10, 0)
    b = wall(0, 0, 10, 0)
    check(approx(core.wall_distance(a, b, 0.03, 0.25), 0.0), 'identical walls are 0 apart')
    nudged = wall(0, 0.01, 10, 0.01)
    check(approx(core.wall_distance(a, nudged, 0.03, 0.25), 0.01),
          'a wall nudged 0.01 should be 0.01 apart')
    # reversed direction is still the same wall
    reversed_wall = wall(10, 0, 0, 0)
    check(approx(core.wall_distance(a, reversed_wall, 0.03, 0.25), 0.0),
          'a reversed wall is still the same wall')
    apart = wall(0, 2, 10, 2)
    check(core.wall_distance(a, apart, 0.03, 0.25) > 1.9, 'a wall 2 apart should score 2')


def test_wall_distance_curved_walls():
    arc = {'points': [[(0, 0, 0), (1, 1, 0), (2, 0, 0)]]}
    same = {'points': [[(0, 0, 0), (1, 1, 0), (2, 0, 0)]]}
    other = {'points': [[(0, 0, 0), (1, -1, 0), (2, 0, 0)]]}
    check(approx(core.wall_distance(arc, same, 0.03, 0.25), 0.0), 'identical arcs match')
    check(core.wall_distance(arc, other, 0.03, 0.25) > 1.0,
          'arcs bulging opposite ways must not match')


def test_straight_ends_only_for_straight_walls():
    check(core.straight_ends(wall(0, 0, 10, 0)) is not None, 'a two point wall is straight')
    curved = {'points': [[(0, 0, 0), (1, 1, 0), (2, 0, 0)]]}
    check(core.straight_ends(curved) is None, 'a sampled arc is not straight')
    two_polylines = {'points': [[(0, 0, 0), (1, 0, 0)], [(0, 5, 0), (1, 5, 0)]]}
    check(core.straight_ends(two_polylines) is None, 'two polylines are not straight')


# --------------------------------------------------------------------------
def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_') and callable(value)]
    for test in tests:
        try:
            test()
        except Exception as ex:
            FAILURES.append('%s raised %s' % (test.__name__, ex))
    if FAILURES:
        print('%d failure(s):' % len(FAILURES))
        for failure in FAILURES:
            print('  ' + failure)
        return 1
    print('%d tests passed' % len(tests))
    return 0


if __name__ == '__main__':
    sys.exit(main())
