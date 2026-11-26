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
    'GeometryCreationUtilities', 'OverrideGraphicSettings', 'UV', 'WallType',
    'SubTransaction', 'TransactionStatus', 'FamilyInstance', 'Opening',
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

    class UnitTypeId(object):
        Meters = 'Meters'
        SquareMeters = 'SquareMeters'

    revit.UnitTypeId = UnitTypeId

    class UnitUtils(object):
        """Only the conversions graph 2 uses, with Revit's own factors."""

        @staticmethod
        def ConvertToInternalUnits(value, unit):
            if unit == 'Meters':
                return float(value) * 3.280839895013123
            if unit == 'SquareMeters':
                return float(value) * 10.763910416709722
            raise ValueError('unsupported unit %r' % (unit,))

    revit.UnitUtils = UnitUtils

    class SpatialElementBoundaryLocation(object):
        Center = 'Center'
        Finish = 'Finish'

    revit.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation

    class BuiltInCategory(object):
        OST_Rooms = -2000169

    revit.BuiltInCategory = BuiltInCategory

    class TransactionStatus(object):
        Committed = 'Committed'
        RolledBack = 'RolledBack'
        Error = 'Error'

    class SubTransaction(object):
        """Records the calls, so a test can see whether a cluster committed
        or rolled back."""
        calls = []

        def __init__(self, document):
            self.document = document

        def Start(self):
            SubTransaction.calls.append('start')

        def Commit(self):
            SubTransaction.calls.append('commit')
            return TransactionStatus.Committed

        def RollBack(self):
            SubTransaction.calls.append('rollback')

    revit.TransactionStatus = TransactionStatus
    revit.SubTransaction = SubTransaction

    class WallType(object):
        def __init__(self, name):
            self.Name = name
            self.Id = ElementId(1)

    revit.WallType = WallType

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
# the document's fixtures
# --------------------------------------------------------------------------
class FakeLevel(object):
    def __init__(self, name, element_id=1):
        self.Name = name
        self.Id = graph2.ElementId(element_id)


WALL_TYPES = ('Closed Wall - RX', 'Open Wall - RX')


def use_wall_types(*names):
    """Prime the Main wall type cache with the names a real document has."""
    from Autodesk.Revit.DB import WallType
    graph2._WALL_TYPES[id(graph2.doc)] = dict((name, WallType(name)) for name in names)


def use_levels(*names):
    """Prime the Main level cache, the way filtered collection would fill it."""
    graph2._LEVELS[id(graph2.doc)] = [FakeLevel(name) for name in names]


use_wall_types(*WALL_TYPES)
use_levels('Level 1')


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


def descriptor(element_id, curve, wall_type='Closed Wall - RX', level_name='Level 1'):
    return {'type': wall_type, 'type_id': 1, 'level_id': 1, 'level_name': level_name,
            'height': 10.0, 'offset': 0.0, 'flipped': False, 'element_id': element_id,
            'curve': curve}


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


def plan(base, main, redraw, redraw_wins=False, redraw_walls=None,
         protected=None, duplicates=None):
    issues = []
    gplan, pplan = graph2.plan_clusters(base, main, redraw, redraw_walls or [],
                                        0.03, 0.25, redraw_wins, [], issues,
                                        protected, duplicates)
    return gplan, pplan, issues


class FakeBoundarySegment(object):
    def __init__(self, element_id):
        self.ElementId = graph2.ElementId(element_id)


class FakeRoom(object):
    """A Room that only has what collect_exclusions() reads."""

    def __init__(self, number, element_id, wall_ids):
        self.Number = number
        self.Id = graph2.ElementId(element_id)
        self.wall_ids = list(wall_ids)

    def GetBoundarySegments(self, options):
        return [[FakeBoundarySegment(wall_id) for wall_id in self.wall_ids]]


class FakeRooms(object):
    """The filtered collector graph2 asks for Rooms, over a fixed list."""

    def __init__(self, rooms):
        self.rooms = rooms

    def OfCategory(self, category):
        return self

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        return self.rooms


def collect_exclusions(rooms, label='Main'):
    original = graph2.FilteredElementCollector
    graph2.FilteredElementCollector = lambda document: FakeRooms(rooms)
    qa = []
    try:
        protected, duplicates = graph2.collect_exclusions(object(), qa, label)
    finally:
        graph2.FilteredElementCollector = original
    return protected, duplicates, qa


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


def test_added_stand_reuses_the_neighbours_existing_wall():
    """A stand added next to an unchanged one shares its wall. That wall is
    already in Main, so it must be kept, never created again on top of itself:
    a duplicate coincident wall is invisible to the room-boundary checks."""
    shared = Curve(10, 0, 10, 10)
    a_outer = Curve(0, 0, 10, 0)
    b_outer = Curve(20, 0, 20, 10)
    base = {'A01': stand('A01', [(10, shared), (11, a_outer)], 1)}
    main = dict(base)
    redraw = {'A01': stand('A01', [(10, shared), (11, a_outer)], 1),
              'A02': stand('A02', [(10, shared), (21, b_outer)], 2)}
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    check(gplan[0]['status'] == 'PLANNED', 'it should be planned, got %r'
          % gplan[0].get('reason'))
    keep = [wall['ref']['element_id'] for wall in gplan[0]['_plan']['keep']]
    created = [wall['ref']['element_id'] for wall in gplan[0]['_plan']['create']]
    check(10 in keep, 'the shared wall should be kept, got keep=%s' % keep)
    check(10 not in created,
          'the shared wall must not be created again, got %s' % created)
    check(created == [21],
          "only the added stand's new wall should be created, got %s" % created)
    check(gplan[0]['walls_created'] == 1, 'one wall should be created')
    rooms = gplan[0]['_rooms']
    check(('ADD', 'A02') in rooms,
          'only the added stand needs room work, got %s' % rooms)


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
    # Winning the conflict no longer sweeps the retained wall into the deletes:
    # Main's wall was moved away from its baseline position, so the merge core
    # cannot tell it apart from a wall added to Main after the Redraw was cut.
    check(gplan[0]['_plan']['retained'],
          'Main\'s own wall should stay retained, got %s' % gplan[0]['_plan']['retained'])


def test_a_stand_recreated_in_the_redraw_replaces_main_walls():
    """A stand that exists in Main and the Redraw but not the baseline, at a
    different place with different walls, was re-created rather than edited.
    When the Redraw wins, Main's old walls must go with the old Room."""
    main = {'A05': stand('A05', [(21, Curve(0, 0, 10, 0))], 1)}
    redraw = {'A05': stand('A05', [(31, Curve(50, 50, 60, 50))], 2)}
    gplan, pplan, issues = plan({}, main, redraw, redraw_wins=True)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    check(gplan[0]['status'] == 'PLANNED', 'it should be planned, got %r'
          % gplan[0].get('reason'))
    deleted = [wall['ref']['element_id'] for wall in gplan[0]['_plan']['delete']]
    created = [wall['ref']['element_id'] for wall in gplan[0]['_plan']['create']]
    check(deleted == [21], "Main's old walls should be deleted, got %s" % deleted)
    check(created == [31], "the Redraw's walls should be created, got %s" % created)
    check(('MOVE', 'A05') in gplan[0]['_rooms'],
          'the existing Room should be moved, got %s' % gplan[0]['_rooms'])


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
    # A real Room keeps its location point when its walls change; the fixture
    # derives it from the bounding box, so pin it here to model a resize rather
    # than a move.
    for state in (base, main, redraw):
        state['A01']['room_point'] = Point(5.0, 5.0)
        state['A02']['room_point'] = Point(15.0, 5.0)
    gplan, pplan, issues = plan(base, main, redraw)
    check(gplan[0]['status'] == 'SKIPPED',
          'removing a wall between two surviving stands must be skipped')
    check('one region' in gplan[0]['reason'],
          'the reason should say why, got %r' % gplan[0]['reason'])


def test_a_moved_stand_vacates_the_wall_it_shared():
    """A stand that leaves the cluster for an island no longer occupies its old
    side, so the wall it shared may be deleted and the neighbour can expand."""
    shared = Curve(10, 0, 10, 10)
    base = {'A': stand('A', [(10, shared), (11, Curve(0, 0, 10, 0))], 1),
            'B': stand('B', [(10, shared), (12, Curve(20, 0, 20, 10))], 2)}
    main = dict(base)
    # B moves to its own island; A closes the gap with a new wall; the wall
    # they shared (10) is gone from the Redraw.
    redraw = {'A': stand('A', [(11, Curve(0, 0, 10, 0)), (13, Curve(20, 0, 20, 10))], 1),
              'B': stand('B', [(12, Curve(50, 0, 50, 10))], 2)}
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    check(gplan[0]['status'] == 'PLANNED',
          'a moved stand should not block the wall it left behind, got %r'
          % gplan[0].get('reason'))
    deleted = [wall['ref']['element_id'] for wall in gplan[0]['_plan']['delete']]
    check(10 in deleted, 'the vacated shared wall should be deleted, got %s' % deleted)


def test_redraw_wins_keeps_a_wall_added_to_main_after_the_redraw():
    """A Main wall that is not in the Redraw and not in the baseline was added
    to Main after the Redraw was cut, so the designer never touched it. Winning
    the conflict must not delete the later Main work."""
    wall_a = Curve(0, 0, 10, 0)
    later = Curve(0, 50, 10, 50)
    base = {'A': stand('A', [(11, wall_a)], 1)}
    main = {'A': stand('A', [(11, wall_a), (12, later)], 1)}
    redraw = {'A': stand('A', [(11, Curve(0, 2, 10, 2))], 1)}   # the Redraw moved it
    gplan, pplan, issues = plan(base, main, redraw, redraw_wins=True)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    check(gplan[0]['status'] == 'PLANNED', 'it should be planned, got %r'
          % gplan[0].get('reason'))
    check(gplan[0]['walls_deleted'] == 1,
          'only the wall the Redraw changed should be deleted, got %d deleted'
          % gplan[0]['walls_deleted'])
    retained = [wall['ref']['element_id'] for wall, _ in gplan[0]['_plan']['retained']]
    check(retained == [12],
          'the later Main wall should stay retained, got %s' % retained)


def test_replaced_wall_between_two_surviving_stands_blocks_the_cluster():
    """A replacement deletes Main's wall before creating the Redraw's, so a
    replaced wall shared by two surviving stands hits the same one-region
    state as a plain delete."""
    shared = Curve(10, 0, 10, 10)
    base = {'A01': stand('A01', [(10, shared), (11, Curve(0, 0, 10, 0))], 1),
            'A02': stand('A02', [(10, shared), (12, Curve(20, 0, 20, 10))], 2)}
    main = {'A01': stand('A01', [(10, shared), (11, Curve(0, 0, 10, 0))], 1),
            'A02': stand('A02', [(10, shared), (12, Curve(20, 0, 20, 10))], 2)}
    redraw = {'A01': stand('A01', [(10, shared), (11, Curve(0, -2, 10, -2))], 1),
              'A02': stand('A02', [(10, shared), (12, Curve(20, 0, 20, 10))], 2)}
    # The Room points do not move when a wall does; pin them so this tests a
    # replacement, not a move.
    for state in (base, main, redraw):
        state['A01']['room_point'] = Point(5.0, 5.0)
        state['A02']['room_point'] = Point(15.0, 5.0)
    # the shared wall stays where it is but the Redraw changes its type
    for state in (main, redraw):
        state['A01']['wall_loops'][0][0]['type'] = (
            'Open Wall - RX' if state is redraw else 'Closed Wall - RX')
        state['A02']['wall_loops'][0][0]['type'] = (
            'Open Wall - RX' if state is redraw else 'Closed Wall - RX')
    gplan, pplan, issues = plan(base, main, redraw)
    check(gplan[0]['status'] == 'SKIPPED',
          'replacing a wall between two surviving stands must be skipped, got %r'
          % gplan[0].get('status'))
    check('one region' in gplan[0].get('reason', ''),
          'the reason should say why, got %r' % gplan[0].get('reason'))


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


def test_blank_numbered_room_protects_its_walls():
    """A Room with no Number is skipped by collect(), so a wall it shares with
    a stand looks unused. Deleting it would put both Rooms in one region."""
    shared = Curve(10, 0, 10, 10)
    outer = Curve(20, 0, 20, 10)
    blank_outer = Curve(20, 10, 20, 20)
    rooms = [FakeRoom('A01', 1, [10, 11]), FakeRoom('', 2, [10, 12])]
    protected, duplicates, qa = collect_exclusions(rooms)
    check(protected == {10, 12},
          'only the blank Room boundaries should be protected, got %s' % protected)
    check(duplicates == set(), 'one blank Room is not a duplicate, got %s' % duplicates)
    check(any('blank Room Number' in line and 'element 2' in line for line in qa),
          'the QA should name the blank Room, got %s' % qa)

    base = {'A01': stand('A01', [(10, shared), (11, outer)], 1)}
    main = dict(base)
    redraw = {'A01': stand('A01', [(11, outer)], 1)}
    inventory = graph2.wall_records([descriptor(11, outer), descriptor(12, blank_outer)])
    # Without the protection the merge would delete the shared wall.
    unprotected, _, _ = plan(base, main, redraw, redraw_walls=inventory)
    check(unprotected[0]['status'] == 'PLANNED',
          'the test needs the wall to be deleted without protection, got %r'
          % unprotected[0].get('status'))
    gplan, pplan, issues = plan(base, main, redraw, redraw_walls=inventory,
                                protected=protected)
    check(gplan[0]['status'] == 'SKIPPED',
          'a wall shared with a blank Room must not be deleted, got %r'
          % gplan[0].get('status'))
    check('one region' in gplan[0].get('reason', ''),
          'the reason should say why, got %r' % gplan[0].get('reason'))


def test_duplicate_room_number_quarantines_its_cluster():
    """Two Rooms with the same Number cannot be told apart, so the cluster is
    skipped before any plan is built."""
    rooms = [FakeRoom('A01', 1, [10]), FakeRoom('A01', 2, [10])]
    protected, duplicates, qa = collect_exclusions(rooms)
    check(duplicates == {'A01'}, 'the duplicate Number should be reported, got %s'
          % duplicates)
    check(protected == {10}, 'the duplicate Rooms walls should be protected, got %s'
          % protected)
    check(any('duplicate Room Number' in line and 'A01' in line for line in qa),
          'the QA should name the duplicate Number, got %s' % qa)

    base = {'A01': stand('A01', [(10, Curve(0, 0, 10, 0))], 1)}
    main = dict(base)
    redraw = {'A01': stand('A01', [(10, Curve(0, 2, 10, 2))], 1)}
    gplan, pplan, issues = plan(base, main, redraw, duplicates=duplicates)
    check(len(gplan) == 1, 'the cluster should be reported, got %d' % len(gplan))
    check(gplan[0]['status'] == 'SKIPPED', 'the cluster should be quarantined, got %r'
          % gplan[0].get('status'))
    check('A01' in gplan[0].get('reason', '') and 'Duplicate' in gplan[0].get('reason', ''),
          'the reason should name the duplicate Number, got %r'
          % gplan[0].get('reason'))
    check(len(issues) == 1 and issues[0]['type'] == 'DUPLICATE ROOM NUMBER',
          'one issue should be reported, got %s' % issues)
    check('_plan' not in gplan[0], 'a quarantined cluster must not be planned')


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
    for state in (base, redraw):
        state['A01']['room_point'] = Point(5.0, 5.0)
        state['A02']['room_point'] = Point(15.0, 5.0)
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


def test_missing_wall_type_skips_the_cluster_before_any_mutation():
    """The apply loop deletes before it creates, so a wall type Main does not
    have would destroy the old wall before create failed. It is caught in
    planning instead."""
    base = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
    main = dict(base)
    redraw = {'A': stand('A', [(11, Curve(0, 2, 10, 2))], 1)}
    use_wall_types('Open Wall - RX')
    try:
        gplan, pplan, issues = plan(base, main, redraw)
    finally:
        use_wall_types(*WALL_TYPES)
    check(gplan[0]['status'] == 'SKIPPED',
          'a wall type Main does not have should skip the cluster, got %r'
          % gplan[0]['status'])
    check('Closed Wall - RX' in gplan[0].get('reason', ''),
          'the reason should name the missing type, got %r' % gplan[0].get('reason'))


def test_unresolved_level_skips_the_cluster_before_any_mutation():
    """A cluster whose build level cannot be resolved would fail on its first
    create, after its deletes."""
    base = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
    main = dict(base)
    main['A']['level_id'] = None
    redraw = {'A': stand('A', [(11, Curve(0, 2, 10, 2))], 1, level_name='Level 9')}
    gplan, pplan, issues = plan(base, main, redraw)
    check(gplan[0]['status'] == 'SKIPPED',
          'a cluster with no build level should be skipped, got %r' % gplan[0]['status'])
    check('level' in gplan[0].get('reason', '').lower(),
          'the reason should mention the level, got %r' % gplan[0].get('reason'))


def test_wall_moved_to_another_level_is_replaced():
    """A Redraw wall on a different level is a different wall, not a kept one.

    The stand also moves, so the cluster is planned: the wall that stayed put
    changes level only, and that alone makes it a replace.
    """
    base = {'A': stand('A', [(11, Curve(0, 0, 10, 0)), (12, Curve(0, 10, 10, 10))], 1)}
    main = dict(base)
    redraw = {'A': stand('A', [(11, Curve(0, 2, 10, 2)), (12, Curve(0, 10, 10, 10))], 1)}
    redraw['A']['wall_loops'][0][1]['level_name'] = 'Level 2'
    use_levels('Level 1', 'Level 2')
    try:
        gplan, pplan, issues = plan(base, main, redraw)
    finally:
        use_levels('Level 1')
    check(gplan[0]['status'] == 'PLANNED',
          'the cluster should be planned, got %r' % gplan[0].get('status'))
    replaced = [wall['ref']['element_id'] for wall, _ in gplan[0]['_plan']['replace']]
    kept = [wall['ref']['element_id'] for wall in gplan[0]['_plan']['keep']]
    check(12 in replaced,
          'the wall on another level should be replaced, got replaced=%s kept=%s'
          % (replaced, kept))
    check(12 not in kept, 'the wall on another level must not be kept')


def test_level_name_missing_in_main_skips_the_cluster():
    """A create/replace wall whose level is not in Main would fail after the
    deletes, so the cluster is skipped in planning instead."""
    base = {'A': stand('A', [(11, Curve(0, 0, 10, 0)), (12, Curve(0, 10, 10, 10))], 1)}
    main = dict(base)
    redraw = {'A': stand('A', [(11, Curve(0, 2, 10, 2)), (12, Curve(0, 10, 10, 10))], 1)}
    redraw['A']['wall_loops'][0][1]['level_name'] = 'Level 9'
    gplan, pplan, issues = plan(base, main, redraw)
    check(gplan[0]['status'] == 'SKIPPED',
          'a wall level Main does not have should skip the cluster, got %r'
          % gplan[0].get('status'))
    check('Level not found in Main: Level 9' in gplan[0].get('reason', ''),
          'the reason should name the missing level, got %r' % gplan[0].get('reason'))


def test_create_wall_prefers_the_walls_own_level():
    """A wall moved to another level is rebuilt on that level, not on the
    cluster's level; the cluster level is only the fallback."""
    from Autodesk.Revit.DB import WallType

    class FakeDocument(object):
        def GetElement(self, element_id):
            return None

    host = FakeDocument()
    graph2._WALL_TYPES[id(host)] = {'Closed Wall - RX': WallType('Closed Wall - RX')}
    graph2._LEVELS[id(host)] = [FakeLevel('Level 1', 1), FakeLevel('Level 2', 2)]
    seen = []
    original = graph2.Wall

    class FakeWall(object):
        def __init__(self, element_id):
            self.Id = graph2.ElementId(element_id)

        @staticmethod
        def Create(host, curve, type_id, level_id, height, offset, flipped, structural):
            seen.append(level_id)
            return FakeWall(99)

    graph2.Wall = FakeWall
    try:
        graph2.create_wall(host, graph2.ElementId(1),
                           descriptor(21, Curve(0, 0, 10, 0), level_name='Level 2'))
        graph2.create_wall(host, graph2.ElementId(7),
                           descriptor(22, Curve(0, 0, 10, 0), level_name=None))
    finally:
        graph2.Wall = original
        graph2._WALL_TYPES.pop(id(host), None)
        graph2._LEVELS.pop(id(host), None)
    check([element_id.Value for element_id in seen] == [2, 7],
          'the wall level should win and the cluster level should be the fallback, got %s'
          % [element_id.Value for element_id in seen])


def test_hosted_insert_skips_the_cluster():
    """Deleting a wall Revit cascades its doors, windows and openings, so the
    cluster is skipped rather than silently losing them."""
    from Autodesk.Revit.DB import WallType

    class FakeCategory(object):
        def __init__(self, name):
            self.Name = name

    class HostWall(object):
        def GetDependentElements(self, element_ids):
            return [graph2.ElementId(21), graph2.ElementId(22)]

    class FakeInsert(graph2.FamilyInstance):
        def __init__(self, name):
            self.Category = FakeCategory(name)

    class FakeOpening(graph2.Opening):
        def __init__(self):
            self.Category = FakeCategory('Openings')

    elements = {21: FakeInsert('Doors'), 22: FakeOpening()}

    class FakeDocument(object):
        def GetElement(self, element_id):
            key = element_id.Value if hasattr(element_id, 'Value') else int(element_id)
            if key == 11:
                return HostWall()
            return elements.get(key)

    host = FakeDocument()
    graph2._WALL_TYPES[id(host)] = {'Closed Wall - RX': WallType('Closed Wall - RX')}
    graph2._LEVELS[id(host)] = [FakeLevel('Level 1', 1)]
    original = graph2.doc
    graph2.doc = host
    base = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
    main = dict(base)
    redraw = {'A': stand('A', [(11, Curve(0, 2, 10, 2))], 1)}
    try:
        gplan, pplan, issues = plan(base, main, redraw)
    finally:
        graph2.doc = original
        graph2._WALL_TYPES.pop(id(host), None)
        graph2._LEVELS.pop(id(host), None)
    check(gplan[0]['status'] == 'SKIPPED',
          'a wall with a hosted insert must not be deleted, got %r'
          % gplan[0].get('status'))
    reason = gplan[0].get('reason', '')
    check('Doors' in reason and '21' in reason,
          'the reason should name the hosted insert, got %r' % reason)


def test_failing_cluster_rolls_back_while_the_next_one_commits():
    """A failure after a delete must not leave the delete behind: the cluster
    is rolled back, and a cluster that succeeds still commits."""
    deleted = []

    class Doc(object):
        def Delete(self, element_id):
            deleted.append(element_id.Value)

        def Regenerate(self):
            pass

    doc = Doc()
    original = graph2.create_wall

    def boom(*args, **kwargs):
        raise Exception('wall type went missing')

    graph2.create_wall = boom
    graph2.SubTransaction.calls = []
    try:
        bad = {'cluster': 'C1', 'status': 'PLANNED', 'affected': [], 'stands': [],
               'change': 'CLUSTER', '_level': 1, '_rooms': [],
               '_plan': {'keep': [], 'retained': [], 'replace': [],
                         'delete': [{'ref': {'element_id': 11, 'type': 'Closed Wall - RX'}}],
                         'create': [{'ref': {'element_id': 21, 'type': 'Closed Wall - RX'}}]}}
        good = {'cluster': 'C2', 'status': 'PLANNED', 'affected': [], 'stands': [],
                'change': 'CLUSTER', '_level': 1, '_rooms': [],
                '_plan': {'keep': [], 'retained': [], 'replace': [],
                          'delete': [], 'create': []}}
        qa = []
        graph2.apply_cluster(doc, bad, {}, {}, {}, qa)
        graph2.apply_cluster(doc, good, {}, {}, {}, qa)
    finally:
        graph2.create_wall = original
    check(bad['status'] == 'FAILED',
          'the failing cluster should be marked FAILED, got %r' % bad['status'])
    check('went missing' in bad.get('reason', ''),
          'the reason should say what failed, got %r' % bad.get('reason'))
    check(graph2.SubTransaction.calls == ['start', 'rollback', 'start', 'commit'],
          'the failing cluster should roll back and the next one commit, got %s'
          % graph2.SubTransaction.calls)
    check(good['status'] == 'APPLIED',
          'the successful cluster should be applied, got %r' % good['status'])
    check(deleted == [11], 'only the failing cluster should have deleted a wall, got %s'
          % deleted)


def test_stand_status_distinguishes_both_removed():
    """Baseline-only is not 'new in Main': both documents dropped the stand."""
    baseline = stand('A01', [(11, Curve(0, 0, 10, 0))], 1)
    main = stand('A01', [(11, Curve(0, 0, 10, 0))], 1)
    check(graph2.stand_status(baseline, None, None, 0.03, 0.25) == 'BOTH_REMOVED',
          'a stand gone from both documents should be BOTH_REMOVED')
    check(graph2.stand_status(baseline, main, None, 0.03, 0.25) == 'REMOVED',
          'a stand only Main dropped should stay REMOVED')
    check(graph2.stand_status(None, main, None, 0.03, 0.25) == 'MAIN_ONLY',
          'a stand new in Main should stay MAIN_ONLY')


def test_main_removed_stand_is_reported():
    """Main deleting a stand the Redraw keeps must reach the operator."""
    curve = Curve(0, 0, 10, 0)
    base = {'A01': stand('A01', [(11, curve)], 1)}
    redraw = {'A01': stand('A01', [(11, curve)], 1)}
    gplan, pplan, issues = plan(base, {}, redraw)
    check(not gplan, 'nothing can be merged for a stand Main removed, got %s' % gplan)
    reported = [i for i in issues if i.get('type') == 'MAIN REMOVED']
    check(len(reported) == 1 and reported[0]['stand'] == 'A01',
          'the removed stand should be reported once, got %s' % issues)
    check('Redraw' in reported[0]['reason'],
          'the reason should name the Redraw, got %r' % reported[0]['reason'])


def test_unchanged_attributes_are_already():
    """Identical geometry and identical wall attributes is nothing to do."""
    base = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
    main = dict(base)
    redraw = dict(base)
    check(graph2.stand_status(base['A'], main['A'], redraw['A'], 0.03, 0.25) == 'ALREADY',
          'a stand with the same geometry and attributes should be ALREADY')
    gplan, pplan, issues = plan(base, main, redraw)
    check(gplan == [], 'nothing should be planned, got %s' % gplan)


def test_wall_type_change_without_moving_is_planned():
    """A wall whose type changed but whose boundary did not move is still a
    change: the stand is AFFECTED and the wall is replaced. Reporting ALREADY
    would silently drop the designer's edit."""
    base = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
    main = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
    redraw = {'A': stand('A', [(11, Curve(0, 0, 10, 0))], 1)}
    redraw['A']['wall_loops'][0][0]['type'] = 'Open Wall - RX'
    check(graph2.stand_status(base['A'], main['A'], redraw['A'], 0.03, 0.25) == 'AFFECTED',
          'a wall type change without a move should be AFFECTED')
    gplan, pplan, issues = plan(base, main, redraw)
    check(len(gplan) == 1, 'one cluster should be planned, got %d' % len(gplan))
    check(gplan[0]['status'] == 'PLANNED', 'it should be planned, got %r'
          % gplan[0].get('status'))
    replaced = [wall['ref']['element_id'] for wall, _ in gplan[0]['_plan']['replace']]
    check(replaced == [11],
          'the wall with the new type should be replaced, got %s' % replaced)


def test_dry_run_keeps_a_skipped_cluster_visible():
    """A dry run must not relabel a blocked cluster as PLANNED."""
    shared = Curve(10, 0, 10, 10)
    base = {'A01': stand('A01', [(10, shared), (11, Curve(0, 0, 10, 0))], 1),
            'A02': stand('A02', [(10, shared), (12, Curve(20, 0, 20, 10))], 2)}
    main = dict(base)
    redraw = {'A01': stand('A01', [(11, Curve(0, 0, 10, 0))], 1),
              'A02': stand('A02', [(12, Curve(20, 0, 20, 10))], 2)}
    # A Room keeps its location point when its walls change; the fixture
    # derives it from the bounding box, so pin it to model a resize.
    for state in (base, redraw):
        state['A01']['room_point'] = Point(5.0, 5.0)
        state['A02']['room_point'] = Point(15.0, 5.0)
    gplan, pplan, issues = plan(base, main, redraw)
    check(gplan[0]['status'] == 'SKIPPED', 'the cluster should be skipped')
    reason = gplan[0]['reason']
    gout = graph2.public_plan(gplan)
    check(gout[0]['status'] == 'SKIPPED',
          'the dry run should keep the skipped status, got %r' % gout[0]['status'])
    check(gout[0].get('reason') == reason,
          'the dry run should keep the reason, got %r' % gout[0].get('reason'))
    check(not any(key.startswith('_') for key in gout[0]),
          'the private working keys should be gone, got %s' % sorted(gout[0]))


def test_only_skipped_clusters_report_warnings():
    """A run where everything was blocked is not a plain SUCCESS."""
    shared = Curve(10, 0, 10, 10)
    base = {'A01': stand('A01', [(10, shared), (11, Curve(0, 0, 10, 0))], 1),
            'A02': stand('A02', [(10, shared), (12, Curve(20, 0, 20, 10))], 2)}
    main = dict(base)
    redraw = {'A01': stand('A01', [(11, Curve(0, 0, 10, 0))], 1),
              'A02': stand('A02', [(12, Curve(20, 0, 20, 10))], 2)}
    for state in (base, redraw):
        state['A01']['room_point'] = Point(5.0, 5.0)
        state['A02']['room_point'] = Point(15.0, 5.0)
    gplan, pplan, issues = plan(base, main, redraw)
    gout = graph2.public_plan(gplan)
    status, message, failed, skipped, conflicts = graph2.judge_run([], gout, issues)
    check(status == 'SUCCESS WITH WARNINGS',
          'a run with only skipped clusters should warn, got %r' % status)
    check(failed == 0 and skipped == 1,
          'the counts should be failed=0 skipped=1, got failed=%d skipped=%d'
          % (failed, skipped))
    check(message, 'the summary should say why the run is not clean')


def test_a_cluster_names_what_it_does():
    """The audit counts real changes: a new stand is ADDED, a deleted one is
    REMOVED, a moved one is MOVED - never the old 'CLUSTER' placeholder."""
    a = Curve(0, 0, 10, 0)
    c_wall = Curve(40, 0, 50, 0)
    base = {'A': stand('A', [(11, a)], 1), 'C': stand('C', [(31, c_wall)], 3)}
    main = dict(base)
    added = dict(base, B=stand('B', [(21, Curve(20, 0, 30, 0))], 2))
    gplan, pplan, issues = plan(base, main, added)
    check(gplan and gplan[0]['change'] == 'ADDED',
          'a new stand should be ADDED, got %s' % [op.get('change') for op in gplan])
    gone = {'C': stand('C', [(31, c_wall)], 3)}
    gplan, pplan, issues = plan(base, main, gone)
    check(gplan and gplan[0]['change'] == 'REMOVED',
          'a deleted stand should be REMOVED, got %s' % [op.get('change') for op in gplan])
    moved_redraw = {'A': stand('A', [(11, Curve(0, 5, 10, 5))], 1),
                    'C': stand('C', [(31, c_wall)], 3)}
    gplan, pplan, issues = plan(base, main, moved_redraw)
    check(gplan and gplan[0]['change'] == 'MOVED',
          'a moved stand should be MOVED, got %s' % [op.get('change') for op in gplan])
    check(all(op.get('change') != 'CLUSTER' for op in gplan),
          'no cluster should carry the old placeholder, got %s'
          % [op.get('change') for op in gplan])


def test_cluster_change_mixes_stand_statuses():
    """A cluster that both adds and removes a stand is MIXED, and a conflict
    merged under the Redraw wins is a move."""
    statuses = {'A': 'ADDED', 'B': 'ADDED', 'C': 'REMOVED', 'D': 'AFFECTED',
                'E': 'CONFLICT'}
    check(graph2.cluster_change(statuses, ['A', 'B']) == 'ADDED',
          'two new stands should be ADDED')
    check(graph2.cluster_change(statuses, ['C']) == 'REMOVED',
          'a deleted stand should be REMOVED')
    check(graph2.cluster_change(statuses, ['A', 'C']) == 'MIXED',
          'an add and a remove should be MIXED')
    check(graph2.cluster_change(statuses, ['D', 'E']) == 'MOVED',
          'a moved stand and a conflict won by the Redraw should be MOVED')


def test_cluster_change_distinguishes_moved_from_resized():
    """A Room whose point stayed put was resized; one that moved was moved."""
    class P(object):
        def __init__(self, x, y):
            self.X = x
            self.Y = y
            self.Z = 0.0

    statuses = {'A': 'AFFECTED'}
    main = {'A': {'room_point': P(0.0, 0.0)}}
    same = {'A': {'room_point': P(0.0, 0.0)}}
    moved = {'A': {'room_point': P(1.0, 0.0)}}
    check(graph2.cluster_change(statuses, ['A'], main, same, 0.03) == 'RESIZED',
          'a fixed point with a changed boundary should be RESIZED, got %r'
          % graph2.cluster_change(statuses, ['A'], main, same, 0.03))
    check(graph2.cluster_change(statuses, ['A'], main, moved, 0.03) == 'MOVED',
          'a point that moved should be MOVED, got %r'
          % graph2.cluster_change(statuses, ['A'], main, moved, 0.03))
    check(graph2.cluster_change(statuses, ['A']) == 'MOVED',
          'without the states the change should stay MOVED')


def test_audit_rows_match_their_headers():
    """Both CSVs must be rectangular: a row shorter or longer than its header
    is a corrupt audit trail. The parameter log is part of the same file."""
    import csv
    import shutil
    import tempfile

    gout = [{'cluster': 'C1', 'stands': ['A01', 'A02'], 'change': 'MOVED',
             'status': 'VERIFIED', 'walls_replaced': 1, 'walls_created': 2,
             'walls_deleted': 3, 'params_written': 0, 'tags_moved': 4,
             'walls_kept': 5}]
    pout = [{'stand': 'A01', 'parameter': 'Product Code', 'main_before': 'X',
             'main_after': 'Y', 'redraw': 'Y', 'status': 'VERIFIED'}]
    rows = graph2.audit_rows(pout, gout)
    totals = graph2.audit_totals(pout, gout, [{'type': 'PARAMETER CONFLICT'}])
    check(len(rows) == 2,
          'one row per cluster and one per parameter, got %d' % len(rows))
    check(all(len(row) == len(graph2.AUDIT_LOG_COLUMNS) for row in rows),
          'every log row must fill the log header, got %s'
          % [len(row) for row in rows])
    check(len(totals) == len(graph2.AUDIT_SUMMARY_COLUMNS),
          'the totals must fill the summary header, got %d for %d columns'
          % (len(totals), len(graph2.AUDIT_SUMMARY_COLUMNS)))
    check(totals[0] == 1 and totals[1] == 0,
          'the merged and skipped counts are wrong, got %s' % totals)
    check(totals[2] == 1,
          'the moved count should come from the cluster change, got %s' % totals)
    check(totals[9] == 1,
          'params_written should count the parameter plan, got %s' % totals)
    check(totals[11] == 1, 'the conflict count is wrong, got %s' % totals)

    folder = tempfile.mkdtemp()
    try:
        ok, error = graph2.write_audit(folder, 'marker', rows, totals, 'APPLIED', 'run-1')
        check(ok and error is None, 'the audit write should succeed, got %r' % (error,))
        with open(os.path.join(folder, 'marker.merge-log.csv'),
                  newline='', encoding='utf-8') as handle:
            log = list(csv.reader(handle))
        with open(os.path.join(folder, 'marker.merge-summary.csv'),
                  newline='', encoding='utf-8') as handle:
            summary = list(csv.reader(handle))
    finally:
        shutil.rmtree(folder)
    check(all(len(row) == len(log[0]) for row in log),
          'every log row must match its header, got %s' % [len(row) for row in log])
    check(all(len(row) == len(summary[0]) for row in summary),
          'every summary row must match its header, got %s'
          % [len(row) for row in summary])
    check(log[0] == graph2.AUDIT_PREFIX + graph2.AUDIT_LOG_COLUMNS,
          'the log header should be the declared one, got %s' % log[0])
    check(summary[0] == graph2.AUDIT_PREFIX + graph2.AUDIT_SUMMARY_COLUMNS,
          'the summary header should be the declared one, got %s' % summary[0])
    cluster_row = log[1]
    check(cluster_row[3] == 'C1' and cluster_row[4] == 'A01, A02',
          'the cluster row should keep its cluster and stands, got %s' % cluster_row)
    param_row = log[2]
    check(param_row[3] == '' and param_row[4] == 'A01' and param_row[5] == 'PARAMETER',
          'a parameter row should have no cluster and name its stand, got %s' % param_row)
    check(param_row[12] == 'Product Code: X -> Y',
          'the parameter detail should show the change, got %r' % param_row[12])


def test_write_audit_reports_a_failure():
    """A failed audit must not look like a successful run."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix='.txt') as handle:
        ok, error = graph2.write_audit(handle.name, 'marker', [], [], 'APPLIED', 'run-1')
    check(ok is False and error, 'a failed audit must say why, got %r' % (ok,))


class FakeVector(object):
    """Only what move_room_to_redraw asks of the delta: its length."""

    def __init__(self, x, y, z=0.0):
        self.X = x
        self.Y = y
        self.Z = z

    def GetLength(self):
        return (self.X ** 2 + self.Y ** 2 + self.Z ** 2) ** 0.5


def test_blank_directory_is_rejected():
    """A blank Directory used to reach folder.strip() as None."""
    for blank in (None, '', '   '):
        try:
            graph2.check_inputs(blank, 0.01, 0.25)
        except Exception as ex:
            check('Select the Redraw directory' in str(ex),
                  'the message should ask for the directory, got %r' % str(ex))
        else:
            check(False, 'a blank directory should be rejected, got %r' % (blank,))


def test_spacing_must_be_positive():
    """sp=0 divides by zero in smp/resample while planning."""
    for spacing in (0.0, -0.25):
        try:
            graph2.check_inputs('/a/folder', 0.01, spacing)
        except Exception as ex:
            check('boundary sample spacing' in str(ex),
                  'the message should name the spacing, got %r' % str(ex))
        else:
            check(False, 'spacing %r should be rejected' % (spacing,))


def test_tolerance_must_not_be_negative():
    for tolerance in (-0.01, float('inf')):
        try:
            graph2.check_inputs('/a/folder', tolerance, 0.25)
        except Exception as ex:
            check('comparison tolerance' in str(ex),
                  'the message should name the tolerance, got %r' % str(ex))
        else:
            check(False, 'tolerance %r should be rejected' % (tolerance,))


def test_coarse_spacing_warns_but_is_not_fatal():
    """The sliders allow a spacing far above the tolerance; say so, don't fail."""
    folder, tol, spacing, warnings = graph2.check_inputs('/a/folder', 0.01, 1.0)
    check(folder == '/a/folder' and tol == 0.01 and spacing == 1.0,
          'the inputs should come back unchanged, got %r' % ((folder, tol, spacing),))
    check(len(warnings) == 1 and 'may be missed' in warnings[0],
          'a coarse spacing should warn once, got %s' % warnings)
    folder, tol, spacing, warnings = graph2.check_inputs('/a/folder', 0.01, 0.05)
    check(warnings == [], 'a spacing close to the tolerance should not warn, got %s' % warnings)
    folder, tol, spacing, warnings = graph2.check_inputs('/a/folder', 0.01, 0.25)
    check(warnings == [], 'the shipped default spacing should not warn, got %s' % warnings)


def test_refused_room_move_raises():
    """A move Revit refuses is not a move that happened."""
    class RefusingLocation(graph2.LocationPoint):
        def __init__(self, point):
            self.Point = point

        def Move(self, delta):
            return False

    class Room(object):
        def __init__(self, point):
            self.Location = RefusingLocation(point)

    original = graph2.XYZ
    graph2.XYZ = FakeVector
    try:
        try:
            graph2.move_room_to_redraw(Room(Point(0.0, 0.0)),
                                       {'room_point': Point(5.0, 5.0)})
        except Exception as ex:
            check('could not be moved' in str(ex),
                  'the message should say the move was refused, got %r' % str(ex))
        else:
            check(False, 'a refused move must raise')
    finally:
        graph2.XYZ = original



# --------------------------------------------------------------------------
# sold stands
# --------------------------------------------------------------------------
class StubParameter(object):
    def __init__(self, text):
        self.text = text
        self.HasValue = True

    def AsString(self):
        return self.text

    def AsValueString(self):
        return self.text


class StubRoom(object):
    """A Room with only the parameters a sold check reads."""

    def __init__(self, parameters=None, area=None):
        self.parameters = parameters or {}
        self.Area = area

    def LookupParameter(self, name):
        return self.parameters.get(name)


def square(number, width=3.0, height=3.0, x=0.0, y=0.0, element_id=1):
    """A rectangular stand, so its area and perimeter are easy to reason about."""
    walls = [(element_id, Curve(x, y, x + width, y)),
             (element_id + 1, Curve(x + width, y, x + width, y + height)),
             (element_id + 2, Curve(x + width, y + height, x, y + height)),
             (element_id + 3, Curve(x, y + height, x, y))]
    return stand(number, walls, element_id)


def as_sold(state, area, status='sold'):
    state['status'] = status
    state['area'] = area
    return state


def sold_plan(base, main, redraw, tol=0.001):
    gplan, pplan, issues = plan(base, main, redraw)
    blockers, warnings, checked = graph2.sold_check(main, redraw, gplan, tol)
    return gplan, blockers, warnings, checked


def test_status_normalises_and_ignores_other_states():
    for text, expected in [('Sold', 'sold'), ('SOLD', 'sold'), (' sold ', 'sold'),
                           ('On Hold', 'onhold'), ('OnHold', 'onhold'),
                           ('Available', 'available'), ('On_Hold', 'onhold')]:
        got = graph2.status_of(StubRoom({'Status': StubParameter(text)}))
        check(got == expected, 'status %r should normalise to %r, got %r'
              % (text, expected, got))
    check(graph2.status_of(StubRoom({})) is None,
          'a Room with no Status parameter should read as None, not as blank')


def test_status_present_tells_absent_from_blank():
    check(not graph2.status_present({'A': {'status': None}}),
          'no Status anywhere in Main should read as absent')
    check(not graph2.status_present({}), 'no stands at all is absent')
    check(graph2.status_present({'A': {'status': ''}}),
          'a blank value still means the parameter exists')
    check(graph2.status_present({'A': {'status': 'available'}}), 'available is present')


def test_sold_stand_area_change_blocks_the_run():
    base = {'S01': as_sold(square('S01'), 9.0)}
    main = {'S01': as_sold(square('S01'), 9.0)}
    redraw = {'S01': as_sold(square('S01', height=4.0), 12.0)}
    gplan, blockers, warnings, checked = sold_plan(base, main, redraw)
    check(gplan and gplan[0]['status'] == 'PLANNED',
          'the fixture should plan a change, got %r' % [op['status'] for op in gplan])
    check(len(blockers) == 1, 'a resize of a sold stand must block, got %r' % (blockers,))
    check(blockers and blockers[0][1] == 'S01', 'the blocker should name the stand')
    check(blockers and blockers[0][0] == gplan[0]['cluster'],
          'the blocker should name the cluster, so a dry run can mark it')
    check('S01' in checked, 'the stand should be recorded as checked')


def test_sold_stand_within_the_allowance_is_clean():
    base = {'S01': as_sold(square('S01'), 9.0)}
    main = {'S01': as_sold(square('S01'), 9.0)}
    redraw = {'S01': as_sold(square('S01', height=3.001), 9.003)}
    gplan, blockers, warnings, checked = sold_plan(base, main, redraw)
    check(not blockers, 'a move inside the allowance must not block, got %r' % (blockers,))
    check(not warnings, 'a move inside the allowance must not warn, got %r' % (warnings,))


def test_sold_stand_that_keeps_its_area_but_changes_shape_warns():
    base = {'S01': as_sold(square('S01'), 9.0)}
    main = {'S01': as_sold(square('S01'), 9.0)}
    # 4.5 x 2 is the same 9 sqft with a longer perimeter.
    redraw = {'S01': as_sold(square('S01', width=4.5, height=2.0), 9.0)}
    gplan, blockers, warnings, checked = sold_plan(base, main, redraw)
    check(not blockers, 'the same area must not block, got %r' % (blockers,))
    check(len(warnings) == 1, 'a shape change should warn, got %r' % (warnings,))
    check(warnings and 'perimeter' in warnings[0][2],
          'the warning should say the perimeter changed')


def test_an_unsold_stand_is_not_checked():
    base = {'A01': as_sold(square('A01'), 9.0, status='available')}
    main = {'A01': as_sold(square('A01'), 9.0, status='available')}
    redraw = {'A01': as_sold(square('A01', height=4.0), 12.0, status='available')}
    gplan, blockers, warnings, checked = sold_plan(base, main, redraw)
    check(not blockers and not warnings, 'an Available stand may be resized')
    check(not checked, 'an Available stand is not a checked stand')


def test_on_hold_is_ignored():
    base = {'H01': as_sold(square('H01'), 9.0, status='onhold')}
    main = {'H01': as_sold(square('H01'), 9.0, status='onhold')}
    redraw = {'H01': as_sold(square('H01', height=4.0), 12.0, status='onhold')}
    gplan, blockers, warnings, checked = sold_plan(base, main, redraw)
    check(not blockers and not warnings, 'OnHold does not block, by decision')


def test_a_sold_stand_in_a_skipped_cluster_is_not_checked():
    base = {'S01': as_sold(square('S01'), 9.0)}
    main = {'S01': as_sold(square('S01'), 9.0)}
    redraw = {'S01': as_sold(square('S01', height=4.0), 12.0)}
    gplan, pplan, issues = plan(base, main, redraw)
    for op in gplan:
        op['status'] = 'SKIPPED'
        op['reason'] = 'blocked for another reason'
    blockers, warnings, checked = graph2.sold_check(main, redraw, gplan, 0.001)
    check(not blockers, 'a cluster this run will not apply cannot resize anything')
    check(not checked, 'a stand in a skipped cluster is not checked')


def test_a_sold_stand_the_redraw_deletes_blocks():
    base = {'S01': as_sold(square('S01'), 9.0), 'B01': square('B01', x=20.0)}
    main = {'S01': as_sold(square('S01'), 9.0), 'B01': square('B01', x=20.0)}
    redraw = {'B01': square('B01', x=20.0)}
    gplan, blockers, warnings, checked = sold_plan(base, main, redraw)
    check(any(op.get('change') == 'REMOVED' for op in gplan),
          'the fixture should plan a deletion, got %r' % [op.get('change') for op in gplan])
    check(len(blockers) == 1, 'deleting a sold stand must block, got %r' % (blockers,))
    check(blockers and 'deletes a sold stand' in blockers[0][2],
          'the reason should say the stand is deleted')


def test_area_allowance_is_derived_from_the_perimeter():
    floor = graph2.area_allowance(0.0, 0.001)
    check(abs(floor - 0.0107639) < 0.0001,
          'the floor should be 0.001 m2 in square feet, got %r' % floor)
    big = graph2.area_allowance(30.0, 0.001)
    check(abs(big - 0.03) < 1e-9,
          'a long perimeter should set the allowance, got %r' % big)
    tighter = graph2.area_allowance(30.0, 0.0005)
    check(tighter < big, 'halving the tolerance should halve the allowance')


def test_the_refusal_names_the_stands_and_the_rule():
    blockers = [('c1', 'S01', 'sold stand area would change: 9.000 -> 12.000 sqft'),
                ('c1', 'S02', 'the Redraw deletes a sold stand')]
    message = graph2.sold_message(blockers)
    check('S01' in message and 'S02' in message, 'the message should name both stands')
    check('must not be resized' in message, 'the message should state the rule')
    check('CRM' in message, 'the message should say where a decision comes from')


def test_the_merge_is_checked_again_before_it_commits():
    after = StubRoom(area=12.0)
    state = as_sold(square('S01'), 9.0)
    state['room'] = after
    main = {'S01': state}
    redraw = {'S01': {'area': 9.0}}
    drift = graph2.merge_sold_drift(main, redraw, {'S01'})
    check(len(drift) == 1, 'an area that moved anyway must be caught, got %r' % (drift,))
    check(drift and drift[0][2].startswith('acknowledged area'),
          'the drift should quote both areas, got %r' % (drift,))
    after.Area = 9.0
    check(not graph2.merge_sold_drift(main, redraw, {'S01'}),
          'an area that matches the Redraw is clean')
    after.Area = None
    drift = graph2.merge_sold_drift(main, redraw, {'S01'})
    check(len(drift) == 1, 'a sold stand with no area is drift, got %r' % (drift,))


def test_sold_warnings_make_the_run_warn():
    issues = [{'stand': 'S01', 'type': 'SOLD STAND DIMENSIONS',
               'reason': 'sold stand keeps its area but changes shape'}]
    status, message, failed, skipped, conflicts = graph2.judge_run([], [], issues)
    check(status == 'SUCCESS WITH WARNINGS',
          'a sold-shape warning should not read as SUCCESS, got %r' % status)
    check('shape' in message, 'the summary should carry the reason, got %r' % message)

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
