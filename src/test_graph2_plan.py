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
    'BuiltInCategory', 'FilteredElementCollector', 'LinkElementId', 'FilePath', 'OpenOptions',
    'Transaction', 'SpatialElementBoundaryOptions', 'SpatialElementBoundaryLocation', 'UnitTypeId',
    'UnitUtils', 'StorageType', 'ElementId', 'Level', 'View', 'Color', 'CurveLoop', 'DirectShape',
    'GeometryCreationUtilities', 'OverrideGraphicSettings', 'UV',
]


def install_stubs():
    clr = types.ModuleType('clr')
    clr.AddReference = lambda *args, **kwargs: None
    revit = types.ModuleType('Autodesk.Revit.DB')
    for name in REVIT_NAMES:
        setattr(revit, name, type(name, (object,), {}))

    class ElementId(object):
        def __init__(self, value=-1):
            self.Value = int(value)

        def __eq__(self, other):
            return isinstance(other, ElementId) and other.Value == self.Value

        def __hash__(self):
            return hash(self.Value)

    revit.ElementId = ElementId

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


def stand(number, walls, element_id=None, level_name='Level 1', parameters=None):
    """A stand. `walls` is a list of (element_id, curve) pairs."""
    descriptors = [descriptor(wall_id, curve) for wall_id, curve in walls]
    curves = [curve for _, curve in walls]
    xs = [c.a[0] for c in curves] + [c.b[0] for c in curves]
    ys = [c.a[1] for c in curves] + [c.b[1] for c in curves]
    if xs:
        centre = Point((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
    else:
        centre = Point(0.0, 0.0)
    return {
        'number': number,
        'element_id': element_id or 1,
        'parameters': parameters if parameters is not None else {},
        'level_id': 1,
        'level_name': level_name,
        'loops': [list(curves)],
        'wall_loops': [descriptors],
        'boundary_ids': [w['element_id'] for w in descriptors],
        'centroid': centre,
        'room_point': centre,
        'has_geometry': bool(curves),
        'placement_status': 'PLACED' if curves else 'NOT_PLACED',
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


def test_unplaced_stand_in_the_redraw_is_removed():
    """A room that is still there but bounds nothing is not a stand any more,
    so its room goes too."""
    wall_a = Curve(0, 0, 10, 0)
    base = {'A01': stand('A01', [(11, wall_a)], 1),
            'A02': stand('A02', [(21, Curve(0, 10, 10, 10))], 2)}
    main = dict(base)
    # A02 is still in the Redraw but it has no boundary any more
    redraw = {'A01': stand('A01', [(11, wall_a)], 1)}
    empty = stand('A02', [], 2)
    redraw['A02'] = empty
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    rooms = gplan[0]['_rooms']
    check(('DELETE', 'A02') in rooms,
          'the stand with no boundary should be deleted, got %s' % rooms)


def test_skipped_cluster_reports_where_every_stand_is():
    """The QA has to say where each stand is, so the operator can see whether
    the Redraw really lost the stand."""
    shared = Curve(10, 0, 10, 10)
    base = {'A01': stand('A01', [(10, shared), (11, Curve(0, 0, 10, 0))], 1),
            'A02': stand('A02', [(10, shared), (12, Curve(20, 0, 20, 10))], 2)}
    main = dict(base)
    redraw = {'A01': stand('A01', [(11, Curve(0, 0, 10, 0))], 1),
              'A02': stand('A02', [(12, Curve(20, 0, 20, 10))], 2)}
    qa = []
    gplan, pplan = graph2.plan_clusters(base, main, redraw, [], 0.03, 0.25, False, qa, [])
    check(gplan[0]['status'] == 'SKIPPED', 'the cluster should be skipped')
    check(len(qa) >= 2, 'every stand of the cluster should be reported, got %s' % qa)
    check(any('A02' in line and 'Redraw=' in line for line in qa),
          'the report should name the stand and say where it is, got %s' % qa)


def test_added_stand_collects_its_parameters():
    """A parameter is a dict of status and value. Reading it like a pair
    raises KeyError(0), which the cluster reports as the reason "0"."""
    from Autodesk.Revit.DB import BuiltInParameter  # noqa: F401  (stub)
    parameters = {'Product Code': {'status': 'VALUE', 'value': 'PX-114'},
                  'Floorplan Sector': {'status': 'NO_VALUE', 'value': None},
                  'Floorplan Hall': {'status': 'PARAMETER_NOT_FOUND', 'value': None}}
    base = {'A01': stand('A01', [(11, Curve(0, 0, 10, 0))], 1)}
    main = dict(base)
    redraw = {'A01': stand('A01', [(11, Curve(0, 0, 10, 0))], 1),
              'A05': stand('A05', [(21, Curve(20, 0, 30, 0))], 2, parameters=parameters)}
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    check(('ADD', 'A05') in gplan[0]['_rooms'],
          'the new stand should be created, got %s' % gplan[0]['_rooms'])
    # the same reading, the way the apply does it
    values = {}
    for name in graph2.PARAMS:
        entry = parameters.get(name)
        if not isinstance(entry, dict):
            continue
        if entry.get('status') != 'VALUE':
            continue
        if entry.get('value') is None:
            continue
        values[name] = entry.get('value')
    check(values == {'Product Code': 'PX-114'},
          'only the parameter with a value should be copied, got %s' % values)


def test_preview_marker_round_trips():
    """The marker is what a later run uses to find a band again, so the view
    and the walls it recoloured have to survive the round trip."""
    text = graph2.preview_marker(3145, [101, 102, 103])
    check(text.startswith(graph2.PREVIEW_MARK),
          'the marker should start with the preview mark, got %r' % text)
    view, walls = graph2.parse_preview_marker(text)
    check(view == 3145, 'the view id should round trip, got %r' % view)
    check(walls == [101, 102, 103], 'the wall ids should round trip, got %r' % walls)


def test_preview_marker_without_walls_round_trips():
    """Blue bands recolour nothing, so their marker carries no walls."""
    text = graph2.preview_marker(77, [])
    view, walls = graph2.parse_preview_marker(text)
    check(view == 77, 'the view id should round trip, got %r' % view)
    check(walls == [], 'the walls should be empty, got %r' % walls)


def test_preview_marker_parses_the_older_format():
    """The markers left by earlier runs only carried the wall ids; the sweep
    has to find their overrides once the marker cannot name a view."""
    view, walls = graph2.parse_preview_marker('REDRAW MERGE PREVIEW walls=5,6')
    check(view is None, 'the older marker has no view, got %r' % view)
    check(walls == [], 'the older format does not use w=, got %r' % walls)


def test_unchanged_stand_is_not_sampled():
    """An untouched stand has exactly the same boundary in Main and in the
    Redraw, so it must be recognised without sampling every curve through the
    Revit API - that is what made a large model slow."""
    calls = []
    original = graph2.bd

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    graph2.bd = spy
    try:
        base = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
        main = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
        redraw = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
        gplan, pplan, issues = plan(base, main, redraw)
    finally:
        graph2.bd = original
    check(gplan == [], 'nothing should be planned, got %s' % gplan)
    check(calls == [], 'an unchanged stand should not be sampled, got %d call(s)'
          % len(calls))


def test_changed_stand_is_still_measured():
    """The fast path must not hide a real change: a moved wall is still
    measured and still planned."""
    moves = []
    original = graph2.bd

    def spy(*args, **kwargs):
        moves.append(1)
        return original(*args, **kwargs)

    graph2.bd = spy
    try:
        base = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
        main = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
        redraw = {'A': stand('A', [(11, Curve(0, 2, 10, 2))], 1)}
        gplan, pplan, issues = plan(base, main, redraw)
    finally:
        graph2.bd = original
    check(len(moves) >= 1, 'a moved boundary has to be measured')
    check(len(gplan) == 1 and gplan[0]['walls_created'] == 1,
          'the moved wall should still be planned, got %s' % gplan)


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
