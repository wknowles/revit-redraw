# -*- coding: utf-8 -*-
"""Offline test for graph 2's planning.

    python3 src/test_graph2_plan.py

Drives plan_clusters() with stand, wall and curve fakes, so the glue between
the Revit data and the merge core is exercised without Revit. The fakes are
deliberately thin: they only have to behave enough like the Revit API for the
geometry helpers.

A wall shared by two stands has the same element id in both, the way Revit
does it, so the clustering can find it.
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


def stand(number, walls, element_id=None, level_name='Level 1'):
    """A stand. `walls` is a list of (element_id, curve) pairs."""
    descriptors = [descriptor(wall_id, curve) for wall_id, curve in walls]
    curves = [curve for _, curve in walls]
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
        'wall_loops': [descriptors],
        'boundary_ids': [w['element_id'] for w in descriptors],
        'centroid': centre,
        'room_point': centre,
        'has_geometry': True,
        'placement_status': 'PLACED',
    }


FAILURES = []


def check(condition, message):
    if not condition:
        FAILURES.append(message)


def plan(base, main, redraw, redraw_wins=False, redraw_walls=None):
    issues = []
    gplan, pplan = graph2.plan_clusters(base, main, redraw, redraw_walls or [],
                                        0.03, 0.25, redraw_wins, [], issues)
    return gplan, pplan, issues


# --------------------------------------------------------------------------
def test_two_isolated_stands_one_moves():
    wall_a = Curve(0, 0, 10, 0)
    wall_b = Curve(0, 10, 10, 10)
    base = {'A': stand('A', [(11, wall_a)], 1), 'B': stand('B', [(21, wall_b)], 2)}
    main = dict(base)
    moved = Curve(0, 20, 10, 20)
    redraw = {'A': stand('A', [(11, moved)], 1), 'B': stand('B', [(21, wall_b)], 2)}
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    check(gplan[0]['status'] == 'PLANNED', 'it should be planned')
    check(gplan[0]['walls_created'] == 1, 'the new wall should be created')
    check(gplan[0]['walls_deleted'] == 1, 'the old wall should be deleted')
    check(gplan[0]['walls_kept'] == 0, 'nothing should be kept')


def test_unchanged_model_plans_nothing():
    base = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
    gplan, pplan, issues = plan(base, dict(base), dict(base))
    check(gplan == [], 'nothing should be planned, got %s' % gplan)


def test_shared_wall_is_one_change():
    """Two stands that share a wall, one of them changes."""
    shared = Curve(10, 0, 10, 10)
    left = Curve(0, 0, 10, 0)
    base = {'A': stand('A', [(10, shared), (11, left)], 1),
            'B': stand('B', [(10, shared)], 2)}
    main = dict(base)
    moved_left = Curve(0, -2, 10, -2)
    redraw = {'A': stand('A', [(10, shared), (12, moved_left)], 1),
              'B': stand('B', [(10, shared)], 2)}
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    check(gplan[0]['walls_created'] == 1, 'one new wall should be created')
    check(gplan[0]['walls_deleted'] == 1, 'one old wall should be deleted')
    check(gplan[0]['walls_kept'] == 1, 'the shared wall should be kept')


def test_added_stand_is_planned():
    wall_a = Curve(0, 0, 10, 0)
    base = {'A': stand('A', [(11, wall_a)], 1)}
    main = dict(base)
    redraw = {'A': stand('A', [(11, wall_a)], 1),
              'B': stand('B', [(21, Curve(20, 0, 30, 0))], 2)}
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    rooms = gplan[0]['_rooms']
    check(('ADD', 'B') in rooms, 'the new stand should be created, got %s' % rooms)
    check(gplan[0]['walls_created'] == 1, 'its wall should be created')


def test_removed_stand_is_planned():
    wall_a = Curve(0, 0, 10, 0)
    base = {'A': stand('A', [(11, wall_a)], 1),
            'B': stand('B', [(21, Curve(20, 0, 30, 0))], 2)}
    main = dict(base)
    redraw = {'A': stand('A', [(11, wall_a)], 1)}
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    rooms = gplan[0]['_rooms']
    check(('DELETE', 'B') in rooms, 'the removed stand should be deleted, got %s' % rooms)


def test_removed_stand_keeps_the_walls_that_survived():
    """The reported case: the separating wall was removed in the Redraw and
    room A02 was deleted. A02's own outer wall survives in the Redraw as an
    orphan - it no longer bounds a room - so it must not be taken out of Main.
    Only the wall that was really removed goes."""
    shared = Curve(10, 0, 10, 10)
    a01_outer = Curve(20, 0, 20, 10)
    a02_outer = Curve(20, 10, 20, 20)
    base = {'A01': stand('A01', [(10, shared), (11, a01_outer)], 1),
            'A02': stand('A02', [(10, shared), (12, a02_outer)], 2)}
    main = dict(base)
    redraw = {'A01': stand('A01', [(11, a01_outer)], 1)}    # the shared wall is gone
    # the Redraw still has both outer walls; A02's no longer bounds a room
    redraw_walls = graph2.wall_records([descriptor(11, a01_outer),
                                         descriptor(12, a02_outer)])
    gplan, pplan, issues = plan(base, main, redraw, redraw_walls=redraw_walls)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    check(gplan[0]['status'] == 'PLANNED', 'it should be planned')
    check(gplan[0]['walls_deleted'] == 1,
          'only the wall that was removed should be deleted, got %d'
          % gplan[0]['walls_deleted'])
    check(gplan[0]['walls_kept'] == 2,
          'the walls that survived should be kept, got %d' % gplan[0]['walls_kept'])
    rooms = gplan[0]['_rooms']
    check(('DELETE', 'A02') in rooms, 'the deleted stand should be removed, got %s' % rooms)


def test_removed_stand_without_the_inventory_loses_its_walls():
    """Without the Redraw's wall inventory the surviving walls look deleted."""
    shared = Curve(10, 0, 10, 10)
    a01_outer = Curve(20, 0, 20, 10)
    a02_outer = Curve(20, 10, 20, 20)
    base = {'A01': stand('A01', [(10, shared), (11, a01_outer)], 1),
            'A02': stand('A02', [(10, shared), (12, a02_outer)], 2)}
    main = dict(base)
    redraw = {'A01': stand('A01', [(11, a01_outer)], 1)}
    gplan, pplan, issues = plan(base, main, redraw)      # no inventory
    check(gplan[0]['walls_deleted'] == 2,
          'without the inventory both of A02 walls look deleted, got %d'
          % gplan[0]['walls_deleted'])


def test_conflict_blocks_the_cluster():
    base = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
    main = {'A': stand('A', [(11, Curve(0, 5, 10, 5))], 1)}     # Main moved it
    redraw = {'A': stand('A', [(11, Curve(0, 9, 10, 9))], 1)}   # the Redraw moved it too
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'the cluster should be reported, got %d' % len(gplan))
    check(gplan[0]['status'] == 'SKIPPED', 'it should be skipped')
    check(len(issues) >= 1, 'a conflict should be reported')


def test_redraw_wins_overrides_a_conflict():
    base = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
    main = {'A': stand('A', [(11, Curve(0, 5, 10, 5))], 1)}
    redraw = {'A': stand('A', [(11, Curve(0, 9, 10, 9))], 1)}
    gplan, pplan, issues = plan(base, main, redraw, redraw_wins=True)
    check(len(gplan) == 1, 'the cluster should be reported, got %d' % len(gplan))
    check(gplan[0]['status'] == 'PLANNED', 'it should be planned when the Redraw wins')
    check(gplan[0]['walls_created'] == 1, "the Redraw's wall should be created")
    check(gplan[0]['walls_deleted'] == 1, "Main's version should be deleted")


def test_redraw_wins_still_blocks_an_unsupported_wall_type():
    base = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
    main = {'A': stand('A', [(11, Curve(0, 5, 10, 5))], 1)}
    redraw = {'A': stand('A', [(11, Curve(0, 9, 10, 9))], 1)}
    redraw['A']['wall_loops'][0][0]['type'] = 'Basic'
    gplan, pplan, issues = plan(base, main, redraw, redraw_wins=True)
    check(gplan[0]['status'] == 'SKIPPED',
          'winning the conflict must not override the wall type rule')


def test_unsupported_wall_type_blocks_the_cluster():
    base = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
    main = dict(base)
    redraw = {'A': stand('A', [(11, Curve(0, 9, 10, 9))], 1)}
    redraw['A']['wall_loops'][0][0]['type'] = 'Basic'
    gplan, pplan, issues = plan(base, main, redraw)
    check(gplan[0]['status'] == 'SKIPPED', 'a non RX wall should block the cluster')
    check('Unsupported' in gplan[0]['reason'], 'the reason should say why, got %r'
          % gplan[0]['reason'])


def test_boundary_that_is_not_walls_blocks_the_cluster():
    base = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
    main = dict(base)
    redraw = {'A': stand('A', [(11, Curve(0, 9, 10, 9))], 1)}
    redraw['A']['loops'][0].append(Curve(10, 0, 10, 10))
    gplan, pplan, issues = plan(base, main, redraw)
    check(gplan[0]['status'] == 'SKIPPED', 'a boundary that is not walls should block')


def test_wall_between_two_surviving_stands_blocks_the_cluster():
    """Removing a wall that separates two stands which both keep a room would
    put both rooms in one region, and Revit's answer to that is a modal
    warning that edits the document outside the merge's transaction."""
    shared = Curve(10, 0, 10, 10)
    base = {'A01': stand('A01', [(10, shared), (11, Curve(0, 0, 10, 0))], 1),
            'A02': stand('A02', [(10, shared), (12, Curve(20, 0, 20, 10))], 2)}
    main = dict(base)
    # the Redraw removed the wall but kept both rooms
    redraw = {'A01': stand('A01', [(11, Curve(0, 0, 10, 0))], 1),
              'A02': stand('A02', [(12, Curve(20, 0, 20, 10))], 2)}
    gplan, pplan, issues = plan(base, main, redraw)
    check(gplan[0]['status'] == 'SKIPPED',
          'removing a wall between two surviving stands must be skipped')
    check('one region' in gplan[0]['reason'],
          'the reason should say why, got %r' % gplan[0]['reason'])


def test_wall_between_a_surviving_and_a_removed_stand_is_allowed():
    """The same wall, but one of the two stands is deleted, so the model never
    reaches the ambiguous state."""
    shared = Curve(10, 0, 10, 10)
    outer = Curve(20, 0, 20, 10)
    base = {'A01': stand('A01', [(10, shared), (11, outer)], 1),
            'A02': stand('A02', [(10, shared), (11, outer)], 2)}
    main = dict(base)
    redraw = {'A01': stand('A01', [(11, outer)], 1)}
    redraw_walls = graph2.wall_records([descriptor(11, outer)])
    gplan, pplan, issues = plan(base, main, redraw, redraw_walls=redraw_walls)
    check(gplan[0]['status'] == 'PLANNED',
          'removing the wall when the other stand goes is fine, got %r'
          % gplan[0].get('reason'))
    check(gplan[0]['walls_deleted'] == 1, 'only the shared wall should be deleted')


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
