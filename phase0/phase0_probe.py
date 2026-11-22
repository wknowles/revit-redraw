# -*- coding: utf-8 -*-
"""Phase 0 probe for the Redraw merge work.

Revit 2027 | Dynamo 4.0.2 | CPython3/PythonNet3

Answers the runtime questions that the Revit API documentation cannot answer:

A  Can a per-element view override recolour an existing wall in a floor plan?
B  Do DirectShape surfaces render in a floor plan, and does SetElementOverrides
   accept a DirectShape?
C  Is a Room's Number writable (LookupParameter('Number') or the built-in
   BuiltInParameter.ROOM_NUMBER)?
D  Are room boundary curves trimmed to the room, or are they the full wall
   curve?  This decides how baseline curves are matched against Main walls.
E  What does Document.Create.NewRoom do inside an existing room?

IN[0] Boolean  run the visible test.  False (default) runs diagnostics only and
               changes nothing.  True commits one transaction that draws a blue
               band and a green wall so the result can be looked at; press
               Ctrl+Z once afterwards to remove it.

OUT   List of log lines.

The probe picks a shared wall (one that bounds two or more rooms) so that
question D is answered on the case that actually matters.  Nothing is written
to the model unless IN[0] is True.
"""
import clr, re

clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, Color, CurveLoop, DirectShape, ElementId,
    Extrusion, FilteredElementCollector, GeometryCreationUtilities, GeometryObject,
    Line, OverrideGraphicSettings, SolidOptions, SpatialElementBoundaryOptions,
    SpatialElementBoundaryLocation, StorageType, Transaction, UV, Wall, XYZ)
from RevitServices.Persistence import DocumentManager

PROBE_MARK = 'PHASE0 PROBE'
BAND_WIDTH = 0.30          # internal feet
BAND_LIFT = 0.05           # internal feet


def _in(index, default=None):
    """Read an optional Dynamo input without exploding when it is absent."""
    try:
        value = IN[index]
    except Exception:
        return default
    return default if value is None else value


def eidv(element_id):
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def band_solid(curve, width, z, samples=12):
    """A closed rectangle around a curve, extruded slightly upward."""
    points = [curve.Evaluate(float(i) / samples, True) for i in range(samples + 1)]
    left = []
    right = []
    for i, p in enumerate(points):
        if i == 0:
            d = points[1] - points[0]
        elif i == len(points) - 1:
            d = points[-1] - points[-2]
        else:
            d = points[i + 1] - points[i - 1]
        d = XYZ(d.X, d.Y, 0.0)
        if d.Length < 1e-9:
            d = XYZ(1.0, 0.0, 0.0)
        d = d.Normalize()
        offset = XYZ(-d.Y, d.X, 0.0) * (width / 2.0)
        left.append(XYZ(p.X + offset.X, p.Y + offset.Y, z))
        right.append(XYZ(p.X - offset.X, p.Y - offset.Y, z))
    ring = left + right[::-1]
    loop = CurveLoop()
    for i in range(len(ring)):
        loop.Append(Line.CreateBound(ring[i], ring[(i + 1) % len(ring)]))
    path = Line.CreateBound(XYZ(0.0, 0.0, z), XYZ(0.0, 0.0, z + BAND_LIFT))
    try:
        return GeometryCreationUtilities.CreateExtrusionSolid(loop, path, BAND_LIFT)
    except Exception:
        return Extrusion.Create(loop, path, BAND_LIFT)


def make_direct_shape(doc, solid):
    """Create a DirectShape in Generic Annotations, whichever API is available."""
    category = ElementId(BuiltInCategory.OST_GenericAnnotations)
    shape = None
    try:
        shape = DirectShape.CreateElement(doc, category)
    except Exception:
        shape = None
    if shape is None:
        try:
            shape = doc.Create.NewDirectShape(category, [solid])
        except Exception:
            shape = None
    if shape is None:
        raise Exception('Could not create a DirectShape in Generic Annotations')
    try:
        shape.SetShape([solid])
    except Exception:
        from System import Array
        shape.SetShape(Array[GeometryObject]([solid]))
    return shape


def blue_style():
    style = OverrideGraphicSettings()
    style.SetSurfaceForegroundPatternColor(Color(0, 120, 255))
    style.SetSurfaceForegroundPatternVisible(True)
    style.SetCutForegroundPatternColor(Color(0, 120, 255))
    style.SetCutForegroundPatternVisible(True)
    style.SetProjectionLineColor(Color(0, 120, 255))
    return style


def green_style(view, wall):
    style = OverrideGraphicSettings(view.GetCategoryOverrides(wall.Category.Id))
    style.SetCutForegroundPatternColor(Color(0, 190, 0))
    style.SetCutLineColor(Color(0, 190, 0))
    style.SetProjectionLineColor(Color(0, 190, 0))
    return style


def clear_previous_probe(doc, view):
    """Remove DirectShapes left by an earlier visible test and reset their wall override."""
    removed = 0
    try:
        notes = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_GenericAnnotations) \
            .WhereElementIsNotElementType().ToElements()
    except Exception:
        return 0
    for element in notes:
        try:
            parameter = element.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if parameter is None or parameter.StorageType != StorageType.String:
                continue
            text = parameter.AsString() or ''
            if not text.startswith(PROBE_MARK):
                continue
            match = re.search(r'wall=(-?\d+)', text)
            if match:
                try:
                    previous = doc.GetElement(ElementId(int(match.group(1))))
                    if previous is not None:
                        view.SetElementOverrides(previous.Id,
                                                 view.GetCategoryOverrides(previous.Category.Id))
                except Exception:
                    pass
            doc.Delete(element.Id)
            removed += 1
        except Exception:
            pass
    return removed


def run():
    doc = DocumentManager.Instance.CurrentDBDocument
    if doc is None:
        log('FATAL: no active Revit document. Open a project model first.')
        return
    view = doc.ActiveView
    log('Revit %s | active view "%s" type=%s' % (doc.Application.VersionNumber, view.Name, view.ViewType))
    try:
        log('A1 AreGraphicsOverridesAllowed: %s' % view.AreGraphicsOverridesAllowed())
    except Exception as ex:
        log('A1 AreGraphicsOverridesAllowed FAILED: %s' % ex)

    options = SpatialElementBoundaryOptions()
    options.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Center

    rooms = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms)
                 .WhereElementIsNotElementType().ToElements())
    triples = []
    for room in rooms:
        try:
            if not room.IsPlaced:
                continue
            segments = room.GetBoundarySegments(options)
        except Exception:
            continue
        if not segments:
            continue
        for loop in segments:
            for segment in loop:
                try:
                    element = doc.GetElement(segment.ElementId)
                except Exception:
                    continue
                if isinstance(element, Wall):
                    triples.append((room, segment, element))
    log('E0 rooms=%d  wall boundary segments=%d' % (len(rooms), len(triples)))
    if not triples:
        log('FATAL: no placed room with a wall boundary was found.')
        return

    usage = {}
    for room, segment, wall in triples:
        key = eidv(wall.Id)
        usage[key] = usage.get(key, 0) + 1
    shared = set([key for key, count in usage.items() if count > 1])
    log('E1 walls bounding more than one room: %d of %d' % (len(shared), len(usage)))

    # D: full wall curve or trimmed to the room?
    candidates = [t for t in triples if eidv(t[2].Id) in shared]
    if not candidates:
        log('D  (no shared wall in this model - results below are not conclusive)')
        candidates = triples
    trimmed = False
    for room, segment, wall in candidates[:4]:
        try:
            segment_length = segment.GetCurve().Length
            wall_length = wall.Location.Curve.Length
            shorter = segment_length < wall_length - 1e-6
            trimmed = trimmed or shorter
            log('D  room %-8s segment len=%9.4f  wall len=%9.4f  %s'
                % (room.Number or '?', segment_length, wall_length,
                   'TRIMMED' if shorter else 'FULL WALL'))
        except Exception as ex:
            log('D  curve comparison failed: %s' % ex)
    log('D  => boundary curves are %s' % ('TRIMMED per room' if trimmed else 'FULL WALL curves'))

    focus = candidates[0]
    room = focus[0]
    wall = focus[2]
    try:
        wall_type = doc.GetElement(wall.GetTypeId())
        wall_type_name = wall_type.Name if wall_type else '?'
    except Exception:
        wall_type_name = '?'
    log('E2 focus: room %r (%s), wall %s, wall type %s'
        % (room.Number, room.Name, wall.Id, wall_type_name))

    z = 0.0
    try:
        z = room.Level.Elevation + 0.02
    except Exception:
        pass

    # ---- diagnostics, all rolled back -------------------------------------
    transaction = Transaction(doc, 'Phase 0 probe - diagnostics')
    transaction.Start()
    try:
        level = doc.GetElement(room.LevelId)
        try:
            anchor = room.Location.Point
        except Exception:
            anchor = None
        attempts = []
        if anchor is not None:
            attempts.append(('inside an existing room', anchor))
            attempts.append(('offset by +1000 ft', XYZ(anchor.X + 1000.0, anchor.Y + 1000.0, 0.0)))
        for label, point in attempts:
            try:
                # NewRoom takes a UV position on the level, not an XYZ.
                probe_room = doc.Create.NewRoom(level, UV(point.X, point.Y))
            except Exception as ex:
                log('E  NewRoom (%s) FAILED: %s' % (label, ex))
                continue
            try:
                has_boundary = probe_room.GetBoundarySegments(options) is not None
            except Exception:
                has_boundary = None
            try:
                is_placed = probe_room.IsPlaced
            except Exception:
                is_placed = None
            log('E  NewRoom (%s): placed=%s hasBoundary=%s number=%r'
                % (label, is_placed, has_boundary, probe_room.Number))
            parameter = None
            try:
                parameter = probe_room.LookupParameter('Number')
            except Exception:
                pass
            log('C  LookupParameter("Number"): found=%s readonly=%s'
                % (parameter is not None, parameter.IsReadOnly if parameter is not None else None))
            if parameter is not None:
                try:
                    parameter.Set('PHASE0-999')
                    log('C  Set("PHASE0-999") -> Number=%r  => WRITABLE' % probe_room.Number)
                except Exception as ex:
                    log('C  Set failed: %s' % ex)
            try:
                builtin = probe_room.get_Parameter(BuiltInParameter.ROOM_NUMBER)
                log('C  built-in ROOM_NUMBER: found=%s readonly=%s'
                    % (builtin is not None, builtin.IsReadOnly if builtin is not None else None))
                if builtin is not None and not builtin.IsReadOnly:
                    try:
                        builtin.Set('PHASE0-998')
                        log('C  built-in Set -> Number=%r  => WRITABLE' % probe_room.Number)
                    except Exception as ex:
                        log('C  built-in Set failed: %s' % ex)
            except Exception as ex:
                log('C  built-in ROOM_NUMBER lookup failed: %s' % ex)
            try:
                doc.Delete(probe_room.Id)
            except Exception:
                pass
    except Exception as ex:
        log('C/E diagnostics failed: %s' % ex)

    try:
        solid = band_solid(wall.Location.Curve, BAND_WIDTH, z)
        log('B1 band solid built ok')
    except Exception as ex:
        solid = None
        log('B1 band solid FAILED: %s' % ex)
    if solid is not None:
        try:
            shape = make_direct_shape(doc, solid)
            log('B1 DirectShape created ok -> %s' % shape.Id)
        except Exception as ex:
            log('B1 DirectShape creation FAILED: %s' % ex)
        try:
            view.SetElementOverrides(shape.Id, blue_style())
            log('B1 SetElementOverrides(DirectShape) ok')
        except Exception as ex:
            log('B1 SetElementOverrides on DirectShape FAILED: %s' % ex)
        try:
            log('A2 GetCategoryOverrides ok -> %s'
                % (view.GetCategoryOverrides(wall.Category.Id) is not None))
        except Exception as ex:
            log('A2 GetCategoryOverrides FAILED: %s' % ex)
    try:
        transaction.RollBack()
    except Exception:
        pass
    log('C/E/A2/B1 diagnostics ROLLED BACK - the model is unchanged')

    # ---- optional visible test, committed ---------------------------------
    if not bool(_in(0, False)):
        log('A2/B2 visible test skipped. Set "Run Visual Test" to true to see it.')
        return
    removed = clear_previous_probe(doc, view)
    if removed:
        log('VISUAL cleanup: removed %d probe element(s) from an earlier run' % removed)
    transaction = Transaction(doc, 'Phase 0 probe - visual test')
    transaction.Start()
    try:
        solid = band_solid(wall.Location.Curve, BAND_WIDTH, z)
        shape = make_direct_shape(doc, solid)
        view.SetElementOverrides(shape.Id, blue_style())
        view.SetElementOverrides(wall.Id, green_style(view, wall))
        try:
            comment = shape.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if comment is not None and comment.StorageType == StorageType.String:
                comment.Set('%s wall=%d' % (PROBE_MARK, eidv(wall.Id)))
        except Exception:
            pass
        transaction.Commit()
        log('VISUAL committed: blue band on wall %s, wall coloured green.' % wall.Id)
        log('>>> Look at the plan, then press Ctrl+Z once to remove the probe.')
    except Exception as ex:
        try:
            transaction.RollBack()
        except Exception:
            pass
        log('VISUAL FAILED: %s' % ex)


LOG = []


def log(message):
    try:
        LOG.append(str(message))
    except Exception:
        pass


try:
    run()
except Exception as ex:
    LOG.append('PROBE CRASHED: %s' % ex)

OUT = LOG
