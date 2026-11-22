# -*- coding: utf-8 -*-
"""Offline test for graph 2's planning.

    python3 src/test_graph2_plan.py

Drives plan_clusters() with stand, wall and curve fakes, so the glue between
the Revit data and the merge core is exercised without Revit. The fakes are
deliberately thin: they only have to behave enough like the Revit API for the
geometry helpers.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'src'))

# graph2 imports the Revit API at module level, so stub it before importing.
REVIT_NAMES = [
    'Arc', 'Line', 'XYZ', 'Wall', 'LocationCurve', 'LocationPoint', 'BuiltInParameter',
    'BuiltInCategory', 'FilteredElementCollector', 'RevitLinkInstance', 'RevitLinkType',
    'RevitLinkOptions', 'FilePath', 'ImportPlacement', 'Transaction', 'ModelPathUtils',
    'SpatialElementBoundaryOptions', 'SpatialElementBoundaryLocation', 'UnitTypeId',
    'UnitUtils', 'StorageType', 'ElementId', 'Level', 'Color', 'CurveLoop', 'DirectShape',
    'GeometryCreationUtilities', 'OverrideGraphicSettings', 'UV',
]


def install_stubs():
    clr = types.ModuleType('clr')
    clr.AddReference = lambda *args, **kwargs: None
    revit = types.ModuleType('Autodesk.Revit.DB')
    for name in REVIT_NAMES:
        setattr(revit, name, type(name, (object,), {}))
    persistence = types.ModuleType('RevitServices.Persistence')

    class DocumentManager(object):
        class Instance(object):
            CurrentDBDocument = None

    persistence.DocumentManager = DocumentManager
    services = types.ModuleType('RevitServices')
    services.Persistence = persistence
    autodesk = types.ModuleType('Autodesk')
    autodesk.Revit = types.ModuleType('Autodesk.Revit')
    autodesk.Revit.DB = revit
    sys.modules['clr'] = clr
    sys.modules['Autodesk'] = autodesk
    sys.modules['Autodesk.Revit'] = autodesk.Revit
    sys.modules['Autodesk.Revit.DB'] = revit
    sys.modules['RevitServices'] = services
    sys.modules['RevitServices.Persistence'] = persistence


install_stubs()

import graph2


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------
class Point(object):
    def __init__(self, x, y, z=0.0):
        self.X = x
        self.Y = y
        self.Z = z


class Projection(object):
    def __init__(self, point, distance):
        self.X = point[0]
        self.Y = point[1]
        self.Z = point[2]
        self.Distance = distance


class Curve(object):
    def __init__(self, ax, ay, bx, by):
        self.a = (ax, ay, 0.0)
        self.b = (bx, by, 0.0)

    @property
    def Length(self):
        return ((self.b[0] - self.a[0]) ** 2 + (self.b[1] - self.a[1]) ** 2) ** 0.5

    def Evaluate(self, t, normalized=False):
        return Point(self.a[0] + (self.b[0] - self.a[0]) * t,
                     self.a[1] + (self.b[1] - self.a[1]) * t, 0.0)

    def Project(self, point):
        ax, ay, _ = self.a
        bx, by, _ = self.b
        abx, aby = bx - ax, by - ay
        squared = abx * abx + aby * aby
        if squared < 1e-18:
            t = 0.0
        else:
            t = ((point.X - ax) * abx + (point.Y - ay) * aby) / squared
            t = max(0.0, min(1.0, t))
        cx, cy = ax + abx * t, ay + aby * t
        return Projection((cx, cy, 0.0), ((point.X - cx) ** 2 + (point.Y - cy) ** 2) ** 0.5)


def descriptor(element_id, curve, wall_type='Closed Wall - RX'):
    return {'type': wall_type, 'type_id': 1, 'level_id': 1, 'height': 10.0,
            'offset': 0.0, 'flipped': False, 'element_id': element_id, 'curve': curve}


def stand(number, curves, element_id=None, level_name='Level 1'):
    """A stand with one wall per boundary curve."""
    walls = []
    for index, curve in enumerate(curves):
        walls.append(descriptor((element_id or 1) * 1000 + index, curve))
    xs = [c.a[0] for c in curves] + [c.b[0] for c in curves]
    ys = [c.a[1] for c in curves] + [c.b[1] for c in curves]
    centre = Point((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
    return {
        'number': number,
        'element_id': element_id or 1,
        'parameters': {},
        'level_id': 1,
        'level_name': level_name,
        'loops': [list(curves)],
        'wall_loops': [walls],
        'boundary_ids': [w['element_id'] for w in walls],
        'centroid': centre,
        'room_point': centre,
        'has_geometry': True,
        'placement_status': 'PLACED',
    }


FAILURES = []


def check(condition, message):
    if not condition:
        FAILURES.append(message)


def plan(base, main, redraw, redraw_wins=False):
    issues = []
    gplan, pplan = graph2.plan_clusters(base, main, redraw, 0.03, 0.25, redraw_wins, [], issues)
    return gplan, pplan, issues


# --------------------------------------------------------------------------
def test_two_isolated_stands_one_moves():
    wall_a = Curve(0, 0, 10, 0)
    wall_b = Curve(0, 10, 10, 10)
    base = {'A': stand('A', [wall_a], 1), 'B': stand('B', [wall_b], 2)}
    main = dict(base)
    moved = Curve(0, 20, 10, 20)
    redraw = {'A': stand('A', [moved], 1), 'B': stand('B', [wall_b], 2)}
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    check(gplan[0]['status'] == 'PLANNED', 'it should be planned')
    check(gplan[0]['walls_created'] == 1, 'the new wall should be created')
    check(gplan[0]['walls_deleted'] == 1, 'the old wall should be deleted')
    check(gplan[0]['walls_kept'] == 0, 'nothing should be kept')


def test_unchanged_model_plans_nothing():
    wall_a = Curve(0, 0, 10, 0)
    base = {'A': stand('A', [wall_a], 1)}
    gplan, pplan, issues = plan(base, dict(base), dict(base))
    check(gplan == [], 'nothing should be planned, got %s' % gplan)


def test_shared_wall_is_one_change():
    """Two stands that share a wall, one of them changes."""
    shared = Curve(10, 0, 10, 10)
    left = Curve(0, 0, 10, 0)
    base = {'A': stand('A', [shared, left], 1), 'B': stand('B', [shared], 2)}
    main = dict(base)
    moved_left = Curve(0, -2, 10, -2)
    redraw = {'A': stand('A', [shared, moved_left], 1), 'B': stand('B', [shared], 2)}
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    check(gplan[0]['walls_created'] == 1, 'one new wall should be created')
    check(gplan[0]['walls_deleted'] == 1, 'one old wall should be deleted')
    check(gplan[0]['walls_kept'] == 1, 'the shared wall should be kept')


def test_added_stand_is_planned():
    wall_a = Curve(0, 0, 10, 0)
    base = {'A': stand('A', [wall_a], 1)}
    main = dict(base)
    new_wall = Curve(20, 0, 30, 0)
    redraw = {'A': stand('A', [wall_a], 1), 'B': stand('B', [new_wall], 2)}
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    rooms = gplan[0]['_rooms']
    check(('ADD', 'B') in rooms, 'the new stand should be created, got %s' % rooms)
    check(gplan[0]['walls_created'] == 1, 'its wall should be created')


def test_removed_stand_is_planned():
    wall_a = Curve(0, 0, 10, 0)
    base = {'A': stand('A', [wall_a], 1), 'B': stand('B', [Curve(20, 0, 30, 0)], 2)}
    main = dict(base)
    redraw = {'A': stand('A', [wall_a], 1)}
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    rooms = gplan[0]['_rooms']
    check(('DELETE', 'B') in rooms, 'the removed stand should be deleted, got %s' % rooms)


def test_conflict_blocks_the_cluster():
    wall_a = Curve(0, 0, 10, 0)
    base = {'A': stand('A', [wall_a], 1)}
    main = {'A': stand('A', [Curve(0, 5, 10, 5)], 1)}     # Main moved it
    redraw = {'A': stand('A', [Curve(0, 9, 10, 9)], 1)}   # the Redraw moved it too
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'the cluster should be reported, got %d' % len(gplan))
    check(gplan[0]['status'] == 'SKIPPED', 'it should be skipped')
    check(len(issues) >= 1, 'a conflict should be reported')


def test_redraw_wins_overrides_a_conflict():
    wall_a = Curve(0, 0, 10, 0)
    base = {'A': stand('A', [wall_a], 1)}
    main = {'A': stand('A', [Curve(0, 5, 10, 5)], 1)}
    redraw = {'A': stand('A', [Curve(0, 9, 10, 9)], 1)}
    gplan, pplan, issues = plan(base, main, redraw, redraw_wins=True)
    check(len(gplan) == 1, 'the cluster should be reported, got %d' % len(gplan))
    check(gplan[0]['status'] == 'PLANNED', 'it should be planned when the Redraw wins')
    check(gplan[0]['walls_created'] == 1, "the Redraw's wall should be created")
    check(gplan[0]['walls_deleted'] == 1, "Main's version should be deleted")


def test_redraw_wins_still_blocks_an_unsupported_wall_type():
    wall_a = Curve(0, 0, 10, 0)
    base = {'A': stand('A', [wall_a], 1)}
    main = {'A': stand('A', [Curve(0, 5, 10, 5)], 1)}
    redraw = {'A': stand('A', [Curve(0, 9, 10, 9)], 1)}
    redraw['A']['wall_loops'][0][0]['type'] = 'Basic'
    gplan, pplan, issues = plan(base, main, redraw, redraw_wins=True)
    check(gplan[0]['status'] == 'SKIPPED',
          'winning the conflict must not override the wall type rule')


def test_unsupported_wall_type_blocks_the_cluster():
    wall_a = Curve(0, 0, 10, 0)
    base = {'A': stand('A', [wall_a], 1)}
    main = dict(base)
    redraw = {'A': stand('A', [Curve(0, 9, 10, 9)], 1)}
    # the Redraw's wall is not an RX type
    redraw['A']['wall_loops'][0][0]['type'] = 'Basic'
    gplan, pplan, issues = plan(base, main, redraw)
    check(gplan[0]['status'] == 'SKIPPED', 'a non RX wall should block the cluster')
    check('Unsupported' in gplan[0]['reason'], 'the reason should say why, got %r'
          % gplan[0]['reason'])


def test_boundary_that_is_not_walls_blocks_the_cluster():
    wall_a = Curve(0, 0, 10, 0)
    base = {'A': stand('A', [wall_a], 1)}
    main = dict(base)
    redraw = {'A': stand('A', [Curve(0, 9, 10, 9)], 1)}
    # a boundary curve with no wall behind it
    redraw['A']['loops'][0].append(Curve(10, 0, 10, 10))
    gplan, pplan, issues = plan(base, main, redraw)
    check(gplan[0]['status'] == 'SKIPPED', 'a boundary that is not walls should block')


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_') and callable(value)]
    for test in tests:
        try:
            test()
        except Exception as ex:
            FAILURES.append('%s raised %s: %s' % (test.__name__, type(ex).__name__, ex))
    if FAILURES:
        print('%d failure(s):' % len(FAILURES))
        for failure in FAILURES:
            print('  ' + failure)
        return 1
    print('%d tests passed' % len(tests))
    return 0


if __name__ == '__main__':
    sys.exit(main())
