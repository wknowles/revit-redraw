# -*- coding: utf-8 -*-
"""Compare and Update Redraw - wall level merge with a visual preview.
Revit 2027 | Dynamo 4.0.2 | CPython3/PythonNet3

IN[0] Redraw directory
IN[1] comparison tolerance metres, optional, default 0.0005
IN[2] boundary sample spacing metres, optional, default 0.25
IN[3] APPLY UPDATES boolean, optional, default False
IN[4] REDRAW WINS ON CONFLICTS boolean, optional, default False

OUT[0] Summary
OUT[1] Parameter Updates
OUT[2] Geometry Updates
OUT[3] Conflicts / Skips
OUT[4] QA / Errors

Boundary location is read from the baseline (Center or Finish).

The Redraw is read from its own document, not from a Revit link. Graph 1 writes
the Redraw as a copy of Main, so its internal coordinates are Main's and no
transform is needed; and opening a document - unlike loading a link - does not
clear Revit's undo history. Nothing but the merge transaction edits Main, so
one Ctrl+Z puts Main back exactly where it was.

The merge compares at wall level, not at room level, so a cluster of connected
stands merges as one change. For each cluster of stands that share walls:

  - a Main wall that already sits where a Redraw wall sits is kept;
  - a Main wall with no Redraw counterpart is deleted;
  - a Redraw wall with no Main counterpart is created;
  - a wall whose type, height, offset, flip or level changed is rebuilt;
  - a wall added to Main after the Redraw was cut is left alone.

Stands the Redraw added are created, stands it deleted are deleted, and stands
it moved or resized have their location point and tags moved.

Scope is deliberately controlled:
- only the Closed Wall - RX and Open Wall - RX wall types may be rebuilt
- a boundary that is not made of walls is skipped (Room Separation Lines)
- level mismatches and conflicts are skipped, unless the Redraw wins flag is set
- a sold stand whose area the merge would change refuses the whole run

On the tolerance. It does not limit how closely a rebuilt wall follows the
drawing: a created or replaced wall is built from the Redraw's own Curve object,
so it is exact. The tolerance decides only whether an existing Main wall counts
as "already the same wall" (kept where Main has it) or is replaced, and whether
Main's wall counts as unchanged since the Redraw was cut. Untouched walls are
bit-identical - the Redraw is a copy - so the tolerance only has to separate
identical walls from edited ones. It ships at 0.5 mm for that reason: at the old
10 mm a wall 9 mm out was kept in Main's position, which moved the area.
Curved walls are matched through sampled points (17 per curve) instead of exact
endpoints, so their resolution is coarser, but an unchanged arc still matches
exactly because both sides sample the same geometry.
"""
import clr, os, json, math, re, time, uuid
clr.AddReference('RevitAPI'); clr.AddReference('RevitServices')
from Autodesk.Revit.DB import (Arc, Line, XYZ, Wall, LocationCurve, LocationPoint, BuiltInParameter,
 BuiltInCategory, FilteredElementCollector, LinkElementId, FilePath, OpenOptions,
 Transaction, SubTransaction, TransactionStatus, SpatialElementBoundaryOptions,
 SpatialElementBoundaryLocation, UnitTypeId, UnitUtils, StorageType, ElementId, Level, View,
 Color, CurveLoop, DirectShape, GeometryCreationUtilities, OverrideGraphicSettings, UV,
 FamilyInstance, Opening)
from RevitServices.Persistence import DocumentManager

doc=DocumentManager.Instance.CurrentDBDocument
PARAMS=['Floorplan Hall','Floorplan Pavilion','Floorplan Sector',
        'Obstruction Area','Product Code','Custom Area']
RX_TYPES=('Closed Wall - RX','Open Wall - RX')
# The CRM plugin owns the sold status. It is a Room parameter in Main and it is
# deliberately absent from the Redraw: it is deleted when the model is saved and
# re-imported about two minutes later, so a run made straight after a save can
# find it gone. Only "sold" blocks; anything else (OnHold, Available, a blank)
# does not.
SOLD_PARAM='Status'
SOLD_ONLY='sold'
# The smallest area drift treated as a change, and the floor of the derived
# allowance. A rebuild that keeps a wall within the tolerance can move the area
# by about perimeter x tolerance, so the allowance is derived, not guessed.
AREA_FLOOR_M2=0.001

class Refused(Exception):
    """A business rule refused the run.

    Not a crash, so the summary says REFUSED and no traceback is shown: the
    operator has a modelling or timing decision to make, not a bug to report.
    """

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

def state_flat(state):
    """The boundary points of a state, in the shape merge_core compares.

    Computed once per state and cached on it, so a stand that is compared
    against the baseline, Main and the Redraw is flattened once.
    """
    cached=state.get('points_flat')
    if cached is not None:return cached
    points=[]
    for loop in state.get('loops',[]):
        for curve in loop:
            try:points.extend(wall_points(curve)[0])
            except Exception:pass
    cached=boundary_points(points)
    state['points_flat']=cached
    return cached

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
        level_name=None
        try:
            level=d.GetElement(e.LevelId)
            level_name=level.Name if level else None
        except: pass
        return {'type':wt.Name if wt else None,'type_id':eidv(e.GetTypeId()),'level_id':eidv(e.LevelId),
                'level_name':level_name,
                'height':height,'offset':offset,'flipped':bool(e.Flipped),'element_id':eidv(e.Id),
                'curve':lc.Curve if isinstance(lc,LocationCurve) else seg.GetCurve()}
    except:return None

def text_param(room,name):
    """One parameter as text, or None when it is not there.

    None means the parameter is absent from the Room, which is how a missing
    Status is told apart from a blank one.
    """
    p=room.LookupParameter(name)
    if p is None:return None
    try:
        if not p.HasValue:return ''
    except Exception:pass
    try:
        v=p.AsString()
        if v is None:v=p.AsValueString()
        return '' if v is None else v
    except Exception:return ''

def status_of(room):
    """The sold status of a Room, normalised, or None when there is no Status."""
    raw=text_param(room,SOLD_PARAM)
    if raw is None:return None
    return ''.join(ch for ch in raw.strip().casefold() if ch not in ' _-')

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

def state(room,tr=None,d=None,boundary_location='Finish',wall_cache=None):
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
          'area':area,'loops':[],'boundary_ids':[],'wall_loops':[],'room_point':None,
          'status':status_of(room)}
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
                # A wall shared by two stands is described once and reused.
                # The cache is only safe without a transform, because the
                # transformed curve would be shared as well.
                wd=None
                if wall_cache is not None and tr is None:
                    key=eidv(seg.ElementId)
                    if key in wall_cache:wd=wall_cache[key]
                    else:
                        wd=wall_desc(d,seg);wall_cache[key]=wd
                else:
                    wd=wall_desc(d,seg)
                if wd is not None:
                    if tr is not None: wd['curve']=wd['curve'].CreateTransformed(tr)
                    walls.append(wd)
        loops.append(curves);wall_loops.append(walls)
    base.update({'placement_status':'PLACED','has_geometry':True,'loops':loops,
                 'centroid':centroid(loops),'boundary_ids':ids,'wall_loops':wall_loops,'room_point':rp})
    return base

def collect(d,tr,qa,label,boundary_location='Finish',only=None):
    out={}
    wall_cache={} if tr is None else None
    rooms=FilteredElementCollector(d).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType().ToElements()
    for room in rooms:
        n=(room.Number or '').strip()
        if not n:continue
        if only is not None and n not in only:continue
        if n in out:qa.append(label+' '+n+': duplicate Room Number');continue
        try:out[n]=state(room,tr,d,boundary_location,wall_cache)
        except Exception as e:qa.append(label+' '+n+': '+str(e))
    return out

def collect_exclusions(d,qa,label,boundary_location='Finish'):
    """The walls of the Rooms the merge cannot manage.

    collect() skips a Room with a blank Number and quarantines a duplicate
    Number, so those Rooms are in no state and their walls look unused. A wall
    they share with a stand would then be deleted, which puts both Rooms in one
    region - exactly what the shared-wall guard exists to prevent.
    """
    opt=SpatialElementBoundaryOptions()
    opt.SpatialElementBoundaryLocation=SpatialElementBoundaryLocation.Center if boundary_location=='Center' else SpatialElementBoundaryLocation.Finish
    rooms=FilteredElementCollector(d).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType().ToElements()
    by_number={}
    for room in rooms:
        by_number.setdefault((room.Number or '').strip(),[]).append(room)
    protected=set();duplicates=set()
    for n,group in sorted(by_number.items()):
        if n and len(group)==1:continue
        if n:
            duplicates.add(n)
            qa.append(label+' '+n+': duplicate Room Number; the Room is not merged and '
                      'its walls are protected.')
        else:
            for room in group:
                qa.append(label+' Room element '+str(eidv(room.Id))+': blank Room Number; '
                          'the Room is not merged and its walls are protected.')
        # Only the unmanaged Rooms are read for boundaries: the managed ones
        # were read by collect() already, and there can be thousands of them.
        for room in group:
            try:segloops=room.GetBoundarySegments(opt)
            except:segloops=None
            for sl in segloops or []:
                for seg in sl:
                    try:wid=eidv(seg.ElementId)
                    except:continue
                    if wid>0:protected.add(wid)
    return protected,duplicates

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
    move=dst(a['centroid'],b['centroid'])
    # The Redraw is a copy of Main, so a stand nobody touched has exactly the
    # same boundary points. Comparing those is a handful of float comparisons;
    # sampling every curve through the Revit API is only needed when the
    # boundary really differs. Empty point lists keep the old behaviour and go
    # through the sampled comparison.
    flat_a=state_flat(a);flat_b=state_flat(b)
    if flat_a and flat_b and same_boundary(flat_a,flat_b):
        delta=0.0
    else:
        delta=bd(a['loops'],b['loops'],sp)
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

def check_inputs(folder,tol,sp):
    """Validate the raw Directory and slider inputs.

    Returns (folder, tolerance, spacing, warnings). The sliders allow 0.0,
    which divides by zero in the boundary sampling, and a blank Directory
    reaches the path handling as None. Both are reported by name instead of as
    an internal error.
    """
    text='' if folder is None else str(folder).strip()
    if not text:
        raise Exception('Select the Redraw directory (the folder that holds one .rvt and its .baseline.json).')
    try: tol=float(tol)
    except Exception: raise Exception('The comparison tolerance must be a number of metres.')
    try: sp=float(sp)
    except Exception: raise Exception('The boundary sample spacing must be a number of metres.')
    if not math.isfinite(tol) or tol<0:
        raise Exception('The comparison tolerance must be zero or a positive number of metres.')
    if not math.isfinite(sp) or sp<=0:
        raise Exception('The boundary sample spacing must be a positive number of metres.')
    warnings=[]
    # The shipped default is 0.25 m: do not warn on every default run, only
    # when the operator makes the sampling coarser than that (or than the
    # tolerance already demands).
    if tol>0 and sp>max(tol*8,0.25):
        warnings.append('Boundary sample spacing %.3g m is much larger than the comparison '
                        'tolerance %.3g m; features shorter than the spacing may be missed.'
                        %(sp,tol))
    return text,tol,sp,warnings

def files(folder):
    if not folder or not os.path.isdir(folder):raise Exception('Redraw directory is invalid')
    ns=os.listdir(folder)
    rv=[os.path.abspath(os.path.join(folder,n)) for n in ns if n.lower().endswith('.rvt') and not re.search(r'\.\d{4}\.rvt$',n,re.I) and os.path.isfile(os.path.join(folder,n))]
    js=[os.path.abspath(os.path.join(folder,n)) for n in ns if n.lower().endswith('.baseline.json') and os.path.isfile(os.path.join(folder,n))]
    if len(rv)!=1:raise Exception('Redraw directory must contain one primary RVT')
    if len(js)!=1:raise Exception('Redraw directory must contain one baseline JSON')
    return rv[0],js[0]

def same_path(a,b):
    """True when two path strings name the same file."""
    try:
        return bool(a) and bool(b) and os.path.normcase(os.path.abspath(a))==os.path.normcase(os.path.abspath(b))
    except Exception:
        return False


def open_redraw(doc,path):
    """The Redraw document, opened for reading.

    Graph 1 writes the Redraw as a copy of Main, so its internal coordinates
    are Main's and no transform is needed. Opening a document does not clear
    Revit's undo history the way loading a link does, and it leaves nothing
    behind in Main, so the run's own changes are the only thing to undo.
    """
    try:
        return doc.Application.OpenDocumentFile(FilePath(path),OpenOptions())
    except Exception as ex:
        raise Exception('Could not open the Redraw for reading: '+str(ex))


def activate_main(doc,qa):
    """Bring Main back to the foreground after the Redraw was opened.

    Opening a document makes it the active one, and the merge edits Main's
    views, so Main goes back first. Best effort: if Main has never been saved
    there is no path to activate and the QA says so.
    """
    try:
        if not doc.PathName:
            qa.append('Main has no saved path, so it could not be brought back to the foreground.')
            return
        ui=DocumentManager.Instance.CurrentUIApplication
        if ui is None:
            return
        active=None
        try:
            active=ui.ActiveUIDocument.Document if ui.ActiveUIDocument else None
        except Exception:
            active=None
        if active is not None and same_path(active.PathName,doc.PathName):
            return
        ui.OpenAndActivateDocument(FilePath(doc.PathName),OpenOptions(),False)
    except Exception as ex:
        qa.append('Could not bring Main back to the foreground: '+str(ex))


def close_redraw(doc,rdoc,qa):
    """Close the Redraw document again. True when it closed."""
    closed=True
    try:
        rdoc.Close(False)
    except Exception as ex:
        closed=False
        qa.append('Could not close the Redraw document: '+str(ex))
    activate_main(doc,qa)
    return closed


_WALL_TYPES={}

def wall_types(host):
    """Every wall type in a document, collected once per run.

    Creating a wall used to collect them all again for every wall, which in a
    merge that creates thousands of walls is thousands of full collections.
    """
    key=id(host)
    found=_WALL_TYPES.get(key)
    if found is None:
        found={w.Name:w for w in FilteredElementCollector(host).OfClass(__import__('Autodesk.Revit.DB',fromlist=['WallType']).WallType).ToElements()}
        _WALL_TYPES[key]=found
    return found


def move_room_to_redraw(main_room,redraw):
    target=redraw.get('room_point') or redraw.get('centroid')
    if target is None: raise Exception('Redraw Room has no usable location point')
    loc=main_room.Location
    if not isinstance(loc,LocationPoint): raise Exception('Main Room has no movable location point')
    current=loc.Point
    delta=XYZ(target.X-current.X,target.Y-current.Y,0.0)
    if delta.GetLength()>1e-9 and not loc.Move(delta):
        raise Exception('The Main Room could not be moved (Revit refused the move to %.3f, %.3f).'
                        %(target.X,target.Y))
    return target

_ROOM_TAGS={}

def room_tag_index(host):
    """Room element id -> the ids of the tags that point at it.

    Built once per run. This used to be one full tag collection - and one
    Revit call per tag - for every moved stand, which is quadratic on a model
    with thousands of stands and thousands of tags.
    """
    key=id(host)
    index=_ROOM_TAGS.get(key)
    if index is not None:return index
    index={}
    try:
        tags=FilteredElementCollector(host).OfCategory(BuiltInCategory.OST_RoomTags).WhereElementIsNotElementType().ToElements()
    except Exception:
        _ROOM_TAGS[key]=index
        return index
    for tag in tags:
        room_id=None
        try:
            tagged=tag.Room
            if tagged is not None:room_id=eidv(tagged.Id)
        except Exception:
            room_id=None
        if room_id is None:
            try:room_id=eidv(tag.TaggedLocalRoomId)
            except Exception:room_id=None
        if room_id is not None:index.setdefault(room_id,[]).append(tag.Id)
    _ROOM_TAGS[key]=index
    return index

def room_tag_ids(host,room):
    """The tags that point at a Room, in every view that has tags."""
    try:return list(room_tag_index(host).get(eidv(room.Id),()))
    except Exception:return []

def room_tag_templates(host):
    """The views that carry Room Tags, and the tag type each of them uses.

    A new stand's tag has to look like the tags already in the model, so both
    the views and the types come from the tags that are already there.
    """
    templates={}
    try:
        tags=FilteredElementCollector(host).OfCategory(BuiltInCategory.OST_RoomTags).WhereElementIsNotElementType().ToElements()
    except Exception:
        return templates
    for tag in tags:
        try:
            view_id=eidv(tag.OwnerViewId)
            if view_id<0:continue
            type_id=eidv(tag.GetTypeId())
        except Exception:
            continue
        entry=templates.setdefault(view_id,{'type_id':None,'count':0})
        entry['count']+=1
        if entry['type_id'] is None:entry['type_id']=type_id
    return templates

def create_room_tags(host,room,templates,point,qa,label):
    """Tag a Room the way the model already tags its stands.

    One tag per view that already carries Room Tags, with the type that view
    uses, on the Room's location point - where the moved tags are centred too.
    With no Room Tags in the model at all, the active view gets one with the
    default type. A view that refuses the tag is reported, not fatal.
    """
    views=sorted(templates)
    if not views:
        view=host.ActiveView
        if view is not None:views=[eidv(view.Id)]
    created=0;errors=[]
    for view_id in views:
        try:
            tag=host.Create.NewRoomTag(LinkElementId(room.Id),UV(point.X,point.Y),ElementId(view_id))
            if tag is None:
                errors.append('view %d: Revit returned no tag'%view_id);continue
            wanted=(templates.get(view_id) or {}).get('type_id')
            if wanted is not None and eidv(tag.GetTypeId())!=wanted:
                try:tag.ChangeTypeId(ElementId(wanted))
                except Exception as ex:errors.append('view %d: the tag type could not be matched (%s)'%(view_id,ex))
            created+=1
        except Exception as ex:
            errors.append('view %d: %s'%(view_id,ex))
    if errors:
        qa.append('%s: %d of %d room tag(s) could not be created (%s)'
                  %(label,len(errors),len(views),'; '.join(errors)))
    return created

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
PREVIEW_COLOURS = (PREVIEW_BLUE, PREVIEW_AMBER, PREVIEW_RED, PREVIEW_SKIPPED)


def preview_marker(view_id, wall_ids):
    """The name (and comment) a preview band carries, so a later run can find
    it again: the view it was drawn in and the walls it recoloured."""
    return PREVIEW_MARK + ' v=%d w=%s' % (view_id, ','.join(str(w) for w in wall_ids))


def parse_preview_marker(text):
    """The view id and wall ids stored in a preview marker.

    Older markers carried only the wall ids, so both may come back empty; the
    override sweep then finds what the marker cannot name.
    """
    view = None
    walls = []
    for token in text.split():
        if token.startswith('v='):
            try:
                view = int(token[2:])
            except Exception:
                pass
        elif token.startswith('w='):
            for part in token[2:].split(','):
                if part.isdigit():
                    walls.append(int(part))
    return view, walls


def override_is_preview(style):
    """True when a view override paints in one of the preview colours."""
    for rgb in PREVIEW_COLOURS:
        for getter in ('SurfaceForegroundPatternColor', 'CutForegroundPatternColor'):
            try:
                colour = getattr(style, getter)
                if colour.Red == rgb[0] and colour.Green == rgb[1] and colour.Blue == rgb[2]:
                    return True
            except Exception:
                pass
    return False


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
    type, height, offset, flip and level."""
    return (wd.get('type'), wd.get('height'), wd.get('offset'),
            bool(wd.get('flipped')), wd.get('level_name'))


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


def state_attribute_set(state):
    """The attributes of every wall bounding a state, sorted.

    Two states whose boundaries match geometrically can still differ: a wall
    can keep its position while its type, height, offset, flip or level
    changes. Comparing these sets catches that without moving anything.
    """
    if state is None:
        return ()
    try:
        attributes = [wall_attributes(wd) for wd in state_walls(state).values()]
        # repr keeps the sort total even if one wall is missing a value.
        return tuple(sorted(attributes, key=repr))
    except Exception:
        return ()


def collect_walls(doc, tr=None):
    """Every wall in a document, not only the ones that bound a room.

    Deleting a stand in the Redraw leaves its walls behind as orphans: they
    are still walls, they simply no longer bound a room. Without this the
    merge would think the Redraw deleted them and take them out of Main.

    The wall type is resolved once per type id, not once per wall.
    """
    out = {}
    try:
        walls = FilteredElementCollector(doc).OfClass(Wall).ToElements()
    except Exception:
        return out
    types = {}
    for wall in walls:
        try:
            type_id = eidv(wall.GetTypeId())
            if type_id in types:
                type_name = types[type_id]
            else:
                try:
                    wall_type = doc.GetElement(wall.GetTypeId())
                    type_name = wall_type.Name if wall_type else None
                except Exception:
                    type_name = None
                types[type_id] = type_name
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
            level_name = None
            try:
                level = doc.GetElement(wall.LevelId)
                level_name = level.Name if level else None
            except Exception:
                pass
            out[eidv(wall.Id)] = {'type': type_name, 'type_id': None,
                                  'level_id': eidv(wall.LevelId), 'level_name': level_name,
                                  'height': height,
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
    BOTH_REMOVED  Main and the Redraw both dropped a stand the baseline had.
    """
    if redraw is None:
        if main is None:
            return 'BOTH_REMOVED'
        if base is not None:
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
    # Main first: if it already is what the Redraw wants, nothing else matters
    # and the two baseline comparisons can be skipped.
    mr = geom(main, redraw, tol, sp)
    if mr == 'UNCHANGED':
        # The boundary is the same, but the Redraw may have changed a wall's
        # type, height, offset, flip or level without moving it. That is still
        # a change, and plan_component will replace the wall.
        if state_attribute_set(main) == state_attribute_set(redraw):
            return 'ALREADY'
        return 'AFFECTED'
    gm = geom(base, main, tol, sp) if base is not None else 'ADDED'
    gr = geom(base, redraw, tol, sp) if base is not None else 'ADDED'
    if gr == 'UNCHANGED':
        return 'UNCHANGED'
    if gm != 'UNCHANGED':
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


def component_level(component, main, redraw, host):
    """The Main level a component's walls are built on.

    Prefer the level of a stand that is already in Main. A cluster of stands
    that are all new in the Redraw has none, so take the level from the
    Redraw's level name instead.
    """
    for number in component:
        state = main.get(number)
        if state is not None and state.get('level_id') is not None:
            return ElementId(state['level_id'])
    for number in component:
        state = redraw.get(number)
        if state is None:
            continue
        level = find_level_by_name(host, state.get('level_name'))
        if level is not None:
            return level.Id
    return None


_LEVELS={}

def find_level_by_name(host, name):
    """The Main level with this name, or None.

    The level list is collected once per run: it cannot change during a merge,
    and it used to be collected for every cluster and every added stand.
    """
    if not name:
        return None
    key=id(host)
    levels=_LEVELS.get(key)
    if levels is None:
        try:
            levels=FilteredElementCollector(host).OfClass(Level).ToElements()
        except Exception:
            return None
        _LEVELS[key]=levels
    for level in levels:
        try:
            if level.Name == name:
                return level
        except Exception:
            pass
    return None


def hosted_dependents(host, wall_id):
    """The elements a wall hosts that deleting it would destroy.

    Only hosted inserts and openings count: they are the ones Revit cascades
    with the wall, and the merge has no way to put them back.
    """
    try:
        key = wall_id if isinstance(wall_id, ElementId) else ElementId(int(wall_id))
        wall = host.GetElement(key)
    except Exception:
        return []
    if wall is None:
        return []
    try:
        ids = wall.GetDependentElements(None)
    except Exception:
        return []
    out = []
    for element_id in ids or []:
        try:
            element = host.GetElement(element_id)
        except Exception:
            continue
        if not isinstance(element, (FamilyInstance, Opening)):
            continue
        try:
            name = element.Category.Name if element.Category else None
        except Exception:
            name = None
        try:
            key = eidv(element_id)
        except Exception:
            key = int(element_id)
        out.append((key, name or type(element).__name__))
    return out


def create_wall(host, level_id, wd):
    """Create one wall in Main from a Redraw wall description.

    The wall's own level wins: a Redraw wall moved to another level is rebuilt
    on that level, not on the cluster's. The cluster level is only the fallback
    for a wall whose level name could not be read.
    """
    types = wall_types(host)
    wall_type = types.get(wd['type'])
    if wall_type is None:
        raise Exception('Wall type not found in Main: ' + str(wd['type']))
    level = find_level_by_name(host, wd.get('level_name'))
    if level is not None:
        level_id = level.Id
    if level_id is None:
        raise Exception('No level to build the wall on')
    if not isinstance(level_id, ElementId):
        level_id = ElementId(int(level_id))
    height = wd.get('height')
    if height is None or height <= 0:
        height = 10.0
    wall = Wall.Create(host, wd['curve'], wall_type.Id, level_id, height,
                       float(wd.get('offset') or 0.0), bool(wd.get('flipped', False)), False)
    return eidv(wall.Id)


def create_room(host, level, redraw_state, number, parameter_values):
    """Create a stand that the Redraw added."""
    candidates = []
    for source in ('room_point', 'centroid'):
        point = redraw_state.get(source)
        if point is not None:
            candidates.append(point)
    if not candidates:
        raise Exception('Redraw Room has no usable location point')
    room = None
    problems = []
    for point in candidates:
        try:
            room = host.Create.NewRoom(level, UV(point.X, point.Y))
        except Exception as ex:
            problems.append(str(ex))
            continue
        if room is None:
            problems.append('NewRoom returned nothing')
            continue
        break
    if room is None:
        raise Exception('The new stand is not in an enclosed region (%s)'
                        % ('; '.join(problems) if problems else 'unknown'))
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
        if delta.GetLength() > 1e-9 and not location.Move(delta):
            raise Exception('The new Room could not be moved (Revit refused the move to %.3f, %.3f).'
                            %(point.X,point.Y))
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


def clear_preview(host, qa):
    """Remove the preview left by an earlier run, wherever it was drawn.

    A band carries the marker in its name (and comment), so it is found even
    when its colour override is gone. Then every view is swept for elements
    that still paint in a preview colour: an unmarked band from an older run is
    deleted, a recoloured wall is reset. Nothing can hide.

    Returns {'elements': n, 'overrides': m}.
    """
    counts = {'elements': 0, 'overrides': 0}
    marked = []
    try:
        candidates = FilteredElementCollector(host).OfCategory(BuiltInCategory.OST_GenericAnnotation) \
            .WhereElementIsNotElementType().ToElements()
    except Exception as ex:
        qa.append('PREVIEW: could not scan for the preview (%s)' % ex)
        return counts
    for element in candidates:
        text = None
        try:
            name = element.Name
            if isinstance(name, str) and name.startswith(PREVIEW_MARK):
                text = name
        except Exception:
            pass
        if text is None:
            try:
                parameter = element.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                if parameter is not None and parameter.StorageType == StorageType.String:
                    value = parameter.AsString()
                    if value and value.startswith(PREVIEW_MARK):
                        text = value
            except Exception:
                pass
        if text is not None:
            marked.append(element.Id)
    for element_id in marked:
        try:
            host.Delete(element_id)
            counts['elements'] += 1
        except Exception as ex:
            qa.append('PREVIEW: could not delete a preview band (%s)' % ex)
    try:
        views = FilteredElementCollector(host).OfClass(View).ToElements()
    except Exception as ex:
        qa.append('PREVIEW: could not sweep the views for leftovers (%s)' % ex)
        return counts
    for view in views:
        try:
            overrides = view.GetElementOverrides()
        except Exception:
            continue
        to_delete = []
        to_reset = []
        for element_id in list(overrides.Keys):
            try:
                if not override_is_preview(overrides[element_id]):
                    continue
                element = host.GetElement(element_id)
            except Exception:
                continue
            if element is None:
                continue
            if isinstance(element, DirectShape):
                to_delete.append(element_id)
            else:
                to_reset.append(element_id)
        for element_id in to_delete:
            try:
                host.Delete(element_id)
                counts['elements'] += 1
            except Exception as ex:
                qa.append('PREVIEW: could not delete a leftover band (%s)' % ex)
        for element_id in to_reset:
            try:
                view.SetElementOverrides(element_id, OverrideGraphicSettings())
                counts['overrides'] += 1
            except Exception as ex:
                qa.append('PREVIEW: could not reset an override (%s)' % ex)
    return counts


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
        marker = preview_marker(eidv(view.Id), coloured.get(rgb, []))
        try:
            shape.SetName(marker)
        except Exception:
            qa.append('PREVIEW: could not mark a preview element; it may need deleting by hand')
        try:
            comment = shape.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if comment is not None and comment.StorageType == StorageType.String:
                comment.Set(marker)
        except Exception:
            pass
    return drawn, recoloured


AUDIT_PREFIX = ['run_utc', 'run_id', 'mode']
AUDIT_LOG_COLUMNS = ['cluster', 'stands', 'change', 'status', 'walls_replaced',
                     'walls_created', 'walls_deleted', 'params_written', 'tags_moved',
                     'details']
AUDIT_SUMMARY_COLUMNS = ['clusters_merged', 'clusters_skipped', 'clusters_moved',
                         'clusters_resized', 'clusters_added', 'clusters_removed',
                         'walls_replaced', 'walls_created', 'walls_deleted',
                         'params_written', 'tags_moved', 'conflicts']


def audit_rows(pout, gout):
    """The log rows for this run: one per cluster, then one per parameter.

    `write_audit` prepends the stamp, run id and mode, so a row here has
    exactly the fields of AUDIT_LOG_COLUMNS.
    """
    rows=[]
    for op in gout:
        details='%d wall(s) kept'%op.get('walls_kept',0)
        if op.get('reason'):details+='; '+op['reason']
        rows.append([op.get('cluster',''),', '.join(op.get('stands',[])),
                     op.get('change','NONE'),op.get('status',''),
                     op.get('walls_replaced',0),op.get('walls_created',0),
                     op.get('walls_deleted',0),op.get('params_written',0),
                     op.get('tags_moved',0),details])
    for op in pout:
        written=op.get('status') in ('PLANNED','APPLIED','VERIFIED')
        # The value the run sets, or what Main actually holds after a failure.
        if op.get('status') in ('VERIFIED','VERIFY_FAILED','FAILED'):
            after=op.get('main_after')
        else:
            after=op.get('redraw')
        rows.append(['',op.get('stand',''),'PARAMETER',op.get('status',''),
                     0,0,0,1 if written else 0,0,
                     '%s: %s -> %s'%(op.get('parameter'),op.get('main_before'),after)])
    return rows


def audit_totals(pout, gout, issues):
    """The summary totals, in the order of AUDIT_SUMMARY_COLUMNS."""
    changes=[op.get('change','NONE') for op in gout]
    return [sum(1 for x in gout if x.get('status') in ('PLANNED','APPLIED','VERIFIED')),
            sum(1 for x in gout if x.get('status')=='SKIPPED'),
            changes.count('MOVED'),changes.count('RESIZED'),
            changes.count('ADDED'),changes.count('REMOVED'),
            sum(x.get('walls_replaced',0) for x in gout),
            sum(x.get('walls_created',0) for x in gout),
            sum(x.get('walls_deleted',0) for x in gout),
            sum(1 for x in pout if x.get('status') in ('PLANNED','APPLIED','VERIFIED')),
            sum(x.get('tags_moved',0) for x in gout),
            sum(1 for x in issues if 'CONFLICT' in x.get('type',''))]


def write_audit(folder, base_name, rows, totals, mode, run_id):
    """Append this run to the CSV audit trail next to the Redraw.

    Returns (True, None) on success, or (False, message). A failed audit must
    not look like a successful one, so the caller reports the message.
    """
    import csv
    import datetime
    stamp = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    log_path = os.path.join(folder, base_name + '.merge-log.csv')
    summary_path = os.path.join(folder, base_name + '.merge-summary.csv')
    new_file = not os.path.isfile(log_path)
    try:
        with open(log_path, 'a', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            if new_file:
                writer.writerow(AUDIT_PREFIX + AUDIT_LOG_COLUMNS)
            for row in rows:
                writer.writerow([stamp, run_id, mode] + row)
        with open(summary_path, 'w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow(AUDIT_PREFIX + AUDIT_SUMMARY_COLUMNS)
            writer.writerow([stamp, run_id, mode] + totals)
        return True,None
    except Exception as ex:
        return False,str(ex)

def where(state):
    """A short description of where a stand is, for the QA log."""
    if state is None:
        return 'absent'
    return '%s, %.0f sqft, %d boundary wall(s)' % (
        'placed' if state.get('has_geometry') else 'NO BOUNDARY',
        state.get('area') or 0.0,
        sum(len(loop) for loop in state.get('wall_loops', [])))


def cluster_change(statuses, numbers, main=None, redraw=None, tol=0.0):
    """What a cluster does, from the statuses of the stands it touches.

    A conflicted stand is merged like any other changed stand when the Redraw
    wins. A cluster with no added or removed stand is classified by where the
    Rooms went: if any Room's location point moved by more than `tol` it is
    MOVED, otherwise the boundary changed around a fixed point and it is
    RESIZED. Without the states (or without location points) it is MOVED.
    """
    kinds=set('AFFECTED' if statuses.get(n)=='CONFLICT' else statuses.get(n)
              for n in numbers)
    if kinds=={'ADDED'}:return 'ADDED'
    if kinds=={'REMOVED'}:return 'REMOVED'
    if len(kinds)>1:return 'MIXED'
    if main is not None and redraw is not None:
        for n in numbers:
            before=main.get(n);after=redraw.get(n)
            if before is None or after is None:continue
            a=before.get('room_point');b=after.get('room_point')
            if a is not None and b is not None and dst(a,b)>tol:return 'MOVED'
        return 'RESIZED'
    return 'MOVED'


def plan_clusters(b, m, r, r_walls, tol, sp, redraw_wins, qa, issues, protected=None, duplicates=None):
    """Plan the merge, one cluster at a time.

    Returns (geometry plan, parameter plan). The geometry plan has one entry
    per affected cluster; each entry carries the walls to keep, replace, delete
    and create, and the room work to do. `protected` holds the walls of Rooms
    the merge does not manage and `duplicates` the duplicate Room Numbers.
    """
    protected=protected or set();duplicates=duplicates or set()
    # how every stand compares across baseline, Main and Redraw
    statuses={}
    for n in sorted(set(b)|set(m)|set(r)):
        statuses[n]=stand_status(b.get(n),m.get(n),r.get(n),tol,sp)
        if statuses[n]=='MAIN_REMOVED':
            # Nothing in the merge touches it, but Main has lost a stand the
            # Redraw still draws, so the operator must hear about it.
            issues.append({'stand':n,'type':'MAIN REMOVED',
                           'reason':'Main no longer has this stand but the Redraw does'})

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
    # A protected wall has a second, unmanaged owner, so the shared-wall guard
    # below sees that deleting it would join it to that Room.
    for wid in protected:
        wall_stands.setdefault(wid,set()).add('#unmanaged')

    gplan=[];pplan=[];comp_no=0
    # The Redraw's whole wall inventory is bucketed once: the same buckets are
    # reused for every cluster. Building them per cluster is quadratic in the
    # number of stands.
    if r_walls:
        orphan_cell=cell_size(tol)
        orphan_buckets=build_buckets(r_walls,orphan_cell)
    else:
        orphan_cell=cell_size(tol);orphan_buckets={}
    for comp in components:
        affected=[n for n in comp if statuses.get(n) in ('AFFECTED','ADDED','REMOVED')]
        conflicted=[n for n in comp if statuses.get(n)=='CONFLICT']
        if not affected and not conflicted:continue
        comp_no+=1;cid='C%d'%comp_no
        op={'cluster':cid,'stands':comp,'affected':affected,'status':'PLANNED',
            'change':cluster_change(statuses,affected+conflicted,m,r,tol)}
        dupes=sorted(set(comp)&duplicates)
        if dupes:
            reason=('Duplicate Room Number(s) in Main or the Redraw: %s. A Number must be '
                    'unique before this cluster can be merged.'%(', '.join(dupes)))
            issues.append({'stand':dupes[0],'cluster':cid,'type':'DUPLICATE ROOM NUMBER',
                           'reason':reason})
            op['status']='SKIPPED';op['reason']=reason
            gplan.append(op);continue
        blocked=None
        if conflicted and not redraw_wins:
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
        # The level gate runs after the Redraw-wins handling, so a stand that
        # won its conflict is checked like any other.
        blocked=None
        for n in affected:
            reason=level_mismatch(b.get(n),m.get(n),r.get(n))
            if reason:blocked=reason;break
        if blocked is not None:
            op['status']='SKIPPED';op['reason']=blocked
            for n in comp:
                qa.append('CLUSTER %s stand %s: baseline=%s | Main=%s | Redraw=%s'
                          %(cid,n,where(b.get(n)),where(m.get(n)),where(r.get(n))))
            gplan.append(op);continue
        mw={};rw={};bw=[]
        # Every stand of the cluster contributes its walls, not just the affected
        # ones. A wall shared with an unchanged neighbour already exists in Main,
        # so matching only against the affected walls would plan it as a create
        # and leave a duplicate on top of it.
        for n in comp:
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
            for record in bw:
                for index in find_matches(record,r_walls,orphan_buckets,tol,sp,orphan_cell):
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
        if redraw_wins and conflicted and plan['retained']:
            # A stand that exists in both documents but shares no wall element
            # id with its Redraw copy was *re-created* there, not edited: the
            # designer made a new stand with the same Number somewhere else.
            # Main's version of that stand is the old one, so its walls go too.
            # A shared id means the same wall lineage, and a wall Main added
            # after the cut is still kept (P2).
            recreated=set()
            for n in conflicted:
                if n in m and n in r:
                    if not (set(state_walls(m[n])) & set(state_walls(r[n]))):
                        recreated.add(n)
            if recreated:
                keep=[]
                for wall,reason in plan['retained']:
                    owners=wall_stands.get(wall['ref']['element_id'],set())
                    if owners and owners<=recreated:
                        plan['delete'].append(wall)
                    else:
                        keep.append((wall,reason))
                plan['retained']=keep
        # Business rules: the boundary must be made of walls, and only the RX
        # wall types may be rebuilt.
        guard=None
        def still_occupies(number):
            """False when the stand no longer sits on its side of the wall.

            A stand the Redraw deleted is gone; a stand whose Room moved away
            (E36-60 leaving the cluster, say) has vacated that side too, so a
            wall it shared is no longer separating two occupied regions.
            """
            if statuses.get(number)=='REMOVED':
                return False
            before=m.get(number);after=r.get(number)
            if before is not None and after is not None:
                a=before.get('room_point');b=after.get('room_point')
                if a is not None and b is not None and dst(a,b)>tol:
                    return False
            return True
        # A replacement deletes the Main wall before the Redraw's version is
        # created, so it needs the same guard as a plain delete.
        touched_main=list(plan['delete'])+[wall for wall,_ in plan['replace']]
        for wall in touched_main:
            owners=wall_stands.get(wall['ref']['element_id'],set())
            staying=sorted([n for n in owners if n=='#unmanaged' or still_occupies(n)])
            if len(staying)>1 or '#unmanaged' in staying:
                # The wall separates two stands that both keep a room, or a
                # stand and a Room the merge does not manage, so removing it
                # would put both rooms in one region.
                names=[n for n in staying if n!='#unmanaged']
                if '#unmanaged' in staying:
                    if names:
                        guard=('The Redraw removes a wall that still separates %s and a Room '
                               'the merge does not manage, so they would end up in one region. '
                               'Delete the stand in the Redraw, or leave the wall in the Redraw '
                               'too.'%(', '.join(names)))
                    else:
                        guard=('The Redraw removes a wall that also bounds a Room the merge does '
                               'not manage, so it would end up in one region with its neighbour. '
                               'Leave the wall in the Redraw too.')
                else:
                    guard=('The Redraw removes a wall that still separates %s, so they would '
                           'end up in one region. Delete one of them in the Redraw, or leave '
                           'the wall in the Redraw too.'%(', '.join(names)))
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
        # Preflight. The apply loop deletes before it creates, so a cluster that
        # is certain to fail part way through must be skipped here instead: the
        # wall type has to exist in Main, and the build level has to resolve.
        if guard is None:
            types=wall_types(doc)
            for wall in plan['create']+[w for _,w in plan['replace']]:
                name=wall['ref'].get('type')
                if name not in types:
                    guard=('Wall type not found in Main: %s. Create that wall type in Main, '
                           'or use a wall type Main already has.'%name)
                    break
        if guard is None:
            for wall in plan['create']+[w for _,w in plan['replace']]:
                name=wall['ref'].get('level_name')
                if name and find_level_by_name(doc,name) is None:
                    guard=('Level not found in Main: %s. Create that level in Main, or put '
                           'the stand on a level Main already has.'%name)
                    break
        if guard is None:
            for wall in touched_main:
                hosted=hosted_dependents(doc,wall['ref']['element_id'])
                if hosted:
                    guard=('Main wall %s hosts %s. This stand\'s walls host '
                           'doors/windows/openings; remove them in the Redraw or handle '
                           'them by hand.'
                           %(wall['ref']['element_id'],
                             ', '.join('%s element %s'%(category,element_id)
                                       for element_id,category in hosted)))
                    break
        level=component_level(affected,m,r,doc)
        if guard is None and (level is None or eidv(level)<=0):
            guard=('No level in Main for this cluster. Put the stand on a level that exists '
                   'in Main, or add the missing level to Main.')
        if guard is not None:
            op['status']='SKIPPED';op['reason']=guard
            for n in comp:
                qa.append('CLUSTER %s stand %s: baseline=%s | Main=%s | Redraw=%s'
                          %(cid,n,where(b.get(n)),where(m.get(n)),where(r.get(n))))
            gplan.append(op);continue
        op['_plan']=plan
        op['_level']=level
        op['_want']={n:state_loop_points(r[n]) for n in affected if n in r and r[n].get('loops')}
        rooms=[]
        for n in affected:
            kind=statuses.get(n)
            if kind=='ADDED':rooms.append(('ADD',n))
            elif kind=='REMOVED':rooms.append(('DELETE',n))
            else:rooms.append(('MOVE',n))
        op['_rooms']=rooms
        op['walls_kept']=len(plan['keep']);op['walls_replaced']=len(plan['replace'])
        op['walls_created']=len(plan['create']);op['walls_deleted']=len(plan['delete'])
        gplan.append(op)

    return gplan,pplan


PRIVATE_KEYS=('_plan','_rooms','_level','_want','_target')

# --------------------------------------------------------------------------
# sold stands
# --------------------------------------------------------------------------


def perimeter_of(state):
    """The length of a stand's boundary, in internal feet."""
    total=0.0
    for loop in state.get('loops') or []:
        for curve in loop:
            try:total+=float(curve.Length)
            except Exception:pass
    return total

def area_allowance(perimeter,tol):
    """The area drift a merge may leave on a stand it does not resize.

    A wall that is kept because it is already within the tolerance sits where
    Main has it, so the rebuilt boundary can be off by about the perimeter
    times the tolerance. Anything larger than that is a real resize.
    """
    floor=UnitUtils.ConvertToInternalUnits(AREA_FLOOR_M2,UnitTypeId.SquareMeters)
    return max(floor,perimeter*tol)

def status_present(m):
    """Whether Main carries the Status parameter at all.

    It is a project parameter, so it is on every Room or on none. The CRM
    plugin deletes it on save and re-imports it about two minutes later, so a
    run made straight after a save finds it gone: that is a timing problem to
    report, not a reason to merge blind.
    """
    for state in m.values():
        if state.get('status') is not None:return True
    return False

def sold_check(m,r,gplan,tol):
    """Sold stands the merge would resize, and sold stands it would reshape.

    Returns (blockers, warnings, checked). Each entry is (cluster, number,
    reason). A blocker is something the run must not apply; a warning is
    reported and does not stop the merge. Only clusters the planner will
    actually apply are examined: a cluster that is skipped for another reason
    changes nothing, so a sold stand in it cannot be resized by this run.
    """
    blockers=[];warnings=[];checked=set()
    for op in gplan:
        if op.get('status')!='PLANNED':continue
        for number in list(op.get('affected') or []):
            main=m.get(number)
            if main is None:continue
            if main.get('status')!=SOLD_ONLY:continue
            checked.add(number)
            redraw=r.get(number)
            if redraw is None:
                blockers.append((op['cluster'],number,
                                 'the Redraw deletes a sold stand, so its area would go to zero'))
                continue
            perimeter=perimeter_of(main)
            allowance=area_allowance(perimeter,tol)
            moved_area=abs((main.get('area') or 0.0)-(redraw.get('area') or 0.0))
            if moved_area>allowance:
                blockers.append((op['cluster'],number,
                                 'sold stand area would change: %.3f -> %.3f sqft (allowed %.3f)'
                                 %(main.get('area') or 0.0,redraw.get('area') or 0.0,allowance)))
                continue
            redraw_perimeter=perimeter_of(redraw)
            if abs(perimeter-redraw_perimeter)>allowance:
                warnings.append((op['cluster'],number,
                                 'sold stand keeps its area but changes shape: perimeter %.1f -> '
                                 '%.1f ft'%(perimeter,redraw_perimeter)))
    return blockers,warnings,checked

def sold_message(blockers):
    """The refusal text: what the rule is, which stands, and what to do."""
    found=sorted(set((str(n),str(reason)) for _,n,reason in blockers))
    return ('A sold stand must not be resized by the Redraw, so nothing was merged. '
            'Stand(s): %s. %s Fix the Redraw so each sold stand keeps its area, or get a '
            'commercial decision recorded in the CRM, then run again.'
            %(', '.join(n for n,_ in found),'; '.join(reason for _,reason in found)+'.'))

def merge_sold_drift(m,r,checked):
    """Sold stands whose area moved after the merge was applied, in Main.

    The prediction before the merge is the gate; this is the same question
    asked of the document that is about to be committed. A Room that has gone
    counts as drift, because its area has gone with it.
    """
    drift=[]
    for number in sorted(checked):
        state=m.get(number);redraw=r.get(number)
        if state is None or redraw is None:continue
        try:after=float(state['room'].Area)
        except Exception:
            drift.append((None,number,'the sold stand has no area after the merge'))
            continue
        want=float(redraw.get('area') or 0.0)
        allowance=area_allowance(perimeter_of(state),0.0)
        if abs(after-want)>allowance:
            drift.append((None,number,'acknowledged area %.3f sqft, merged area %.3f sqft'
                          %(want,after)))
    return drift



def public_plan(entries):
    """The plan entries as the watches and the audit should see them.

    Only the private working keys are dropped. A cluster the planner skipped
    stays SKIPPED, with its reason, or the dry run would report a blocked
    cluster as mergeable.
    """
    out=[]
    for op in entries:
        for key in PRIVATE_KEYS:
            op.pop(key,None)
        out.append(op)
    return out


def judge_run(pout, gout, issues):
    """The headline status of a run, and the first reason it is not clean.

    Returns (status, message, failed, skipped, conflicts). Skipped clusters and
    conflicts are warnings, not errors: the run finished, but not everything
    could be merged, so the Summary must not say SUCCESS on its own.
    """
    failed=sum(1 for x in pout+gout if x['status'] in ('FAILED','VERIFY_FAILED'))
    skipped=[x for x in pout+gout if x['status']=='SKIPPED']
    conflicts=[x for x in issues if 'CONFLICT' in x.get('type','')]
    sold=[x for x in issues if str(x.get('type','')).startswith('SOLD ')]
    if failed:
        status='COMPLETED WITH ERRORS'
    elif skipped or conflicts or sold:
        status='SUCCESS WITH WARNINGS'
    else:
        status='SUCCESS'
    reasons=[x.get('reason') for x in skipped+conflicts+sold if x.get('reason')]
    if failed:
        message='%d change(s) failed; see QA'%failed
    elif reasons:
        message=reasons[0]
    elif skipped or conflicts or sold:
        message='%d cluster(s) skipped, %d conflict(s)'%(len(skipped),len(conflicts))
    else:
        message='Nothing was blocked'
    return status,message,failed,len(skipped),len(conflicts)


def apply_cluster(doc,op,m,r,tag_templates,qa):
    """Apply one planned cluster in a sub-transaction of the merge.

    The sub-transaction is what keeps a cluster atomic: on any failure every
    edit it made is rolled back, so a cluster that fails after deleting a room
    cannot leave the deletion behind. The outer merge transaction still ends
    the run as one step, so a single Ctrl+Z puts Main back.
    """
    plan=op['_plan']
    sub=SubTransaction(doc)
    try:
        sub.Start()
        written=0;moved=0;tags=0;added=0;gone=0
        # Rooms the Redraw deleted go first. Removing a shared wall while both
        # rooms still exist puts two rooms in one region, and Revit's answer to
        # that is a modal warning that edits the document outside this
        # transaction.
        for action,number in op['_rooms']:
            if action=='DELETE':
                doc.Delete(ElementId(m[number]['element_id']));gone+=1
        # Only regenerate when the step before it changed something: a
        # regeneration is the most expensive call in the loop and most
        # clusters do not need all three.
        if gone:doc.Regenerate()
        for wall in plan['delete']:doc.Delete(ElementId(wall['ref']['element_id']))
        for wall,redraw_wall in plan['replace']:doc.Delete(ElementId(wall['ref']['element_id']))
        if plan['delete'] or plan['replace']:doc.Regenerate()
        level_id=op['_level']
        for wall in plan['create']:create_wall(doc,level_id,wall['ref'])
        for wall,redraw_wall in plan['replace']:create_wall(doc,level_id,redraw_wall['ref'])
        if plan['create'] or plan['replace']:doc.Regenerate()
        for action,number in op['_rooms']:
            if action=='ADD':
                redraw_state=r.get(number)
                if redraw_state is None:raise Exception('No Redraw Room for stand '+str(number))
                values={}
                for pn in PARAMS:
                    # A parameter is a dict of status and value, not a pair,
                    # so it has to be read by name.
                    pv=redraw_state.get('parameters',{}).get(pn)
                    if not isinstance(pv,dict):continue
                    if pv.get('status')!='VALUE':continue
                    if pv.get('value') is None:continue
                    values[pn]=pv.get('value')
                level=find_level_by_name(doc,redraw_state.get('level_name'))
                if level is None:raise Exception('Level not found in Main: '+str(redraw_state.get('level_name')))
                room,params=create_room(doc,level,redraw_state,number,values)
                written+=len(params);added+=1
                missing=[pn for pn in PARAMS if pn not in values]
                if missing:
                    qa.append('Stand %s: %d of the 6 merged parameters are blank in the '
                              'Redraw and were not written (%s)'
                              %(number,len(missing),', '.join(missing)))
                point=None
                try:
                    if isinstance(room.Location,LocationPoint):point=room.Location.Point
                except Exception:
                    point=None
                if point is None:
                    point=redraw_state.get('room_point') or redraw_state.get('centroid')
                if point is not None:
                    tags+=create_room_tags(doc,room,tag_templates,point,qa,'Stand '+number)
                else:
                    qa.append('Stand %s: no location point, so no room tag was created'%number)
            elif action=='MOVE':
                room=m[number]['room']
                tag_ids=room_tag_ids(doc,room)
                target=move_room_to_redraw(room,r[number])
                moved+=1
                tmoved,tag_errors=centre_room_tags(doc,tag_ids,target)
                tags+=tmoved
        status=sub.Commit()
        if status!=TransactionStatus.Committed:
            raise Exception('Cluster sub-transaction did not commit: '+str(status))
        op['params_written']=written;op['rooms_moved']=moved;op['tags_moved']=tags
        op['rooms_created']=added;op['rooms_deleted']=gone
        op['status']='APPLIED'
        qa.append('CLUSTER %s applied: %d wall(s) deleted, %d created, %d kept; '
                  '%d room(s) moved, %d created, %d deleted; %d tag(s) moved or created; '
                  '%d parameter(s) written'
                  %(op['cluster'],len(plan['delete']),len(plan['create']),len(plan['keep']),
                    moved,added,gone,tags,written))
    except Exception as e:
        try:sub.RollBack()
        except Exception:pass
        op['status']='FAILED';op['reason']=str(e)


qa=[];pout=[];gout=[];issues=[];summary={'Status':'FAILED'};apply=False
rdoc=None;rdoc_closed=False
START=time.perf_counter()
TIMING=[]

def mark(label):
    """Record a phase boundary, so the QA can say where a slow run spent its
    time instead of the operator guessing."""
    TIMING.append((label,time.perf_counter()-START))

def timing_note():
    if not TIMING:return 'Timing: not recorded'
    parts=[];last=0.0
    for label,at in TIMING:
        parts.append('%s %.1fs'%(label,at-last));last=at
    return 'Timing: '+' | '.join(parts)+' | total %.1fs'%(time.perf_counter()-START)

try:
    if doc is None:raise Exception('No active Main document')
    folder,tol_m,sp_m,notes=check_inputs(pv(iv(0)),iv(1,.0005),iv(2,.25))
    qa.extend(notes)
    apply=bool(iv(3,False));redraw_wins=bool(iv(4,False))
    rvt,side=files(os.path.abspath(folder))
    if same_path(doc.PathName,rvt):
        raise Exception('Active document is the Redraw. Open Main before running Compare and Update Redraw.')
    for open_doc in doc.Application.Documents:
        try: op=open_doc.PathName
        except: op=None
        if same_path(op,rvt):
            raise Exception('Close the Redraw before running Compare and Update Redraw.')
    with open(side,'r',encoding='utf-8') as f:payload=json.load(f)
    loc=validate(payload,rvt,side,doc,qa,issues)
    mark('setup')
    rdoc=open_redraw(doc,rvt)
    if rdoc is None:raise Exception('Redraw document is unavailable')
    activate_main(doc,qa)
    mark('open Redraw')
    qa.append('The Redraw was read from its own document and is closed again at the end of '
              'the run. Main was never linked to it, so the undo history is intact and one '
              'Ctrl+Z puts Main back exactly where it was.')
    tol=UnitUtils.ConvertToInternalUnits(tol_m,UnitTypeId.Meters);sp=UnitUtils.ConvertToInternalUnits(sp_m,UnitTypeId.Meters)
    b=bases(payload);mark('baseline')
    m=collect(doc,None,qa,'Main',loc);mark('Main collect')
    r=collect(rdoc,None,qa,'Redraw',loc);mark('Redraw collect')
    main_protected,main_dupes=collect_exclusions(doc,qa,'Main',loc)
    redraw_protected,redraw_dupes=collect_exclusions(rdoc,qa,'Redraw',loc)
    protected=main_protected|redraw_protected;duplicates=main_dupes|redraw_dupes
    mark('collect exclusions')

    redraw_all=wall_records(list(collect_walls(rdoc,None).values()));mark('Redraw wall scan')
    gplan,pplan=plan_clusters(b,m,r,redraw_all,tol,sp,redraw_wins,qa,issues,
                              protected,duplicates);mark('plan')

    # ---- sold stands ----
    # Status lives in Main only: the CRM plugin owns it and the Redraw has no
    # such parameter. It is deleted when the model is saved and re-imported a
    # couple of minutes later, so its absence is a timing problem to report,
    # never a licence to merge blind.
    if not status_present(m):
        message=('The Status parameter is not present in Main, so sold stands cannot be '
                 'checked. The CRM plugin removes it when the model is saved and re-imports '
                 'it about two minutes later. Wait for the import, then run again.')
        qa.append('SOLD STATUS: '+message)
        issues.append({'stand':None,'type':'SOLD STATUS UNAVAILABLE','reason':message})
        if apply:raise Refused(message)
    blockers,warnings,checked=sold_check(m,r,gplan,tol)
    for cluster,number,reason in warnings:
        qa.append('SOLD STAND %s: %s'%(number,reason))
        issues.append({'stand':number,'cluster':cluster,'type':'SOLD STAND DIMENSIONS',
                       'reason':reason})
    for cluster,number,reason in blockers:
        qa.append('SOLD STAND %s: %s'%(number,reason))
        issues.append({'stand':number,'cluster':cluster,'type':'SOLD STAND AREA','reason':reason})
    if blockers:
        if apply:raise Refused(sold_message(blockers))
        # A dry run writes nothing, so show which clusters an apply would refuse
        # instead of drawing a preview of walls that can never land.
        blocked=set(cluster for cluster,_,_ in blockers)
        for op in gplan:
            if op.get('cluster') in blocked and op.get('status')=='PLANNED':
                op['status']='SKIPPED'
                op['reason']=('SOLD STAND: the merge would resize a sold stand, so an '
                              'apply would refuse the whole run')
    mark('sold check')
    # The tag types and views the model already uses, read before anything is
    # merged so a new stand's tags match what is there now.
    tag_templates=room_tag_templates(doc)

    # ---- parameters, as before ----
    for n in sorted(set(b)|set(m)|set(r)):
        if n in duplicates:
            # The cluster is quarantined, so its parameters stay as Main has them.
            continue
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
                mstate=m.get(number)
                if mstate is None:continue
                for wd in state_walls(mstate).values():
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

    preview_note=None

    if not apply:
        pout=public_plan(pplan)
        gout=public_plan(gplan)
        # Draw the visual diff so the plan can be checked on the plan. The old
        # preview goes in the same transaction, so one Ctrl+Z takes the whole
        # preview off the plan.
        preview_tx=Transaction(doc,'Redraw Merge Preview')
        try:
            preview_tx.Start()
            view=doc.ActiveView
            cleared=clear_preview(doc,qa)
            z=preview_height(m,gplan)
            drawn,recoloured=draw_preview(doc,view,bands,recolour,z,qa)
            committed=preview_tx.Commit()
            if committed!=TransactionStatus.Committed:
                raise Exception('Preview transaction did not commit: '+str(committed))
            preview_note=('Preview drawn: %d band(s) (%d blue, %d amber, %d red), %d wall(s) recoloured.'%(
                drawn,
                sum(1 for b2 in bands if b2[1]==PREVIEW_BLUE),
                sum(1 for b2 in bands if b2[1]==PREVIEW_AMBER),
                sum(1 for b2 in bands if b2[1]==PREVIEW_RED),
                recoloured))
            preview_note+=(' %d band(s) are magenta: those clusters are skipped.'
                           %sum(1 for b2 in bands if b2[1]==PREVIEW_SKIPPED))
            if cleared['elements'] or cleared['overrides']:
                preview_note+=' Removed %d element(s) and %d override(s) from an earlier preview.'%(
                    cleared['elements'],cleared['overrides'])
            qa.append(preview_note)
        except Exception as ex:
            try:preview_tx.RollBack()
            except Exception:pass
            preview_note='Preview could not be drawn: '+str(ex);qa.append(preview_note)
    else:
        # The old preview goes in its own transaction *before* the merge, so a
        # single Ctrl+Z undoes the merge and does not bring the bands back.
        preview_tx=Transaction(doc,'Redraw Merge - Clear Preview')
        try:
            preview_tx.Start()
            cleared=clear_preview(doc,qa)
            committed=preview_tx.Commit()
            if committed!=TransactionStatus.Committed:
                raise Exception('The preview clear transaction did not commit: '+str(committed))
            preview_note='Preview cleared: %d element(s) and %d override(s).'%(
                cleared['elements'],cleared['overrides'])
            qa.append(preview_note)
        except Exception as ex:
            try:preview_tx.RollBack()
            except Exception:pass
            preview_note='The old preview could not be cleared: '+str(ex);qa.append(preview_note)
        tx=Transaction(doc,'MVP2.4B6 - Merge Redraw Updates')
        try:
            tx.Start()
            for op in gplan:
                if op['status']!='PLANNED':continue
                apply_cluster(doc,op,m,r,tag_templates,qa)
            for op in pplan:
                p=m[op['stand']]['room'].LookupParameter(op['parameter'])
                if p is None:op['status']='SKIPPED';op['reason']='Parameter not found in Main'
                elif p.IsReadOnly:op['status']='SKIPPED';op['reason']='Parameter is read-only'
                elif p.StorageType!=StorageType.String:op['status']='SKIPPED';op['reason']='Parameter is not text'
                else:
                    try:op['status']='APPLIED' if p.Set(op['_target']) else 'FAILED'
                    except Exception as e:op['status']='FAILED';op['reason']=str(e)
            # The prediction before the merge is the gate. This asks the same
            # question of the document that is about to be committed: if a sold
            # stand's area moved anyway, the whole merge goes back.
            if checked:
                doc.Regenerate()
                drift=merge_sold_drift(m,r,checked)
                if drift:raise Refused(sold_message(drift))
            committed=tx.Commit()
            if committed!=TransactionStatus.Committed:
                raise Exception('Merge transaction did not commit: '+str(committed))
        except:
            try:tx.RollBack()
            except:pass
            raise
        # Committing regenerates the document by itself, so there is no need
        # to regenerate here - and doing so outside a transaction is refused.
        fresh=collect(doc,None,qa,'Verify',loc,
                      only=set(n for op in gplan for n in op.get('affected',[])))
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
    # The merge is the only transaction that edits Main, so one Ctrl+Z puts
    # Main back exactly where it was. The preview cleanup runs before it, in
    # its own transaction, so undoing the merge does not bring the bands back.
    mark('merge' if apply else 'preview')

    # ---- the audit trail ----
    rows=audit_rows(pout,gout)
    totals=audit_totals(pout,gout,issues)
    audit_error=None
    try:
        ok,audit_error=write_audit(os.path.dirname(rvt),
                                   os.path.splitext(os.path.basename(rvt))[0],
                                   rows,totals,'APPLIED' if apply else 'PREVIEW',
                                   str(uuid.uuid4()))
    except Exception as ex:
        ok,audit_error=False,str(ex)
    if not ok:qa.append('AUDIT TRAIL: '+str(audit_error))
    mark('audit')

    status,message,hard_fail,skipped,conflicts=judge_run(pout,gout,issues)
    summary={'Status':status,'Message':message,'Mode':'APPLIED' if apply else 'DRY RUN',
             'Audit':'OK' if not audit_error else 'FAILED',
             'Clusters':sum(1 for x in gout if x['status'] in ('PLANNED','VERIFIED')),
             'Parameters':sum(1 for x in pout if x['status'] in ('PLANNED','VERIFIED')),
             'Geometry':sum(1 for x in gout if x['status'] in ('PLANNED','VERIFIED')),
             'Rooms created':sum(x.get('rooms_created',0) for x in gout),
             'Skipped':skipped,
             'Errors':hard_fail,
             'Conflicts':conflicts,
             'Sold status':'PRESENT' if (m and status_present(m)) else 'MISSING',
             'Sold stand blocks':sum(1 for x in issues if x.get('type')=='SOLD STAND AREA'),
             'Sold stand warnings':sum(1 for x in issues if x.get('type')=='SOLD STAND DIMENSIONS'),
             'Redraw document closed':'YES' if rdoc_closed else 'NO',
             'Preview':preview_note if preview_note else ('not drawn in apply mode'
                                                          if apply else 'NOT DRAWN'),
             'Undo':('ONE STEP - MVP2.4B6 - Merge Redraw Updates'
                     if apply else 'ONE STEP - Redraw Merge Preview' if preview_note else 'NOT REQUIRED')}
except Exception as e:
    # Put the reason in the summary as well as the QA list: the summary is the
    # watch that is always visible.
    import traceback
    refused=isinstance(e,Refused)
    detail='' if refused else traceback.format_exc()
    qa.append(('REFUSED: ' if refused else 'FATAL: ')+str(e))
    if detail:qa.append(detail)
    summary['Status']='REFUSED' if refused else 'FAILED'
    summary['Message']=str(e);summary['Error']=str(e)
    if detail:summary['Trace']=detail
    # A failed run still leaves a record behind, or the audit trail would read
    # as if the run never happened. Nothing here may raise: the original
    # exception is what the operator needs to see.
    try:
        path=os.path.dirname(rvt)
        name=os.path.splitext(os.path.basename(rvt))[0]
    except Exception:
        path=name=None
    if path is None:
        summary['Audit']='SKIPPED'
    else:
        failure=[['','','NONE','REFUSED' if refused else 'RUN FAILED',0,0,0,0,0,str(e)]]
        try:
            ok,audit_error=write_audit(path,name,failure,audit_totals([],[],issues),
                                       'APPLIED' if apply else 'PREVIEW',
                                       str(uuid.uuid4()))
        except Exception as ex:
            ok,audit_error=False,str(ex)
        summary['Audit']='OK' if ok else 'FAILED'
        if not ok:qa.append('AUDIT TRAIL: the failed run could not be logged: '+str(audit_error))
finally:
    # The Redraw document is closed whatever happened, and Main is brought back
    # to the foreground.
    if rdoc is not None:
        rdoc_closed=close_redraw(doc,rdoc,qa)
        summary['Redraw document closed']='YES' if rdoc_closed else 'NO'
    else:
        summary['Redraw document closed']='NOT OPENED'
    mark('close')
    qa.append(timing_note())

OUT=summary,pout,gout,issues,qa
