# -*- coding: utf-8 -*-
"""Compare and Update Redraw - Wall/Room/tag merge; pre-move tag capture and late link cleanup.
Revit 2027 | Dynamo 4.0.2 | CPython3/PythonNet3

IN[0] Redraw directory
IN[1] comparison tolerance metres, optional, default 0.01
IN[2] boundary sample spacing metres, optional, default 0.25
IN[3] APPLY UPDATES boolean, optional, default False

OUT[0] Summary
OUT[1] Parameter Updates
OUT[2] Geometry Updates
OUT[3] Conflicts / Skips
OUT[4] QA / Errors

Boundary location is read from the baseline (Center or Finish).

Geometry scope is deliberately controlled:
- existing Rooms only
- REDRAW_ONLY geometry changes only
- Main boundary must consist entirely of unshared walls using Closed Wall - RX / Open Wall - RX
- Redraw boundary must use only those wall types; Room Separation Lines are not supported
- added/removed/placed/unplaced Rooms, level mismatches and conflicts are skipped
"""
import clr, os, json, math, re
clr.AddReference('RevitAPI'); clr.AddReference('RevitServices')
from Autodesk.Revit.DB import (Arc, Line, XYZ, Wall, LocationCurve, LocationPoint, BuiltInParameter,
 BuiltInCategory, FilteredElementCollector, RevitLinkInstance, RevitLinkType,
 RevitLinkOptions, FilePath, ImportPlacement, Transaction, ModelPathUtils,
 SpatialElementBoundaryOptions, SpatialElementBoundaryLocation,
 UnitTypeId, UnitUtils, StorageType, ElementId)
from RevitServices.Persistence import DocumentManager

doc=DocumentManager.Instance.CurrentDBDocument
PARAMS=['Floorplan Hall','Floorplan Pavilion','Floorplan Sector',
        'Obstruction Area','Product Code','Custom Area']

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
    base={'number':(room.Number or '').strip(),'room':room,'element_id':eidv(room.Id),
          'parameters':pvals(room),'level_id':eidv(room.LevelId),'level_name':level_name,
          'loops':[],'boundary_ids':[],'wall_loops':[],'room_point':None}
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
                'level_id':r.get('level_id'),'level_name':r.get('level_name')}
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
        if not RevitLinkType.IsLoaded(host,lt.Id):
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

def remove_link_type(host,type_id):
    tx=Transaction(host,'MVP2.4B1 - Remove Redraw Link')
    try:
        tx.Start();host.Delete(type_id);tx.Commit();return True
    except:
        try:tx.RollBack()
        except:pass
        raise

def boundary_usage(main):
    use={}
    for s in main.values():
        for i in set(s.get('boundary_ids',[])):
            if i>0:use[i]=use.get(i,0)+1
    return use

def wall_types(host):
    return {w.Name:w for w in FilteredElementCollector(host).OfClass(__import__('Autodesk.Revit.DB',fromlist=['WallType']).WallType).ToElements()}

def geometry_eligibility(host,s,use,redraw):
    ids=sorted(set(i for i in s.get('boundary_ids',[]) if i>0))
    if not ids:return False,'No Main wall boundary elements found',[]
    for i in ids:
        e=host.GetElement(ElementId(i))
        if not isinstance(e,Wall):return False,'Main boundary contains a non-wall element',ids
        if use.get(i,0)>1:return False,'Main boundary includes a wall shared by another stand',ids
        wt=host.GetElement(e.GetTypeId())
        if wt is None or wt.Name not in ('Closed Wall - RX','Open Wall - RX'):
            return False,'Main boundary contains unsupported wall type',ids
    rw=[w for lp in redraw.get('wall_loops',[]) for w in lp]
    rc=flat(redraw.get('loops',[]))
    if len(rw)!=len(rc):return False,'Redraw boundary is not entirely wall-based',ids
    for w in rw:
        if w['type'] not in ('Closed Wall - RX','Open Wall - RX'):
            return False,'Redraw uses unsupported wall type',ids
    return True,None,ids

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

def create_walls(host,main_room,redraw):
    types=wall_types(host);created=[]
    rw=[w for lp in redraw.get('wall_loops',[]) for w in lp]
    if not rw:raise Exception('No Redraw walls available')
    for w in rw:
        wt=types.get(w['type'])
        if wt is None:raise Exception('Wall type not found in Main: '+str(w['type']))
        h=w.get('height')
        if h is None or h<=0:h=10.0
        wall=Wall.Create(host,w['curve'],wt.Id,main_room.LevelId,h,float(w.get('offset') or 0.0),bool(w.get('flipped',False)),False)
        created.append(eidv(wall.Id))
    return created

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

qa=[];pout=[];gout=[];issues=[];summary={'Status':'FAILED'}
lnk=None;link_type_id=None;cleanup=False
try:
    if doc is None:raise Exception('No active Main document')
    folder=pv(iv(0));tol_m=float(iv(1,.01));sp_m=float(iv(2,.25));apply=bool(iv(3,False))
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
    b=bases(payload);m=collect(doc,None,qa,'Main',loc);r=collect(ldoc,lnk.GetTotalTransform(),qa,'Redraw',loc);use=boundary_usage(m)
    pplan=[];gplan=[]
    for n in sorted(set(b)|set(m)|set(r)):
        if n in b and n in m and n in r:
            bgm=geom(b[n],m[n],tol,sp);bgr=geom(b[n],r[n],tol,sp)
            if bgm=='UNCHANGED' and bgr in ('MOVED','MODIFIED'):
                reason=level_mismatch(b[n],m[n],r[n])
                if reason:
                    gout.append({'stand':n,'change':bgr,'status':'SKIPPED','reason':reason})
                else:
                    ok,reason,ids=geometry_eligibility(doc,m[n],use,r[n])
                    op={'stand':n,'change':bgr,'status':'PLANNED'}
                    if ok:
                        op['_delete_ids']=ids;op['_redraw']=r[n];gplan.append(op)
                    else:
                        op['status']='SKIPPED';op['reason']=reason;gout.append(op)
            elif bgm not in ('UNCHANGED',) and bgr not in ('UNCHANGED',) and geom(m[n],r[n],tol,sp)!='UNCHANGED':
                issues.append({'stand':n,'type':'GEOMETRY CONFLICT','reason':'Main and Redraw both changed differently'})
            elif bgr in ('ADDED','REMOVED','PLACEMENT_CHANGED'):
                issues.append({'stand':n,'type':'GEOMETRY SKIPPED','reason':'Added, removed, placed or unplaced stands are outside MVP2.4B1'})
            for pn in PARAMS:
                rel,bv,mv,rv=prel(b[n].get('parameters',{}).get(pn),m[n].get('parameters',{}).get(pn),r[n].get('parameters',{}).get(pn))
                if rel=='REDRAW_ONLY':
                    target=desired(rv);op={'stand':n,'main_before':mv[1],'baseline':bv[1],'main_after':mv[1],'status':'PLANNED','redraw':rv[1],'parameter':pn}
                    if target is None:op['status']='SKIPPED';op['reason']='Redraw parameter cannot be written safely';pout.append(op)
                    else:op['_target']=target;pplan.append(op)
                elif rel=='CONFLICT':issues.append({'stand':n,'type':'PARAMETER CONFLICT','parameter':pn,'baseline':bv[1],'main':mv[1],'redraw':rv[1]})

    # Keep Redraw loaded until verification is complete. Link-derived Curve
    # objects may become invalid if the RevitLinkType is removed too early.
    summary_link_removed='PENDING' if cleanup else 'NOT REQUIRED'

    if not apply:
        for op in pplan:op.pop('_target',None);pout.append(op)
        for op in gplan:op.pop('_delete_ids',None);op.pop('_redraw',None);gout.append(op)
    else:
        tx=Transaction(doc,'MVP2.4B6 - Merge Redraw Updates')
        try:
            tx.Start()
            for op in gplan:
                try:
                    for i in op['_delete_ids']:doc.Delete(ElementId(i))
                    doc.Regenerate()
                    room=m[op['stand']]['room']
                    tag_ids=room_tag_ids_in_active_view(doc,room)
                    op['created_wall_ids']=create_walls(doc,room,op['_redraw'])
                    doc.Regenerate()
                    target=move_room_to_redraw(room,op['_redraw'])
                    op['room_moved']='YES'
                    moved,tag_errors=centre_room_tags(doc,tag_ids,target)
                    op['room_tags_found']=len(tag_ids)
                    op['room_tags_moved']=moved
                    if tag_errors:op['tag_errors']=tag_errors
                    op['status']='APPLIED'
                except Exception as e:op['status']='FAILED';op['reason']=str(e)
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
        doc.Regenerate()
        # Verify geometry and parameters after commit.
        fresh=collect(doc,None,qa,'Verify',loc)
        for op in gplan:
            if op['status']=='APPLIED':
                after=fresh.get(op['stand']);delta=bd(after['loops'],r[op['stand']]['loops'],sp) if after else 1e99
                op['status']='VERIFIED' if delta<=tol else 'VERIFY_FAILED'
                if op['status']!='VERIFIED':op['reason']='Main boundary does not match Redraw after update'
            op.pop('_delete_ids',None);op.pop('_redraw',None);gout.append(op)
        for op in pplan:
            if op['status']=='APPLIED':
                p=m[op['stand']]['room'].LookupParameter(op['parameter']);after=p.AsString() if p else None;op['main_after']=after
                op['status']='VERIFIED' if after==op['_target'] else 'VERIFY_FAILED'
                if op['status']!='VERIFIED':op['reason']='Main value does not match Redraw after update'
            op.pop('_target',None);pout.append(op)
    # Verification no longer needs linked model data; remove Redraw now.
    if cleanup and link_type_id is not None:
        remove_link_type(doc,link_type_id)
        cleanup=False
        summary_link_removed='YES'

    hard_fail=sum(1 for x in pout+gout if x['status'] in ('FAILED','VERIFY_FAILED'))
    summary={'Status':'SUCCESS' if hard_fail==0 else 'COMPLETED WITH ERRORS','Mode':'APPLIED' if apply else 'DRY RUN',
             'Parameters':sum(1 for x in pout if x['status'] in ('PLANNED','VERIFIED')),
             'Geometry':sum(1 for x in gout if x['status'] in ('PLANNED','VERIFIED')),
             'Skipped':sum(1 for x in pout+gout if x['status']=='SKIPPED'),
             'Errors':hard_fail,
             'Conflicts':sum(1 for x in issues if 'CONFLICT' in x['type']),
             'Redraw link removed':summary_link_removed,
             'Undo':'ONE STEP - MVP2.4B6 - Merge Redraw Updates' if apply else 'NOT REQUIRED'}
except Exception as e:
    qa.append('FATAL: '+str(e));summary['Status']='FAILED'
finally:
    # Emergency cleanup only. Normal cleanup happens before Main is edited.
    if cleanup and link_type_id is not None:
        try:
            remove_link_type(doc,link_type_id)
            summary['Redraw link removed']='YES'
        except Exception as e:
            qa.append('CLEANUP FAILED: '+str(e));summary['Redraw link removed']='NO'

OUT=summary,pout,gout,issues,qa
