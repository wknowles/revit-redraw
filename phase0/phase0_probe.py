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
import clr, math, re

clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, Color, CurveLoop, DirectShape, ElementId,
    FilteredElementCollector, GeometryCreationUtilities, GeometryObject,
    Line, LocationPoint, OverrideGraphicSettings, SpatialElementBoundaryOptions,
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
    # Revit 2024+ has ElementId.Value; the old IntegerValue is gone.
    return int(element_id.Value)


def curves_match(first, second, tol=1e-6):
    """True when two curves have the same endpoints, in either direction."""
    try:
        a0, a1 = first.GetEndPoint(0), first.GetEndPoint(1)
        b0, b1 = second.GetEndPoint(0), second.GetEndPoint(1)
    except Exception:
        return None

    def same(p, q):
        return (abs(p.X - q.X) <= tol and abs(p.Y - q.Y) <= tol
                and abs(p.Z - q.Z) <= tol)

    return ((same(a0, b0) and same(a1, b1)) or (same(a0, b1) and same(a1, b0)))


def band_solid(curve, width, z, samples=12):
    """A closed rectangle around a curve, extruded slightly upward.

    All the arithmetic is plain Python on floats. RevitAPI's XYZ has no
    Length property (use GetLength()) and its operators are not dependable
    from PythonNet, so nothing here touches them.
    """
    points = [curve.Evaluate(float(i) / samples, True) for i in range(samples + 1)]
    half = width / 2.0
    left = []
    right = []
    for i, p in enumerate(points):
        if i == 0:
            ax, ay = points[1].X, points[1].Y
            bx, by = points[0].X, points[0].Y
        elif i == len(points) - 1:
            ax, ay = points[-1].X, points[-1].Y
            bx, by = points[-2].X, points[-2].Y
        else:
            ax, ay = points[i + 1].X, points[i + 1].Y
            bx, by = points[i - 1].X, points[i - 1].Y
        dx = ax - bx
        dy = ay - by
        length = math.sqrt(dx * dx + dy * dy)
        if length < 1e-9:
            dx, dy = 1.0, 0.0
        else:
            dx, dy = dx / length, dy / length
        offset_x = -dy * half
        offset_y = dx * half
        left.append(XYZ(p.X + offset_x, p.Y + offset_y, z))
        right.append(XYZ(p.X - offset_x, p.Y - offset_y, z))
    ring = left + right[::-1]
    loop = CurveLoop()
    for i in range(len(ring)):
        loop.Append(Line.CreateBound(ring[i], ring[(i + 1) % len(ring)]))
    # Revit 2027 has no Extrusion.Create; CreateExtrusionGeometry is the API.
    return GeometryCreationUtilities.CreateExtrusionGeometry([loop], XYZ(0.0, 0.0, 1.0), BAND_LIFT)


def make_direct_shape(doc, solid):
    """Create a DirectShape in Generic Annotations, whichever API is available."""
    category = ElementId(BuiltInCategory.OST_GenericAnnotation)
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
        notes = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_GenericAnnotation) \
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


def room_placement(room):
    """Describe how a room is placed.

    Revit 2027 has no Room.IsPlaced - SpatialElement exposes Area, Level,
    LevelId, Location and Number only. A placed room has a LocationPoint,
    which is the check graph 1 uses.
    """
    try:
        return 'located=%s' % isinstance(room.Location, LocationPoint)
    except Exception:
        return 'located=?'


def boundary_scan(doc, rooms, options, location_name):
    """Inventory the boundary of every room at one boundary location.

    Returns (per_room, tally, errors) where per_room holds
    (number, status, [element descriptions]) for each room.
    """
    per_room = []
    tally = {}
    errors = []
    for room in rooms:
        number = room.Number or '?'
        placement = room_placement(room)
        try:
            segments = room.GetBoundarySegments(options)
        except Exception as ex:
            errors.append('%s: %s' % (number, ex))
            per_room.append((number, '%s, GetBoundarySegments FAILED' % placement, []))
            continue
        if not segments:
            per_room.append((number, '%s, NO BOUNDARY' % placement, []))
            continue
        descriptions = []
        for loop in segments:
            for segment in loop:
                try:
                    element = doc.GetElement(segment.ElementId)
                except Exception:
                    element = None
                if element is None:
                    description = '<null element>'
                else:
                    try:
                        category = element.Category
                        description = category.Name if category is not None else '?'
                    except Exception:
                        description = '?'
                    try:
                        description += ' [%s]' % element.GetType().Name
                    except Exception:
                        pass
                    if isinstance(element, Wall):
                        description += ' WALL'
                descriptions.append(description)
                tally[description] = tally.get(description, 0) + 1
        per_room.append((number, '%s, OK' % placement, descriptions))
    return per_room, tally, errors


def report_scan(location_name, per_room, tally, errors):
    log('--- %s ---' % location_name)
    for number, status, descriptions in per_room[:12]:
        if descriptions:
            counts = {}
            for description in descriptions:
                counts[description] = counts.get(description, 0) + 1
            summary = ', '.join(['%s x%d' % (k, v) for k, v in sorted(counts.items())])
            log('  room %-8s %s  (%d segments: %s)' % (number, status, len(descriptions), summary))
        else:
            log('  room %-8s %s' % (number, status))
    if len(per_room) > 12:
        log('  ... and %d more rooms' % (len(per_room) - 12))
    if tally:
        summary = ', '.join(['%s x%d' % (k, v) for k, v in sorted(tally.items())])
        log('  TOTAL boundary elements: %s' % summary)
    else:
        log('  TOTAL boundary elements: none')
    for message in errors[:6]:
        log('  ERROR %s' % message)
    if len(errors) > 6:
        log('  ... and %d more errors' % (len(errors) - 6))


def run():
    doc = DocumentManager.Instance.CurrentDBDocument
    if doc is None:
        log('FATAL: no active Revit document. Open a project model first.')
        return
    view = doc.ActiveView
    log('Revit %s | active view "%s" type=%s' % (doc.Application.VersionNumber, view.Name, view.ViewType))
    log('This probe is standalone: it needs only an open model with rooms. No Redraw is required.')
    try:
        log('A1 AreGraphicsOverridesAllowed: %s' % view.AreGraphicsOverridesAllowed())
    except Exception as ex:
        log('A1 AreGraphicsOverridesAllowed FAILED: %s' % ex)

    rooms = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms)
                 .WhereElementIsNotElementType().ToElements())
    log('E0 rooms in model: %d' % len(rooms))
    if not rooms:
        log('FATAL: the model contains no Rooms.')
        return

    # Inventory the boundaries at both locations. Center is what the Redraw
    # contract uses; Finish is the fallback, and Room Separation Lines only
    # support Finish.
    scans = {}
    options = SpatialElementBoundaryOptions()
    options.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Center
    for location in (SpatialElementBoundaryLocation.Center, SpatialElementBoundaryLocation.Finish):
        scan_options = SpatialElementBoundaryOptions()
        scan_options.SpatialElementBoundaryLocation = location
        name = 'Center' if location == SpatialElementBoundaryLocation.Center else 'Finish'
        scans[name] = boundary_scan(doc, rooms, scan_options, name)

    log('=== boundary inventory ===')
    for name in ('Center', 'Finish'):
        report_scan(name, *scans[name])

    # Pick the location that produced wall-bounded rooms; prefer Center.
    chosen = None
    for name in ('Center', 'Finish'):
        per_room, tally, errors = scans[name]
        if any('WALL' in description for description in tally):
            chosen = name
            break
    if chosen is None:
        for name in ('Center', 'Finish'):
            per_room, tally, errors = scans[name]
            if tally:
                chosen = name
                break

    triples = []
    if chosen is not None:
        log('=== selected ===')
        log('boundary location used: %s' % chosen)
        options = SpatialElementBoundaryOptions()
        options.SpatialElementBoundaryLocation = (SpatialElementBoundaryLocation.Center
                                                  if chosen == 'Center'
                                                  else SpatialElementBoundaryLocation.Finish)
        for room in rooms:
            try:
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
    if triples:
        log('wall-bounded rooms found: %d'
            % len(set([t[0].Id.Value for t in triples])))
    else:
        log('NO wall-bounded rooms. The boundary is not made of Walls - see the inventory above.')
        log('  (Room Separation Lines support only the Finish location, and are not supported by the merge.)')

    usage = {}
    for room, segment, wall in triples:
        key = eidv(wall.Id)
        usage[key] = usage.get(key, 0) + 1
    shared = set([key for key, count in usage.items() if count > 1])
    log('E1 walls bounding more than one room: %d of %d' % (len(shared), len(usage)))

    # D: full wall curve or trimmed to the room?
    wall = None
    room = None
    if triples:
        candidates = [t for t in triples if eidv(t[2].Id) in shared]
        if not candidates:
            log('D  (no shared wall in this model - results below are not conclusive)')
            candidates = triples
        trimmed = False
        for probe_room, segment, probe_wall in candidates[:4]:
            try:
                segment_curve = segment.GetCurve()
                wall_curve = probe_wall.Location.Curve
                segment_length = segment_curve.Length
                wall_length = wall_curve.Length
                match = curves_match(segment_curve, wall_curve)
                if match is False:
                    trimmed = True
                verdict = 'TRIMMED' if (segment_length < wall_length - 1e-6 or match is False) else 'FULL WALL'
                log('D  room %-8s segment len=%9.4f  wall len=%9.4f  endpoints=%s  %s'
                    % (probe_room.Number or '?', segment_length, wall_length,
                       'MATCH' if match else ('DIFFER' if match is False else '?'), verdict))
            except Exception as ex:
                log('D  curve comparison failed: %s' % ex)
        log('D  => boundary curves are %s'
            % ('TRIMMED per room' if trimmed else 'FULL WALL curves'))
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
    else:
        log('D  SKIPPED - no wall-bounded room to compare against.')
        log('E2 SKIPPED - no wall to focus on.')

    # C and E need a room with a location point, but not a wall boundary.
    if room is None:
        for candidate in rooms:
            try:
                if isinstance(candidate.Location, LocationPoint):
                    room = candidate
                    break
            except Exception:
                continue
        if room is None and rooms:
            room = rooms[0]
        log('E2b using room %r for the C/E tests (no wall-bounded room available)'
            % (room.Number if room is not None else None))

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
        if not attempts:
            log('C/E SKIPPED - no room with a location point to work from.')
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
            log('E  NewRoom (%s): %s hasBoundary=%s number=%r'
                % (label, room_placement(probe_room), has_boundary, probe_room.Number))
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
                probe_room.Number = 'PHASE0-997'
                log('C  Number property setter -> %r  => WRITABLE (property)' % probe_room.Number)
            except Exception as ex:
                log('C  Number property setter FAILED: %s' % ex)
            try:
                doc.Delete(probe_room.Id)
            except Exception:
                pass
    except Exception as ex:
        log('C/E diagnostics failed: %s' % ex)

    solid = None
    if wall is None:
        log('B1 SKIPPED - no wall to build a band from.')
    else:
        try:
            solid = band_solid(wall.Location.Curve, BAND_WIDTH, z)
            log('B1 band solid built ok')
        except Exception as ex:
            solid = None
            log('B1 band solid FAILED: %s' % ex)
    if solid is not None:
        shape = None
        try:
            shape = make_direct_shape(doc, solid)
            log('B1 DirectShape created ok -> %s' % shape.Id)
        except Exception as ex:
            log('B1 DirectShape creation FAILED: %s' % ex)
        if shape is not None:
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
    if wall is None:
        log('VISUAL SKIPPED - there is no wall in this model to highlight.')
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
