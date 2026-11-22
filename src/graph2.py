# -*- coding: utf-8 -*-
"""Compare and Update Redraw - wall level merge with a visual preview.
Revit 2027 | Dynamo 4.0.2 | CPython3/PythonNet3

IN[0] Redraw directory
IN[1] comparison tolerance metres, optional, default 0.01
IN[2] boundary sample spacing metres, optional, default 0.25
IN[3] APPLY UPDATES boolean, optional, default False
IN[4] REDRAW WINS ON CONFLICTS boolean, optional, default False

OUT[0] Summary
OUT[1] Parameter Updates
OUT[2] Geometry Updates
OUT[3] Conflicts / Skips
OUT[4] QA / Errors

Boundary location is read from the baseline (Center or Finish).

The merge compares at wall level, not at room level, so a cluster of connected
stands merges as one change. For each cluster of stands that share walls:

  - a Main wall that already sits where a Redraw wall sits is kept;
  - a Main wall with no Redraw counterpart is deleted;
  - a Redraw wall with no Main counterpart is created;
  - a wall whose type, height, offset or flip changed is rebuilt;
  - a wall added to Main after the Redraw was cut is left alone.

Stands the Redraw added are created, stands it deleted are deleted, and stands
it moved or resized have their location point and tags moved.

Scope is deliberately controlled:
- only the Closed Wall - RX and Open Wall - RX wall types may be rebuilt
- a boundary that is not made of walls is skipped (Room Separation Lines)
- level mismatches and conflicts are skipped, unless the Redraw wins flag is set
"""
import clr, os, json, math, re, uuid
clr.AddReference('RevitAPI'); clr.AddReference('RevitServices')
from Autodesk.Revit.DB import (Arc, Line, XYZ, Wall, LocationCurve, LocationPoint, BuiltInParameter,
 BuiltInCategory, FilteredElementCollector, RevitLinkInstance, RevitLinkType,
 RevitLinkOptions, FilePath, ImportPlacement, Transaction, ModelPathUtils,
 SpatialElementBoundaryOptions, SpatialElementBoundaryLocation,
 UnitTypeId, UnitUtils, StorageType, ElementId, Level, Color, CurveLoop,
 DirectShape, GeometryCreationUtilities, OverrideGraphicSettings, UV)
from RevitServices.Persistence import DocumentManager

doc=DocumentManager.Instance.CurrentDBDocument
PARAMS=['Floorplan Hall','Floorplan Pavilion','Floorplan Sector',
        'Obstruction Area','Product Code','Custom Area']
RX_TYPES=('Closed Wall - RX','Open Wall - RX')
try:
    # Standalone, the merge core is a module. Inside the Dynamo graph the
    # generator inlines merge_core.py instead, so there is nothing to import.
    from merge_core import *
except Exception:
    pass

def iv(i,d=None):
    try: return d if IN[i] is None else IN[i]
    except: return d

def pv(x):
    if x is None:return None
    for a in ('FullName','Path'):
        try:
            v=getattr(x,a)
            if v:return str(v)
        except:pass
    return str(x)

def eidv(eid):
    # Revit 2024+ has ElementId.Value; the old IntegerValue is gone.
    return int(eid.Value)

def X(v):return XYZ(float(v[0]),float(v[1]),float(v[2]))
def dst(a,b):return math.sqrt((a.X-b.X)**2+(a.Y-b.Y)**2+(a.Z-b.Z)**2)
def flat(loops):return [c for loop in loops for c in loop]

def smp(c,sp):
    n=max(1,int(math.ceil(max(c.Length,1e-9)/sp)))
    return [c.Evaluate(float(i)/n,True) for i in range(n+1)]

def pd(p,cs):
    v=1e99
    for c in cs:
        try:
            q=c.Project(p)
            if q is not None:v=min(v,q.Distance)
        except:pass
    return v

def bd(a,b,sp):
    a=flat(a);b=flat(b)
    if not a or not b:return 1e99
    pa=[p for c in a for p in smp(c,sp)]
    pb=[p for c in b for p in smp(c,sp)]
    return max(max(pd(p,b) for p in pa),max(pd(p,a) for p in pb))

def centroid(loops):
    cs=flat(loops);pts=[p for c in cs for p in smp(c,max(c.Length/8.0,.01))]
    if not pts:return None
    return XYZ(sum(p.X for p in pts)/len(pts),sum(p.Y for p in pts)/len(pts),sum(p.Z for p in pts)/len(pts))


def wall_desc(d, seg):
    try:
        e=d.GetElement(seg.ElementId)
        if not isinstance(e,Wall): return None
        wt=d.GetElement(e.GetTypeId())
        lc=e.Location
        height=None; offset=0.0
        try: height=e.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM).AsDouble()
        except: pass
        try: offset=e.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET).AsDouble()
        except: pass
        return {'type':wt.Name if wt else None,'type_id':eidv(e.GetTypeId()),'level_id':eidv(e.LevelId),
                'height':height,'offset':offset,'flipped':bool(e.Flipped),'element_id':eidv(e.Id),
                'curve':lc.Curve if isinstance(lc,LocationCurve) else seg.GetCurve()}
    except:return None

def pvals(room):
    out={}
    for n in PARAMS:
        p=room.LookupParameter(n)
        if p is None:out[n]={'status':'PARAMETER_NOT_FOUND','value':None};continue
        try:
            if not p.HasValue:out[n]={'status':'NO_VALUE','value':None};continue
        except:pass
        try:
            v=p.AsString()
            if v is None:v=p.AsValueString()
            out[n]={'status':'VALUE','value':v}
        except:out[n]={'status':'READ_ERROR','value':None}
    return out

def state(room,tr=None,d=None,boundary_location='Finish'):
    opt=SpatialElementBoundaryOptions()
    opt.SpatialElementBoundaryLocation=SpatialElementBoundaryLocation.Center if boundary_location=='Center' else SpatialElementBoundaryLocation.Finish
    segloops=room.GetBoundarySegments(opt)
    try:
        rp=room.Location.Point if isinstance(room.Location,LocationPoint) else None
        if rp is not None and tr is not None: rp=tr.OfPoint(rp)
    except: rp=None
    try: level_name=room.Level.Name if room.Level else None
    except: level_name=None
    try: area=float(room.Area)
    except Exception: area=0.0
    base={'number':(room.Number or '').strip(),'room':room,'element_id':eidv(room.Id),
          'parameters':pvals(room),'level_id':eidv(room.LevelId),'level_name':level_name,
          'area':area,'loops':[],'boundary_ids':[],'wall_loops':[],'room_point':None}
    if segloops is None or len(segloops)==0:
        base.update({'placement_status':'NOT_PLACED','has_geometry':False,'centroid':None,'room_point':rp});return base
    loops=[];ids=[]
    wall_loops=[]
    for sl in segloops:
        curves=[];walls=[]
        for seg in sl:
            c=seg.GetCurve();curves.append(c.CreateTransformed(tr) if tr else c)
            ids.append(eidv(seg.ElementId))
            if d is not None:
                wd=wall_desc(d,seg)
                if wd is not None:
                    if tr is not None: wd['curve']=wd['curve'].CreateTransformed(tr)
                    walls.append(wd)
        loops.append(curves);wall_loops.append(walls)
    base.update({'placement_status':'PLACED','has_geometry':True,'loops':loops,
                 'centroid':centroid(loops),'boundary_ids':ids,'wall_loops':wall_loops,'room_point':rp})
    return base

def collect(d,tr,qa,label,boundary_location='Finish'):
    out={}
    rooms=FilteredElementCollector(d).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType().ToElements()
    for room in rooms:
        n=(room.Number or '').strip()
        if not n:continue
        if n in out:qa.append(label+' '+n+': duplicate Room Number');continue
        try:out[n]=state(room,tr,d,boundary_location)
        except Exception as e:qa.append(label+' '+n+': '+str(e))
    return out

def jc(q):
    t=q.get('type')
    if t=='LINE':return [Line.CreateBound(X(q['start']),X(q['end']))]
    if t=='ARC':return [Arc.Create(X(q['start']),X(q['end']),X(q['mid']))]
    if t=='POLYLINE':
        ps=[X(v) for v in q.get('points',[])];return [Line.CreateBound(ps[i-1],ps[i]) for i in range(1,len(ps)) if dst(ps[i-1],ps[i])>1e-9]
    return []

def bases(payload):
    out={}
    for n,r in payload.get('stands',{}).items():
        loops=[]
        for lp in r.get('loops',[]):
            cs=[]
            for q in lp.get('curves',[]):cs.extend(jc(q))
            if cs:loops.append(cs)
        pl=r.get('placement_status','PLACED');hg=bool(r.get('has_geometry',pl=='PLACED'))
        out[n]={'number':n,'placement_status':pl,'has_geometry':hg,'loops':loops,
                'centroid':X(r['centroid_internal_ft']) if r.get('centroid_internal_ft') else centroid(loops),
                'parameters':r.get('parameters',{}),'element_id':r.get('element_id'),
                'level_id':r.get('level_id'),'level_name':r.get('level_name'),
                'area':r.get('area_internal_sqft')}
    return out

def np(x):return ('ABSENT',None) if x is None else (x.get('status'),x.get('value'))
def geom(a,b,tol,sp):
    if a is None:return 'ADDED'
    if b is None:return 'REMOVED'
    if a['placement_status']!=b['placement_status'] or a['has_geometry']!=b['has_geometry']:return 'PLACEMENT_CHANGED'
    if not a['has_geometry']:return 'UNCHANGED'
    move=dst(a['centroid'],b['centroid']);delta=bd(a['loops'],b['loops'],sp)
    if delta<=tol:return 'UNCHANGED'
    if move>tol and delta<=move+tol:return 'MOVED'
    return 'MODIFIED'

def level_mismatch(b,m,r):
    bid=(b or {}).get('level_id'); mid=(m or {}).get('level_id')
    bl=(b or {}).get('level_name'); ml=(m or {}).get('level_name'); rl=(r or {}).get('level_name')
    if bid is not None and mid is not None and bid!=mid:
        return 'Main Room level differs from baseline'
    if bl and ml and bl!=ml:
        return 'Main Room level differs from baseline'
    if bl and rl and bl!=rl:
        return 'Redraw Room level differs from baseline'
    if ml and rl and ml!=rl:
        return 'Main and Redraw Room levels differ'
    return None

def prel(bp,mp,rp):
    b=np(bp);m=np(mp);r=np(rp)
    if m==b and r==b:return 'NO_CHANGE',b,m,r
    if m!=b and r==b:return 'MAIN_ONLY',b,m,r
    if m==b and r!=b:return 'REDRAW_ONLY',b,m,r
    if m==r:return 'SAME_CHANGE',b,m,r
    return 'CONFLICT',b,m,r

def desired(r):
    if r[0]=='VALUE':return '' if r[1] is None else str(r[1])
    if r[0]=='NO_VALUE':return ''
    return None

def files(folder):
    if not folder or not os.path.isdir(folder):raise Exception('Redraw directory is invalid')
    ns=os.listdir(folder)
    rv=[os.path.abspath(os.path.join(folder,n)) for n in ns if n.lower().endswith('.rvt') and not re.search(r'\.\d{4}\.rvt$',n,re.I) and os.path.isfile(os.path.join(folder,n))]
    js=[os.path.abspath(os.path.join(folder,n)) for n in ns if n.lower().endswith('.baseline.json') and os.path.isfile(os.path.join(folder,n))]
    if len(rv)!=1:raise Exception('Redraw directory must contain one primary RVT')
    if len(js)!=1:raise Exception('Redraw directory must contain one baseline JSON')
    return rv[0],js[0]

def norm(p):return os.path.normcase(os.path.abspath(p))
def find_link(host,path):
    target=norm(path);found=[]
    for x in FilteredElementCollector(host).OfClass(RevitLinkInstance).ToElements():
        try:
            ld=x.GetLinkDocument()
            if ld and ld.PathName and norm(ld.PathName)==target:found.append(x)
        except:pass
    if len(found)>1:raise Exception('Multiple Redraw link instances found')
    return found[0] if found else None

def find_link_type(host,path):
    target=norm(path);found=[]
    for t in FilteredElementCollector(host).OfClass(RevitLinkType).ToElements():
        try:
            ref=t.GetExternalFileReference()
            p=ref.GetAbsolutePath() if ref is not None else None
            if p is not None and norm(ModelPathUtils.ConvertModelPathToUserVisiblePath(p))==target:found.append(t)
        except:pass
    if len(found)>1:raise Exception('Multiple Redraw link types found')
    return found[0] if found else None

def find_instances(host,type_id):
    found=[]
    for x in FilteredElementCollector(host).OfClass(RevitLinkInstance).ToElements():
        try:
            if x.GetTypeId()==type_id:found.append(x)
        except:pass
    if len(found)>1:raise Exception('Multiple Redraw link instances found')
    return found[0] if found else None

def add_link(host,path):
    lt=find_link_type(host,path)
    if lt is not None:
        # Always reload, so the merge sees the Redraw as it is on disk now and
        # not as it was when the link was first loaded. Loading clears the
        # undo history, so it has to happen before the merge transaction.
        try:
            if RevitLinkType.IsLoaded(host,lt.Id):
                lt.Unload(None)
        except Exception:
            pass
        lt.Load()
        if not RevitLinkType.IsLoaded(host,lt.Id):
            raise Exception('Could not load the existing Redraw link')
        old=find_instances(host,lt.Id)
        if old:return old,lt.Id
        tx=Transaction(host,'MVP2.4B1 - Load Redraw')
        try:
            tx.Start();inst=RevitLinkInstance.Create(host,lt.Id,ImportPlacement.Origin);tx.Commit();return inst,lt.Id
        except:
            try:tx.RollBack()
            except:pass
            raise
    old=find_link(host,path)
    if old:return old,old.GetTypeId()
    tx=Transaction(host,'MVP2.4B1 - Load Redraw')
    try:
        tx.Start();res=RevitLinkType.Create(host,FilePath(path),RevitLinkOptions(False))
        inst=RevitLinkInstance.Create(host,res.ElementId,ImportPlacement.Origin);tx.Commit();return inst,res.ElementId
    except:
        try:tx.RollBack()
        except:pass
        raise

def remove_link_type(host,type_id,inside_transaction=False):
    if inside_transaction:
        host.Delete(type_id)
        return True
    tx=Transaction(host,'MVP2.4B1 - Remove Redraw Link')
    try:
        tx.Start();host.Delete(type_id);tx.Commit();return True
    except:
        try:tx.RollBack()
        except:pass
        raise


def wall_types(host):
    return {w.Name:w for w in FilteredElementCollector(host).OfClass(__import__('Autodesk.Revit.DB',fromlist=['WallType']).WallType).ToElements()}


def move_room_to_redraw(main_room,redraw):
    target=redraw.get('room_point') or redraw.get('centroid')
    if target is None: raise Exception('Redraw Room has no usable location point')
    loc=main_room.Location
    if not isinstance(loc,LocationPoint): raise Exception('Main Room has no movable location point')
    current=loc.Point
    delta=XYZ(target.X-current.X,target.Y-current.Y,0.0)
    if delta.GetLength()>1e-9: loc.Move(delta)
    return target

def room_tag_ids_in_active_view(host,room):
    # Capture tags before moving the Room, while association is still reliable.
    ids=[]
    view=host.ActiveView
    if view is None:return ids
    tags=FilteredElementCollector(host,view.Id).OfCategory(BuiltInCategory.OST_RoomTags).WhereElementIsNotElementType().ToElements()
    for tag in tags:
        matched=False
        try:
            tagged=tag.Room
            matched=tagged is not None and tag.Room.Id==room.Id
        except:pass
        if not matched:
            try:matched=tag.TaggedLocalRoomId==room.Id
            except:pass
        if matched:ids.append(tag.Id)
    return ids

def centre_room_tags(host,tag_ids,target):
    moved=0;errors=[]
    for tag_id in tag_ids:
        try:
            tag=host.GetElement(tag_id)
            if tag is None:
                errors.append('Tag no longer exists');continue
            p=tag.TagHeadPosition
            tag.TagHeadPosition=XYZ(target.X,target.Y,p.Z)
            moved+=1
        except Exception as e:errors.append(str(e))
    return moved,errors


def validate(payload,rvt,side,host,qa,issues):
    if str(payload.get('schema_version'))!='1.0':raise Exception('Baseline schema_version is not 1.0')
    if payload.get('snapshot_type')!='REDRAW_BASELINE':raise Exception('Baseline snapshot_type is not REDRAW_BASELINE')
    red=payload.get('redraw') or {}
    ex=red.get('filename')
    if not ex:raise Exception('Baseline redraw.filename is missing')
    if ex.lower()!=os.path.basename(rvt).lower():raise Exception('Baseline does not match Redraw RVT')
    sx=red.get('sidecar_filename')
    if not sx:raise Exception('Baseline redraw.sidecar_filename is missing')
    if sx.lower()!=os.path.basename(side).lower():raise Exception('Baseline sidecar identity mismatch')
    settings=payload.get('extraction_settings') or {}
    loc=settings.get('boundary_location')
    if loc not in ('Finish','Center'):raise Exception('Baseline boundary_location must be Finish or Center')
    mp=settings.get('merge_parameters')
    if mp is not None and list(mp)!=PARAMS:raise Exception('Baseline merge parameter list differs from graph')
    stands=payload.get('stands')
    if not isinstance(stands,dict) or not stands:raise Exception('Baseline stands are missing or empty')
    src=payload.get('source') or {}
    bpn=str(src.get('project_number') or '').strip()
    hpn=str(getattr(host.ProjectInformation,'Number','') or '').strip()
    if bpn and hpn and bpn!=hpn:raise Exception('Baseline project number does not match active Main')
    if not bpn or not hpn:
        msg='WARNING: Project number missing; source identity check skipped'
        qa.append(msg);issues.append({'stand':None,'type':'IDENTITY WARNING','reason':msg})
    return loc

# --------------------------------------------------------------------------
# wall level merge
# --------------------------------------------------------------------------


PREVIEW_MARK = 'REDRAW MERGE PREVIEW'
PREVIEW_BLUE = (0, 120, 255)
PREVIEW_AMBER = (255, 150, 0)
PREVIEW_RED = (220, 40, 40)
PREVIEW_SKIPPED = (200, 0, 200)


def wall_points(curve):
    """Point list for a wall curve, in the shape merge_core expects."""
    try:
        if isinstance(curve, Line):
            a = curve.GetEndPoint(0)
            b = curve.GetEndPoint(1)
            return [[(a.X, a.Y, a.Z), (b.X, b.Y, b.Z)]]
    except Exception:
        pass
    points = []
    for index in range(17):
        p = curve.Evaluate(float(index) / 16.0, True)
        points.append((p.X, p.Y, p.Z))
    return [points]


def wall_attributes(wd):
    """What makes a wall a different wall rather than a different position:
    type, height, offset and flip."""
    return (wd.get('type'), wd.get('height'), wd.get('offset'), bool(wd.get('flipped')))


def wall_records(descriptors):
    out = []
    for wd in descriptors:
        out.append({'points': wall_points(wd['curve']),
                    'attributes': wall_attributes(wd),
                    'ref': wd})
    return out


def state_walls(state):
    """The walls bounding a state, de-duplicated by element.

    A wall shared by two stands is listed by both, so the same wall comes back
    more than once. It is still one wall.
    """
    out = {}
    for loop in state.get('wall_loops', []):
        for wd in loop:
            out.setdefault(wd['element_id'], wd)
    return out


def collect_walls(doc, tr=None):
    """Every wall in a document, not only the ones that bound a room.

    Deleting a stand in the Redraw leaves its walls behind as orphans: they
    are still walls, they simply no longer bound a room. Without this the
    merge would think the Redraw deleted them and take them out of Main.
    """
    out = {}
    try:
        walls = FilteredElementCollector(doc).OfClass(Wall).ToElements()
    except Exception:
        return out
    for wall in walls:
        try:
            location = wall.Location
            curve = location.Curve if isinstance(location, LocationCurve) else None
            if curve is None:
                continue
            if tr is not None:
                curve = curve.CreateTransformed(tr)
            height = None
            offset = 0.0
            try:
                height = wall.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM).AsDouble()
            except Exception:
                pass
            try:
                offset = wall.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET).AsDouble()
            except Exception:
                pass
            try:
                wall_type = doc.GetElement(wall.GetTypeId())
                type_name = wall_type.Name if wall_type else None
            except Exception:
                type_name = None
            out[eidv(wall.Id)] = {'type': type_name, 'type_id': None,
                                  'level_id': eidv(wall.LevelId), 'height': height,
                                  'offset': offset, 'flipped': bool(wall.Flipped),
                                  'element_id': eidv(wall.Id), 'curve': curve}
        except Exception:
            continue
    return out


def state_wall_usage(states, prefix):
    """wall -> the stands that use it, for one document.

    The prefix keeps Main's element ids apart from the Redraw link's, which
    are different walls that can share a number.
    """
    usage = {}
    for number, state in states.items():
        for loop in state.get('wall_loops', []):
            for wd in loop:
                usage.setdefault((prefix, wd['element_id']), set()).add(number)
    return usage


def stand_status(base, main, redraw, tol, sp):
    """How one stand compares across the baseline, Main and the Redraw.

    ALREADY       Main already is what the Redraw wants - nothing to do.
    UNCHANGED     the Redraw did not touch it.
    CONFLICT      Main and the Redraw both changed it, differently.
    AFFECTED      the Redraw changed it and Main did not - merge it.
    ADDED         it is new in the Redraw.
    REMOVED       the Redraw deleted it.
    MAIN_ONLY     it is new in Main, after the Redraw was cut - leave it alone.
    MAIN_REMOVED  Main deleted it after the Redraw was cut.
    """
    if redraw is None:
        if base is not None and main is not None:
            return 'REMOVED'
        return 'MAIN_ONLY'
    if main is None:
        if base is None:
            return 'ADDED'
        return 'MAIN_REMOVED'
    if not redraw.get('has_geometry'):
        # The Redraw has the room but it bounds nothing: the designer took the
        # stand out, so its room goes too.
        return 'REMOVED'
    gm = geom(base, main, tol, sp) if base is not None else 'ADDED'
    gr = geom(base, redraw, tol, sp) if base is not None else 'ADDED'
    mr = geom(main, redraw, tol, sp)
    if mr == 'UNCHANGED':
        return 'ALREADY'
    if gr == 'UNCHANGED':
        return 'UNCHANGED'
    if gm != 'UNCHANGED' and gr != 'UNCHANGED':
        return 'CONFLICT'
    return 'AFFECTED'


def state_loop_points(state):
    """The boundary of a state as plain point lists.

    The Redraw's curves come from a link, and they die with it. Taking a copy
    as plain points lets the link go inside the merge transaction, which is
    what makes undo one step.
    """
    out = []
    for loop in state.get('loops', []):
        for curve in loop:
            try:
                out.append(wall_points(curve)[0])
            except Exception:
                pass
    return out


def baseline_walls(base_state):
    """The baseline curves of a stand, as wall records."""
    out = []
    for curves in base_state.get('loops', []):
        for curve in curves:
            out.append({'points': wall_points(curve), 'attributes': None, 'ref': curve})
    return out


def component_level(component, main):
    """The Main level id a component's walls are built on."""
    for number in component:
        state = main.get(number)
        if state is not None and state.get('level_id') is not None:
            return state['level_id']
    return None


def find_level_by_name(host, name):
    """The Main level with this name, or None."""
    if not name:
        return None
    try:
        levels = FilteredElementCollector(host).OfClass(Level).ToElements()
    except Exception:
        return None
    for level in levels:
        try:
            if level.Name == name:
                return level
        except Exception:
            pass
    return None


def create_wall(host, level_id, wd):
    """Create one wall in Main from a Redraw wall description."""
    types = wall_types(host)
    wall_type = types.get(wd['type'])
    if wall_type is None:
        raise Exception('Wall type not found in Main: ' + str(wd['type']))
    height = wd.get('height')
    if height is None or height <= 0:
        height = 10.0
    wall = Wall.Create(host, wd['curve'], wall_type.Id, level_id, height,
                       float(wd.get('offset') or 0.0), bool(wd.get('flipped', False)), False)
    return eidv(wall.Id)


def create_room(host, level, redraw_state, number, parameter_values):
    """Create a stand that the Redraw added."""
    point = redraw_state.get('room_point') or redraw_state.get('centroid')
    if point is None:
        raise Exception('Redraw Room has no usable location point')
    room = host.Create.NewRoom(level, UV(point.X, point.Y))
    if room is None:
        raise Exception('NewRoom returned nothing')
    room.Number = number
    written = []
    for name in PARAMS:
        value = parameter_values.get(name)
        if value is None:
            continue
        parameter = room.LookupParameter(name)
        if parameter is None or parameter.IsReadOnly:
            continue
        if parameter.StorageType != StorageType.String:
            continue
        try:
            if parameter.Set(str(value)):
                written.append(name)
        except Exception:
            pass
    location = room.Location
    if isinstance(location, LocationPoint):
        current = location.Point
        delta = XYZ(point.X - current.X, point.Y - current.Y, 0.0)
        if delta.GetLength() > 1e-9:
            location.Move(delta)
    return room, written


def band_solid(curve, width, z):
    """A filled band along a wall, for the preview."""
    try:
        if isinstance(curve, Line):
            a = curve.GetEndPoint(0)
            b = curve.GetEndPoint(1)
            ax, ay, bx, by = a.X, a.Y, b.X, b.Y
        else:
            points = [curve.Evaluate(float(i) / 16.0, True) for i in range(17)]
            ax, ay, bx, by = points[0].X, points[0].Y, points[-1].X, points[-1].Y
    except Exception:
        raise Exception('Cannot build a preview band for this curve')
    dx = bx - ax
    dy = by - ay
    length = math.sqrt(dx * dx + dy * dy)
    if length < 1e-9:
        raise Exception('Degenerate wall for the preview band')
    dx = dx / length
    dy = dy / length
    ox = -dy * width / 2.0
    oy = dx * width / 2.0
    quad = [XYZ(ax + ox, ay + oy, z), XYZ(bx + ox, by + oy, z),
            XYZ(bx - ox, by - oy, z), XYZ(ax - ox, ay - oy, z)]
    loop = CurveLoop()
    for index in range(4):
        loop.Append(Line.CreateBound(quad[index], quad[(index + 1) % 4]))
    return GeometryCreationUtilities.CreateExtrusionGeometry([loop], XYZ(0.0, 0.0, 1.0), 0.05)


def make_override(view, category_id, rgb):
    """A graphics override that paints elements in one colour."""
    colour = Color(rgb[0], rgb[1], rgb[2])
    try:
        style = OverrideGraphicSettings(view.GetCategoryOverrides(category_id))
    except Exception:
        style = OverrideGraphicSettings()
    style.SetCutForegroundPatternColor(colour)
    style.SetCutForegroundPatternVisible(True)
    style.SetCutLineColor(colour)
    style.SetProjectionLineColor(colour)
    style.SetSurfaceForegroundPatternColor(colour)
    style.SetSurfaceForegroundPatternVisible(True)
    return style


def preview_height(main, gplan):
    """The height to draw the preview bands at."""
    for op in gplan:
        for number in op.get('affected', []):
            state = main.get(number)
            if state is None:
                continue
            try:
                return state['room'].Level.Elevation + 0.02
            except Exception:
                pass
    return 0.02


def clear_preview(host, view):
    """Remove the preview left by an earlier run."""
    removed = 0
    try:
        elements = FilteredElementCollector(host).OfCategory(BuiltInCategory.OST_GenericAnnotation) \
            .WhereElementIsNotElementType().ToElements()
    except Exception:
        return 0
    for element in elements:
        try:
            parameter = element.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if parameter is None or parameter.StorageType != StorageType.String:
                continue
            text = parameter.AsString() or ''
            if not text.startswith(PREVIEW_MARK):
                continue
            for token in text.replace('walls=', '').split(','):
                token = token.strip()
                if not token.isdigit():
                    continue
                try:
                    wall = host.GetElement(ElementId(int(token)))
                    if wall is not None:
                        view.SetElementOverrides(wall.Id,
                                                 view.GetCategoryOverrides(wall.Category.Id))
                except Exception:
                    pass
            host.Delete(element.Id)
            removed += 1
        except Exception:
            pass
    return removed


def draw_preview(host, view, bands, recolour, z, qa):
    """Draw the visual diff on the plan.

    `bands` is a list of (curve, rgb) for the wall positions to mark.
    `recolour` is a list of (element_id, rgb) for the existing Main walls,
    which are recoloured in place as well as banded.

    Returns (bands drawn, walls recoloured).
    """
    drawn = 0
    recoloured = 0
    shapes = []
    category = ElementId(BuiltInCategory.OST_GenericAnnotation)
    by_colour = {}
    for curve, rgb in bands:
        try:
            solid = band_solid(curve, 0.30, z)
        except Exception as ex:
            qa.append('PREVIEW: could not band a wall (%s)' % ex)
            continue
        by_colour.setdefault(rgb, []).append(solid)
    for rgb, solids in by_colour.items():
        try:
            shape = DirectShape.CreateElement(host, category)
            shape.SetShape(solids)
            view.SetElementOverrides(shape.Id, make_override(view, category, rgb))
            shapes.append((shape, rgb))
            drawn += len(solids)
        except Exception as ex:
            qa.append('PREVIEW: could not draw the %s band(s) (%s)'
                      % ('blue' if rgb == PREVIEW_BLUE else
                         ('amber' if rgb == PREVIEW_AMBER else 'red'), ex))
    coloured = {}
    for element_id, rgb in recolour:
        try:
            element = host.GetElement(ElementId(element_id))
            if element is None:
                qa.append('PREVIEW: wall %s is no longer in the model' % element_id)
                continue
            view.SetElementOverrides(element.Id, make_override(view, element.Category.Id, rgb))
            coloured.setdefault(rgb, []).append(str(element_id))
            recoloured += 1
        except Exception as ex:
            qa.append('PREVIEW: could not recolour wall %s (%s)' % (element_id, ex))
    for shape, rgb in shapes:
        try:
            comment = shape.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if comment is not None and comment.StorageType == StorageType.String:
                comment.Set(PREVIEW_MARK + ' walls=' + ','.join(coloured.get(rgb, [])))
        except Exception:
            qa.append('PREVIEW: could not mark a preview element; it may need deleting by hand')
    return drawn, recoloured


def write_audit(folder, base_name, rows, totals, mode, run_id):
    """Append this run to the CSV audit trail next to the Redraw."""
    import csv
    import datetime
    stamp = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    log_path = os.path.join(folder, base_name + '.merge-log.csv')
    summary_path = os.path.join(folder, base_name + '.merge-summary.csv')
    header = ['run_utc', 'run_id', 'mode', 'cluster', 'stand_number', 'stand_name',
              'change', 'status', 'walls_replaced', 'walls_created', 'walls_deleted',
              'params_written', 'tags_moved', 'details']
    new_file = not os.path.isfile(log_path)
    try:
        with open(log_path, 'a', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            if new_file:
                writer.writerow(header)
            for row in rows:
                writer.writerow([stamp, run_id, mode] + row)
        with open(summary_path, 'w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow(['run_utc', 'run_id', 'mode', 'clusters_merged', 'clusters_skipped',
                             'stands_moved', 'stands_resized', 'stands_added', 'stands_removed',
                             'walls_replaced', 'walls_created', 'walls_deleted',
                             'params_written', 'tags_moved', 'conflicts'])
            writer.writerow([stamp, run_id, mode] + totals)
        return True
    except Exception:
        return False

def where(state):
    """A short description of where a stand is, for the QA log."""
    if state is None:
        return 'absent'
    return '%s, %.0f sqft, %d boundary wall(s)' % (
        'placed' if state.get('has_geometry') else 'NO BOUNDARY',
        state.get('area') or 0.0,
        sum(len(loop) for loop in state.get('wall_loops', [])))


def plan_clusters(b, m, r, r_walls, tol, sp, redraw_wins, qa, issues):
    """Plan the merge, one cluster at a time.

    Returns (geometry plan, parameter plan). The geometry plan has one entry
    per affected cluster; each entry carries the walls to keep, replace, delete
    and create, and the room work to do.
    """
    # how every stand compares across baseline, Main and Redraw
    statuses={}
    for n in sorted(set(b)|set(m)|set(r)):
        statuses[n]=stand_status(b.get(n),m.get(n),r.get(n),tol,sp)

    # cluster the stands: neighbours share a wall
    usage={}
    usage.update(state_wall_usage(m,'m'));usage.update(state_wall_usage(r,'r'))
    adjacency=adjacency_from_wall_usage(usage)
    components=find_components(sorted(set(b)|set(m)|set(r)),adjacency)

    # which Main stands use each Main wall
    wall_stands={}
    for number,state in m.items():
        for wd in state_walls(state).values():
            wall_stands.setdefault(wd['element_id'],set()).add(number)

    gplan=[];pplan=[];comp_no=0
    for comp in components:
        affected=[n for n in comp if statuses.get(n) in ('AFFECTED','ADDED','REMOVED')]
        conflicted=[n for n in comp if statuses.get(n)=='CONFLICT']
        if not affected and not conflicted:continue
        comp_no+=1;cid='C%d'%comp_no
        op={'cluster':cid,'stands':comp,'affected':affected,'status':'PLANNED','change':'MOVED'}
        blocked=None
        for n in affected:
            reason=level_mismatch(b.get(n),m.get(n),r.get(n))
            if reason:blocked=reason;break
        if blocked is None and conflicted and not redraw_wins:
            blocked='Main and the Redraw changed this cluster differently'
        if blocked is not None:
            op['status']='SKIPPED';op['reason']=blocked
            for n in conflicted:
                issues.append({'stand':n,'cluster':cid,'type':'GEOMETRY CONFLICT','reason':blocked})
            for n in comp:
                qa.append('CLUSTER %s stand %s: baseline=%s | Main=%s | Redraw=%s'
                          %(cid,n,where(b.get(n)),where(m.get(n)),where(r.get(n))))
            gplan.append(op);continue
        if conflicted and redraw_wins:
            # The Redraw's version wins, so the conflicted stands are planned
            # like any other changed stand.
            for n in conflicted:
                issues.append({'stand':n,'cluster':cid,'type':'GEOMETRY CONFLICT',
                               'reason':'Main and the Redraw changed this stand differently; the Redraw wins'})
                if n not in affected:affected.append(n)
            op['affected']=affected
        mw={};rw={};bw=[]
        for n in affected:
            if n in m:
                for wd in state_walls(m[n]).values():mw.setdefault(wd['element_id'],wd)
            if n in r:
                for wd in state_walls(r[n]).values():rw.setdefault(wd['element_id'],wd)
            if n in b:bw.extend(baseline_walls(b[n]))
        base_records=wall_records(list(mw.values()))
        redraw_records=wall_records(list(rw.values()))
        if r_walls:
            # Walls of the Redraw that no longer bound a room - the designer
            # deleted the stand but kept its walls - are still walls, so they
            # belong to the cluster and must not be taken out of Main.
            cell=cell_size(tol)
            buckets=build_buckets(r_walls,cell)
            for record in bw:
                for index in find_matches(record,r_walls,buckets,tol,sp,cell):
                    orphan=r_walls[index]
                    key=orphan['ref']['element_id']
                    if key in rw:
                        # already known from a room boundary
                        continue
                    rw[key]=orphan['ref']
                    redraw_records.append(orphan)
        plan=plan_component(base_records,redraw_records,bw,tol,sp)
        if plan['conflicts']:
            for wall,reason in plan['conflicts']:
                issues.append({'stand':None,'cluster':cid,'type':'GEOMETRY CONFLICT','reason':reason})
            if redraw_wins:
                for wall,reason in plan['conflicts']:plan['create'].append(wall)
            else:
                op['status']='SKIPPED'
                op['reason']='Main and the Redraw disagree about a wall'
                gplan.append(op);continue
        if redraw_wins and plan['retained']:
            # Main's own version of a wall the Redraw also changed is dropped:
            # the Redraw wins.
            for wall,reason in plan['retained']:plan['delete'].append(wall)
            plan['retained']=[]
        # Business rules: the boundary must be made of walls, and only the RX
        # wall types may be rebuilt.
        guard=None
        removed_stands=[n for n in comp if statuses.get(n)=='REMOVED']
        for wall in plan['delete']:
            owners=wall_stands.get(wall['ref']['element_id'],set())
            staying=sorted([n for n in owners if n not in removed_stands])
            if len(staying)>1:
                # The wall separates two stands that both keep a room, so
                # removing it would put both rooms in one region.
                guard=('The Redraw removes a wall that still separates %s, so they would '
                       'end up in one region. Delete one of them in the Redraw, or leave '
                       'the wall in the Redraw too.'%(', '.join(staying)))
                break
        for n in affected:
            for source in (m,r):
                st=source.get(n)
                if st is None:continue
                walls=sum(len(l) for l in st.get('wall_loops',[]))
                curves=sum(len(l) for l in st.get('loops',[]))
                if walls!=curves:
                    guard='Stand %s has a boundary that is not made of walls'%n;break
            if guard:break
        if guard is None:
            touched=[w for w in plan['delete']]+[w for w,_ in plan['replace']]
            for wall in touched:
                if wall['ref'].get('type') not in RX_TYPES:
                    guard='Unsupported Main wall type: '+str(wall['ref'].get('type'));break
        if guard is None:
            for wall in plan['create']+[w for _,w in plan['replace']]:
                if wall['ref'].get('type') not in RX_TYPES:
                    guard='Unsupported Redraw wall type: '+str(wall['ref'].get('type'));break
        if guard is not None:
            op['status']='SKIPPED';op['reason']=guard
            for n in comp:
                qa.append('CLUSTER %s stand %s: baseline=%s | Main=%s | Redraw=%s'
                          %(cid,n,where(b.get(n)),where(m.get(n)),where(r.get(n))))
            gplan.append(op);continue
        op['_plan']=plan
        op['_level']=component_level(affected,m)
        op['_want']={n:state_loop_points(r[n]) for n in affected if n in r and r[n].get('loops')}
        rooms=[]
        for n in affected:
            kind=statuses.get(n)
            if kind=='ADDED':rooms.append(('ADD',n))
            elif kind=='REMOVED':rooms.append(('DELETE',n))
            else:rooms.append(('MOVE',n))
        op['_rooms']=rooms
        op['change']='CLUSTER'
        op['walls_kept']=len(plan['keep']);op['walls_replaced']=len(plan['replace'])
        op['walls_created']=len(plan['create']);op['walls_deleted']=len(plan['delete'])
        gplan.append(op)

    return gplan,pplan


qa=[];pout=[];gout=[];issues=[];summary={'Status':'FAILED'}
lnk=None;link_type_id=None;cleanup=False
try:
    if doc is None:raise Exception('No active Main document')
    folder=pv(iv(0));tol_m=float(iv(1,.01));sp_m=float(iv(2,.25))
    apply=bool(iv(3,False));redraw_wins=bool(iv(4,False))
    rvt,side=files(os.path.abspath(folder.strip()))
    rvt_norm=os.path.normcase(rvt)
    if doc.PathName and os.path.normcase(os.path.abspath(doc.PathName))==rvt_norm:
        raise Exception('Active document is the Redraw. Open Main before running Compare and Update Redraw.')
    for open_doc in doc.Application.Documents:
        try: op=open_doc.PathName
        except: op=None
        if op and os.path.normcase(os.path.abspath(op))==rvt_norm:
            raise Exception('Close the Redraw before running Compare and Update Redraw.')
    with open(side,'r',encoding='utf-8') as f:payload=json.load(f)
    loc=validate(payload,rvt,side,doc,qa,issues)
    lnk,link_type_id=add_link(doc,rvt);cleanup=True;ldoc=lnk.GetLinkDocument()
    if ldoc is None:raise Exception('Redraw link is unavailable')
    tol=UnitUtils.ConvertToInternalUnits(tol_m,UnitTypeId.Meters);sp=UnitUtils.ConvertToInternalUnits(sp_m,UnitTypeId.Meters)
    b=bases(payload);m=collect(doc,None,qa,'Main',loc);r=collect(ldoc,lnk.GetTotalTransform(),qa,'Redraw',loc)

    # ---- how every stand compares across baseline, Main and Redraw ----
    statuses={}
    for n in sorted(set(b)|set(m)|set(r)):
        statuses[n]=stand_status(b.get(n),m.get(n),r.get(n),tol,sp)

    # ---- cluster the stands: neighbours share a wall ----
    usage={}
    usage.update(state_wall_usage(m,'m'));usage.update(state_wall_usage(r,'r'))
    adjacency=adjacency_from_wall_usage(usage)
    components=find_components(sorted(set(b)|set(m)|set(r)),adjacency)

    redraw_all=wall_records(list(collect_walls(ldoc,lnk.GetTotalTransform()).values()))
    gplan,pplan=plan_clusters(b,m,r,redraw_all,tol,sp,redraw_wins,qa,issues)

    # ---- parameters, as before ----
    for n in sorted(set(b)|set(m)|set(r)):
        if n in b and n in m and n in r:
            for pn in PARAMS:
                rel,bv,mv,rv=prel(b[n].get('parameters',{}).get(pn),m[n].get('parameters',{}).get(pn),r[n].get('parameters',{}).get(pn))
                if rel=='REDRAW_ONLY':
                    target=desired(rv);op={'stand':n,'main_before':mv[1],'baseline':bv[1],'main_after':mv[1],'status':'PLANNED','redraw':rv[1],'parameter':pn}
                    if target is None:op['status']='SKIPPED';op['reason']='Redraw parameter cannot be written safely';pout.append(op)
                    else:op['_target']=target;pplan.append(op)
                elif rel=='CONFLICT':
                    issues.append({'stand':n,'type':'PARAMETER CONFLICT','parameter':pn,'baseline':bv[1],'main':mv[1],'redraw':rv[1]})

    # ---- what the plan would change, for the preview ----
    bands=[];recolour=[]
    for op in gplan:
        if op['status']!='PLANNED':
            # Show what was skipped, and why, rather than leaving the plan
            # looking empty.
            for number in op.get('affected',[]) or op.get('stands',[]):
                state=m.get(number)
                if state is None:continue
                for wd in state_walls(state).values():
                    bands.append((wd['curve'],PREVIEW_SKIPPED))
            continue
        plan=op['_plan']
        for wall in plan['create']:bands.append((wall['ref']['curve'],PREVIEW_BLUE))
        for wall,redraw_wall in plan['replace']:
            bands.append((redraw_wall['ref']['curve'],PREVIEW_BLUE))
            bands.append((wall['ref']['curve'],PREVIEW_AMBER))
            recolour.append((wall['ref']['element_id'],PREVIEW_AMBER))
        for wall in plan['delete']:
            bands.append((wall['ref']['curve'],PREVIEW_AMBER))
            recolour.append((wall['ref']['element_id'],PREVIEW_AMBER))
        for wall,reason in plan['retained']:
            bands.append((wall['ref']['curve'],PREVIEW_RED))
            recolour.append((wall['ref']['element_id'],PREVIEW_RED))

    summary_link_removed='PENDING' if cleanup else 'NOT REQUIRED'
    preview_note=None

    if not apply:
        for op in pplan:op.pop('_target',None);pout.append(op)
        for op in gplan:
            op.pop('_plan',None);op.pop('_rooms',None);op.pop('_level',None)
            op['status']='PLANNED';gout.append(op)
        # Draw the visual diff so the plan can be checked on the plan. Creating
        # the bands and recolouring the walls both modify the document, so
        # they need a transaction.
        preview_tx=Transaction(doc,'Redraw Merge Preview')
        try:
            preview_tx.Start()
            view=doc.ActiveView
            cleared=clear_preview(doc,view)
            z=preview_height(m,gplan)
            drawn,recoloured=draw_preview(doc,view,bands,recolour,z,qa)
            preview_tx.Commit()
            preview_note=('Preview drawn: %d band(s) (%d blue, %d amber, %d red), %d wall(s) recoloured.'%(
                drawn,
                sum(1 for b2 in bands if b2[1]==PREVIEW_BLUE),
                sum(1 for b2 in bands if b2[1]==PREVIEW_AMBER),
                sum(1 for b2 in bands if b2[1]==PREVIEW_RED),
                recoloured))
            preview_note+=(' %d band(s) are magenta: those clusters are skipped.'
                           %sum(1 for b2 in bands if b2[1]==PREVIEW_SKIPPED))
            if cleared:preview_note+=' Removed %d element(s) from an earlier preview.'%cleared
            qa.append(preview_note)
        except Exception as ex:
            try:preview_tx.RollBack()
            except Exception:pass
            preview_note='Preview could not be drawn: '+str(ex);qa.append(preview_note)
    else:
        tx=Transaction(doc,'MVP2.4B6 - Merge Redraw Updates')
        try:
            tx.Start()
            try:
                clear_preview(doc,doc.ActiveView)
            except Exception:
                pass
            for op in gplan:
                if op['status']!='PLANNED':continue
                plan=op['_plan']
                try:
                    written=0;moved=0;tags=0
                    # Rooms the Redraw deleted go first. Removing a shared wall
                    # while both rooms still exist puts two rooms in one region,
                    # and Revit's answer to that is a modal warning that edits
                    # the document outside this transaction.
                    for action,number in op['_rooms']:
                        if action=='DELETE':
                            doc.Delete(ElementId(m[number]['element_id']))
                    doc.Regenerate()
                    for wall in plan['delete']:doc.Delete(ElementId(wall['ref']['element_id']))
                    for wall,redraw_wall in plan['replace']:doc.Delete(ElementId(wall['ref']['element_id']))
                    doc.Regenerate()
                    level_id=op['_level']
                    for wall in plan['create']:create_wall(doc,level_id,wall['ref'])
                    for wall,redraw_wall in plan['replace']:create_wall(doc,level_id,redraw_wall['ref'])
                    doc.Regenerate()
                    for action,number in op['_rooms']:
                        if action=='ADD':
                            redraw_state=r.get(number)
                            if redraw_state is None:raise Exception('No Redraw Room for stand '+str(number))
                            values={}
                            for pn in PARAMS:
                                pv=redraw_state.get('parameters',{}).get(pn)
                                if pv is not None and pv[0]=='VALUE':values[pn]=pv[1]
                            level=find_level_by_name(doc,redraw_state.get('level_name'))
                            if level is None:raise Exception('Level not found in Main: '+str(redraw_state.get('level_name')))
                            room,params=create_room(doc,level,redraw_state,number,values)
                            written+=len(params)
                        elif action=='MOVE':
                            room=m[number]['room']
                            tag_ids=room_tag_ids_in_active_view(doc,room)
                            target=move_room_to_redraw(room,r[number])
                            moved+=1
                            tmoved,tag_errors=centre_room_tags(doc,tag_ids,target)
                            tags+=tmoved
                    op['params_written']=written;op['rooms_moved']=moved;op['tags_moved']=tags
                    op['status']='APPLIED'
                except Exception as e:
                    op['status']='FAILED';op['reason']=str(e)
            # The Redraw link goes inside the same transaction as the merge, so
            # one undo takes the whole thing back.
            try:
                remove_link_type(doc,link_type_id,True)
                cleanup=False
                summary_link_removed='YES'
            except Exception as e:
                qa.append('LINK: could not remove the Redraw link in the merge transaction: '+str(e))
            for op in pplan:
                p=m[op['stand']]['room'].LookupParameter(op['parameter'])
                if p is None:op['status']='SKIPPED';op['reason']='Parameter not found in Main'
                elif p.IsReadOnly:op['status']='SKIPPED';op['reason']='Parameter is read-only'
                elif p.StorageType!=StorageType.String:op['status']='SKIPPED';op['reason']='Parameter is not text'
                else:
                    try:op['status']='APPLIED' if p.Set(op['_target']) else 'FAILED'
                    except Exception as e:op['status']='FAILED';op['reason']=str(e)
            tx.Commit()
        except:
            try:tx.RollBack()
            except:pass
            raise
        # Committing regenerates the document by itself, so there is no need
        # to regenerate here - and doing so outside a transaction is refused.
        fresh=collect(doc,None,qa,'Verify',loc)
        for op in gplan:
            if op['status']=='APPLIED':
                worst=0.0;ok=True
                for number in op['affected']:
                    want=op.get('_want',{}).get(number)
                    if not want:continue
                    after=fresh.get(number)
                    if after is None:ok=False;break
                    delta=curve_distance(state_loop_points(after),want,sp)
                    if delta>worst:worst=delta
                if ok and worst<=tol:op['status']='VERIFIED'
                else:
                    op['status']='VERIFY_FAILED'
                    op['reason']='Main does not match the Redraw after the update'
            op.pop('_plan',None);op.pop('_rooms',None);op.pop('_level',None);op.pop('_want',None)
            gout.append(op)
        for op in pplan:
            if op['status']=='APPLIED':
                p=m[op['stand']]['room'].LookupParameter(op['parameter']);after=p.AsString() if p else None;op['main_after']=after
                op['status']='VERIFIED' if after==op['_target'] else 'VERIFY_FAILED'
                if op['status']!='VERIFIED':op['reason']='Main value does not match Redraw after update'
            op.pop('_target',None);pout.append(op)
    # An apply removes the link inside the merge transaction, so one undo
    # takes the whole merge back. A dry run has no merge, so the link is
    # removed on its own afterwards.
    if cleanup and link_type_id is not None:
        remove_link_type(doc,link_type_id)
        cleanup=False
        summary_link_removed='YES'

    # ---- the audit trail ----
    try:
        rows=[];totals=[0,0,0,0,0,0,0,0,0,0,0,0]
        for op in gout:
            rows.append([op.get('cluster',''),'',', '.join(op.get('stands',[])),
                         op.get('change',''),op.get('status',''),
                         op.get('walls_replaced',0),op.get('walls_created',0),
                         op.get('walls_deleted',0),op.get('params_written',0),
                         op.get('tags_moved',0),
                         '%d wall(s) kept'%op.get('walls_kept',0)])
        merged=sum(1 for x in gout if x['status'] in ('PLANNED','VERIFIED'))
        skipped=sum(1 for x in gout if x['status']=='SKIPPED')
        totals=[merged,skipped,
                sum(1 for x in gout if x.get('change')=='MOVED'),
                sum(1 for x in gout if x.get('change')=='RESIZED'),
                sum(1 for x in gout if x.get('change')=='ADDED'),
                sum(1 for x in gout if x.get('change')=='REMOVED'),
                sum(x.get('walls_replaced',0) for x in gout),
                sum(x.get('walls_created',0) for x in gout),
                sum(x.get('walls_deleted',0) for x in gout),
                sum(x.get('params_written',0) for x in gout),
                sum(x.get('tags_moved',0) for x in gout),
                sum(1 for x in issues if 'CONFLICT' in x.get('type',''))]
        run_id=os.path.splitext(os.path.basename(rvt))[0]
        write_audit(os.path.dirname(rvt),run_id,rows,totals,'APPLIED' if apply else 'PREVIEW',
                    str(uuid.uuid4()))
    except Exception as ex:
        qa.append('AUDIT TRAIL: '+str(ex))

    hard_fail=sum(1 for x in pout+gout if x['status'] in ('FAILED','VERIFY_FAILED'))
    summary={'Status':'SUCCESS' if hard_fail==0 else 'COMPLETED WITH ERRORS','Mode':'APPLIED' if apply else 'DRY RUN',
             'Clusters':sum(1 for x in gout if x['status'] in ('PLANNED','VERIFIED')),
             'Parameters':sum(1 for x in pout if x['status'] in ('PLANNED','VERIFIED')),
             'Geometry':sum(1 for x in gout if x['status'] in ('PLANNED','VERIFIED')),
             'Skipped':sum(1 for x in pout+gout if x['status']=='SKIPPED'),
             'Errors':hard_fail,
             'Conflicts':sum(1 for x in issues if 'CONFLICT' in x.get('type','')),
             'Redraw link removed':summary_link_removed,
             'Preview':preview_note if preview_note else ('not drawn in apply mode'
                                                          if apply else 'NOT DRAWN'),
             'Undo':('ONE STEP - MVP2.4B6 - Merge Redraw Updates'
                     if apply else 'NOT REQUIRED')}
except Exception as e:
    # Put the reason in the summary as well as the QA list: the summary is the
    # watch that is always visible.
    import traceback
    detail=traceback.format_exc()
    qa.append('FATAL: '+str(e));qa.append(detail)
    summary['Status']='FAILED';summary['Error']=str(e);summary['Trace']=detail
finally:
    # Emergency cleanup only. Normal cleanup happens before Main is edited.
    if cleanup and link_type_id is not None:
        try:
            remove_link_type(doc,link_type_id)
            summary['Redraw link removed']='YES'
        except Exception as e:
            qa.append('CLEANUP FAILED: '+str(e));summary['Redraw link removed']='NO'

OUT=summary,pout,gout,issues,qa
