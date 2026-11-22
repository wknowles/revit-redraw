# -*- coding: utf-8 -*-
"""Offline harness for phase0_probe.py.

The probe cannot run without Revit, so this stubs the parts of the Revit API it
touches and drives the probe through three scenarios:

  walls     rooms bounded by real walls, two of them sharing one wall
  nonwalls  rooms bounded by something that is not a Wall (Room Separation
            Lines, or any other boundary element)
  empty     a model with no rooms at all

It cannot tell us whether an API call is correct against Revit, only whether the
probe's own control flow survives the situations we expect. Run it after every
edit to phase0_probe.py:

    python3 phase0/test_probe.py
"""
import math
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


# --------------------------------------------------------------------------
# fake Revit API
# --------------------------------------------------------------------------
class XYZ(object):
    """RevitAPI's XYZ: no Length property, no arithmetic operators.

    Deliberately strict - the first version of this mock had Length and the
    operators, which hid two real bugs in the probe.
    """

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.X = float(x)
        self.Y = float(y)
        self.Z = float(z)

    def GetLength(self):
        return math.sqrt(self.X ** 2 + self.Y ** 2 + self.Z ** 2)

    def Normalize(self):
        length = self.GetLength() or 1.0
        return XYZ(self.X / length, self.Y / length, self.Z / length)


class UV(object):
    def __init__(self, u=0.0, v=0.0):
        self.U = float(u)
        self.V = float(v)


class FakeCurve(object):
    def __init__(self, a, b):
        self._a = a
        self._b = b

    @property
    def Length(self):
        return math.sqrt((self._a.X - self._b.X) ** 2 + (self._a.Y - self._b.Y) ** 2
                         + (self._a.Z - self._b.Z) ** 2)

    def GetEndPoint(self, index):
        return self._a if index == 0 else self._b

    def Evaluate(self, t, normalized=False):
        return XYZ(self._a.X + (self._b.X - self._a.X) * t,
                   self._a.Y + (self._b.Y - self._a.Y) * t,
                   self._a.Z + (self._b.Z - self._a.Z) * t)


class Line(object):
    @staticmethod
    def CreateBound(a, b):
        return FakeCurve(a, b)


class CurveLoop(object):
    def __init__(self):
        self.segments = []

    def Append(self, curve):
        self.segments.append(curve)


class GeometryCreationUtilities(object):
    @staticmethod
    def CreateExtrusionGeometry(profiles, direction, height):
        if not profiles:
            raise Exception('no profile')
        return object()

    @staticmethod
    def CreateExtrusionSolid(loop, path, height):
        raise NotImplementedError('not a Revit 2027 API - the probe must not use it')


class Extrusion(object):
    @staticmethod
    def Create(loop, path, length):
        return object()


class ElementId(object):
    def __init__(self, value=-1):
        if isinstance(value, str):
            value = abs(hash(value)) % 100000
        self.Value = int(value)

    def __eq__(self, other):
        return isinstance(other, ElementId) and other.Value == self.Value

    def __hash__(self):
        return hash(self.Value)

    def __repr__(self):
        return 'ElementId(%d)' % self.Value


class Category(object):
    def __init__(self, name):
        self.Name = name
        self.Id = ElementId(abs(hash(name)) % 100000)


class Element(object):
    def __init__(self, element_id, category_name, type_name):
        self.Id = ElementId(element_id)
        self.Category = Category(category_name)
        self._type_name = type_name
        self._parameters = {}

    def GetType(self):
        return type(self._type_name, (object,), {})

    def get_Parameter(self, built_in):
        return self._parameters.get(built_in)


class Wall(Element):
    def __init__(self, element_id, length=10.0, category_name='Walls'):
        Element.__init__(self, element_id, category_name, 'Wall')
        self._length = length
        self.Location = FakeLocationCurve(length)

    def GetTypeId(self):
        return ElementId(9000)


class FakeLocationCurve(object):
    def __init__(self, length):
        self.Curve = FakeCurve(XYZ(0, 0, 0), XYZ(length, 0, 0))


class RoomSeparationLine(Element):
    def __init__(self, element_id):
        Element.__init__(self, element_id, 'Room Separation Lines', 'RoomSeparationLine')


class FakeParameter(object):
    def __init__(self, owner=None, readonly=False, value=None):
        self.IsReadOnly = readonly
        self.StorageType = 'String'
        self._value = value
        self._owner = owner

    def AsString(self):
        return self._value

    def Set(self, value):
        if self.IsReadOnly:
            raise Exception('parameter is read-only')
        self._value = value
        if self._owner is not None:
            self._owner.Number = value
        return True


class LocationPoint(object):
    def __init__(self, point):
        self.Point = point


class Level(Element):
    def __init__(self, elevation=0.0):
        Element.__init__(self, 1, 'Levels', 'Level')
        self.Elevation = elevation


class FakeRoom(Element):
    """A Room as Revit 2027 exposes it: no IsPlaced, writable Number."""

    def __init__(self, element_id, number, placed=True, boundary_elements=None,
                 unbounded=False, has_location=True):
        Element.__init__(self, element_id, 'Rooms', 'Room')
        self.Number = number
        self.Name = 'Room ' + str(number)
        self.LevelId = ElementId(1)
        self.Level = Level(0.0)
        self.Location = LocationPoint(XYZ(10.0 * element_id, 5.0, 0.0)) if has_location else None
        self._boundary_elements = boundary_elements or []
        self._unbounded = unbounded
        self.number_parameter = FakeParameter(owner=self, readonly=False, value=number)

    def GetBoundarySegments(self, options):
        if self._unbounded:
            return None
        if not self._boundary_elements:
            return None
        segments = []
        for element in self._boundary_elements:
            segment = FakeSegment(element)
            segments.append([segment])
        return segments

    def LookupParameter(self, name):
        if name == 'Number':
            return self.number_parameter
        return None


class FakeSegment(object):
    def __init__(self, element):
        self.ElementId = element.Id
        self._element = element

    def GetCurve(self):
        curve = getattr(self._element.Location, 'Curve', None)
        if curve is not None:
            return curve
        return FakeCurve(XYZ(0, 0, 0), XYZ(5.0, 0, 0))


class FakeApplication(object):
    VersionNumber = '2027'


class FakeView(object):
    def __init__(self):
        self.Name = 'Level 1'
        self.ViewType = 'FloorPlan'
        self.overrides = {}

    def AreGraphicsOverridesAllowed(self):
        return True

    def GetCategoryOverrides(self, category_id):
        return OverrideGraphicSettings()

    def SetElementOverrides(self, element_id, settings):
        self.overrides[element_id.Value] = settings


class FakeDocument(object):
    def __init__(self, rooms):
        self.Application = FakeApplication()
        self.ActiveView = FakeView()
        self._rooms = rooms
        self._elements = {}
        self.Create = FakeCreation(self)

    def GetElement(self, element_id):
        return self._elements.get(getattr(element_id, 'Value', element_id))

    def Delete(self, element_id):
        self._elements.pop(getattr(element_id, 'Value', element_id), None)


class FakeCreation(object):
    def __init__(self, document):
        self._document = document

    def NewRoom(self, level, uv):
        room = FakeRoom(900, 'NEW-1', boundary_elements=[])
        self._document._elements[room.Id.Value] = room
        return room

    def NewDirectShape(self, category_id, shapes):
        shape = DirectShape(self._document, category_id)
        shape.SetShape(shapes)
        self._document._elements[shape.Id.Value] = shape
        return shape


class FilteredElementCollector(object):
    def __init__(self, document):
        self._rooms = document._rooms

    def OfCategory(self, category):
        return self

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        return list(self._rooms)


class Transaction(object):
    def __init__(self, document, name):
        self.document = document
        self.name = name
        self.committed = False

    def Start(self):
        pass

    def Commit(self):
        self.committed = True

    def RollBack(self):
        self.committed = False


class OverrideGraphicSettings(object):
    def __init__(self, other=None):
        self.calls = []

    def _record(self, name):
        self.calls.append(name)

    def SetSurfaceForegroundPatternColor(self, value):
        self._record('surface colour')

    def SetSurfaceForegroundPatternVisible(self, value):
        self._record('surface visible')

    def SetCutForegroundPatternColor(self, value):
        self._record('cut colour')

    def SetCutForegroundPatternVisible(self, value):
        self._record('cut visible')

    def SetCutLineColor(self, value):
        self._record('cut line colour')

    def SetProjectionLineColor(self, value):
        self._record('projection colour')


class DirectShape(object):
    next_id = 5000

    def __init__(self, document, category_id):
        DirectShape.next_id += 1
        self.Id = ElementId(DirectShape.next_id)
        self._parameters = {}

    @staticmethod
    def CreateElement(document, category_id):
        return DirectShape(document, category_id)

    def SetShape(self, shapes):
        self.shapes = shapes

    def get_Parameter(self, built_in):
        return self._parameters.get(built_in)


class BuiltInCategory(object):
    OST_Rooms = 'OST_Rooms'
    OST_GenericAnnotations = 'OST_GenericAnnotations'


class BuiltInParameter(object):
    ROOM_NUMBER = 'ROOM_NUMBER'
    ALL_MODEL_INSTANCE_COMMENTS = 'COMMENTS'


class StorageType(object):
    String = 'String'


class SpatialElementBoundaryLocation(object):
    Center = 'Center'
    Finish = 'Finish'
    CoreCenter = 'CoreCenter'
    CoreBoundary = 'CoreBoundary'


class SpatialElementBoundaryOptions(object):
    def __init__(self):
        self.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish


class Color(object):
    def __init__(self, r=0, g=0, b=0):
        self.r, self.g, self.b = r, g, b


class GeometryObject(object):
    pass


def install_stubs(document):
    """Put the fake Revit API in sys.modules and return the fake document."""
    revit = types.ModuleType('Autodesk.Revit.DB')
    for name in ('XYZ', 'UV', 'Line', 'CurveLoop', 'GeometryCreationUtilities', 'Extrusion',
                 'ElementId', 'Wall', 'Element', 'Category', 'BuiltInCategory', 'BuiltInParameter',
                 'StorageType', 'SpatialElementBoundaryLocation', 'SpatialElementBoundaryOptions',
                 'Color', 'GeometryObject', 'Transaction', 'OverrideGraphicSettings',
                 'DirectShape', 'FilteredElementCollector', 'RoomSeparationLine', 'LocationPoint'):
        setattr(revit, name, globals()[name])

    clr = types.ModuleType('clr')
    clr.AddReference = lambda *args, **kwargs: None

    persistence = types.ModuleType('RevitServices.Persistence')

    class DocumentManager(object):
        class Instance(object):
            CurrentDBDocument = document

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


def build_document(scenario):
    """Build a fake document for one scenario."""
    if scenario == 'empty':
        return FakeDocument([])

    if scenario == 'unplaced':
        # A room with no location point and no boundary - the fallback path.
        orphan = FakeRoom(1, 'A01', has_location=False)
        placed = FakeRoom(2, 'A02', boundary_elements=[])
        document = FakeDocument([orphan, placed])
        for room in document._rooms:
            document._elements[room.Id.Value] = room
        return document

    if scenario == 'walls':
        shared = Wall(101, 20.0)
        left = Wall(102, 10.0)
        top = Wall(103, 10.0)
        bottom = Wall(104, 10.0)
        right = Wall(105, 10.0)
        room_a = FakeRoom(1, '101', boundary_elements=[shared, left, top, bottom])
        room_b = FakeRoom(2, '102', boundary_elements=[shared, right, top, bottom])
        room_c = FakeRoom(3, '103', boundary_elements=[left, top])
        rooms = [room_a, room_b, room_c]
    else:  # nonwalls
        line_a = RoomSeparationLine(201)
        line_b = RoomSeparationLine(202)
        room_a = FakeRoom(1, '101', boundary_elements=[line_a, line_b])
        room_b = FakeRoom(2, '102', boundary_elements=[line_a, line_b])
        rooms = [room_a, room_b]

    document = FakeDocument(rooms)
    for room in rooms:
        document._elements[room.Id.Value] = room
        for element in room._boundary_elements:
            document._elements[element.Id.Value] = element
    return document


def run_scenario(scenario, visual):
    document = build_document(scenario)
    install_stubs(document)
    sys.modules.pop('phase0_probe', None)

    # Dynamo injects IN into the script globals before it runs. Putting it in
    # builtins makes the name resolvable when the module executes on import.
    import builtins
    builtins.IN = [visual]

    try:
        import phase0_probe
    except Exception as ex:
        return ['PROBE CRASHED ON IMPORT: %s' % ex]
    return list(phase0_probe.LOG)


def main():
    failures = 0
    for scenario in ('walls', 'nonwalls', 'unplaced', 'empty'):
        for visual in (False, True):
            log = run_scenario(scenario, visual)
            crashed = any(line.startswith('PROBE CRASHED') for line in log)
            label = '%s / visual=%s' % (scenario, visual)
            if crashed:
                failures += 1
                print('FAIL %s' % label)
                for line in log:
                    print('     ' + line)
            else:
                print('ok   %s  (%d log lines)' % (label, len(log)))
                if scenario == 'walls' and not visual:
                    for line in log:
                        print('     ' + line)
                if scenario == 'nonwalls' and not visual:
                    for line in log:
                        print('     ' + line)
    print('\n%s' % ('FAILED' if failures else 'all scenarios passed'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
