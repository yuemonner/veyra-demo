"""Executable Investigation V1. Local-only, fixed tools, immutable runs; no LLM execution."""
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import Asset, Evidence, Investigation, CaseEvent, RuntimeCase, RuntimeSource, RuntimeRun
from app.runtime_diagnostics import CONTEXT_FIELDS, POLICY_VERSION, snapshot, value, compatibility, plan, context_at
from app.runtime_summary import summarize
from app.runtime_adapters import parse_source
from app.runtime_tools import ToolRecorder, statistics, trajectory_error, intervals

router=APIRouter(prefix='/runtime',tags=['Local executable investigation'])
ROOT=Path(__file__).resolve().parents[2]/'work'/'cerberus'


def enabled(request:Request):
    if os.getenv('VEYRA_ENABLE_RUNTIME')!='true': raise HTTPException(404,'Local runtime is not enabled')
    if request.headers.get('origin') and request.headers['origin'] not in os.getenv('VEYRA_RUNTIME_ORIGINS','http://localhost:3019,http://127.0.0.1:3019').split(','):
        raise HTTPException(403,'This local runtime does not accept that origin')
    size=request.headers.get('content-length','0')
    if not size.isdigit() or int(size)>5_100_000: raise HTTPException(413,'Request exceeds local size limit')
router.dependencies.append(Depends(enabled))


def now(): return datetime.utcnow().isoformat()
def uid(prefix): return prefix+'-'+uuid4().hex

def case_required(db,cid):
    c=db.get(RuntimeCase,cid)
    if not c: raise HTTPException(404,'Runtime case not found')
    return c

def sources(db,cid): return list(db.scalars(select(RuntimeSource).where(RuntimeSource.case_id==cid).order_by(RuntimeSource.created_at,RuntimeSource.id)))
def runs(db,cid): return list(db.scalars(select(RuntimeRun).where(RuntimeRun.case_id==cid).order_by(RuntimeRun.created_at,RuntimeRun.id)))
def events(db,cid): return list(db.scalars(select(CaseEvent).where(CaseEvent.investigation_id==cid,CaseEvent.event_kind.like('runtime_%')).order_by(CaseEvent.occurred_at,CaseEvent.id)))
def event_dict(e): return {'id':e.id,'kind':e.event_kind.removeprefix('runtime_'),'timestamp':e.occurred_at.isoformat(),**e.payload}

def record(db,cid,kind,body):
    e=CaseEvent(id=uid('rt-event'),investigation_id=cid,event_kind='runtime_'+kind,occurred_at=datetime.utcnow(),payload=body);db.add(e);db.commit();return event_dict(e)

def source_info(s): return {'id':s.id,'sha256':s.sha256,'created_at':s.created_at.isoformat(),**{k:v for k,v in s.payload.items() if k!='events'}}


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')

class CaseIn(Strict):
    title:str=Field(min_length=1,max_length=200)
    adapter:Literal['cerberus','industrial']
    asset_id:str=Field(min_length=1,max_length=150)
    actor:str=Field(min_length=1,max_length=150)
    context:dict[str,str]=Field(default_factory=dict,max_length=20)
    synthetic:bool=False

class SourceIn(Strict):
    name:str=Field(min_length=1,max_length=160)
    content:str=Field(min_length=1,max_length=5_000_000)
    format:Literal['tum','csv','json']
    role:Literal['online','offline','reference','offline_reference','observation','baseline']='observation'
    frame:str=Field(min_length=1,max_length=200)
    source_uri:str=Field(min_length=1,max_length=2000)
    actor:str=Field(min_length=1,max_length=150)
    mapping:dict[str,str]=Field(default_factory=dict,max_length=20)
    context:dict[str,str]=Field(default_factory=dict,max_length=20)
    synthetic:bool=False

class RunIn(Strict):
    actor:str=Field(min_length=1,max_length=150)
    mode:Literal['operational','retrospective_evaluation']='operational'
    position_threshold_m:float=Field(default=1,gt=0,le=100,allow_inf_nan=False)
    tolerance_s:float=Field(default=.02,ge=0,le=.2,allow_inf_nan=False)
    alignment:Literal['se3_prefix','known_common_frame']='se3_prefix'
    prefix_seconds:float=Field(default=300,ge=1,le=600,allow_inf_nan=False)
    frames_confirmed:bool=False
    use_memory:bool=False
    goal:Literal['reduce_uncertainty','restore_operations']='reduce_uncertainty'

class ToolIn(Strict):
    tool:Literal['inspect_signals','query_window','compute_statistics']
    source_id:str
    signal:Optional[str]=None
    start:Optional[float]=Field(default=None,allow_inf_nan=False)
    end:Optional[float]=Field(default=None,allow_inf_nan=False)
    limit:int=Field(default=100,ge=1,le=2000)
    mode:Literal['operational','retrospective_evaluation']='operational'
    actor:str=Field(min_length=1,max_length=150)


def add_source(db,c,body):
    if c.payload['status']=='closed': raise HTTPException(409,'Reopen the case before adding evidence')
    if len(sources(db,c.id))>=12: raise HTTPException(422,'Local case limit: 12 sources')
    if '..' in body.name or '/' in body.name or '\\' in body.name or body.name.startswith('.'):
        raise HTTPException(422,'Use a filename without paths')
    if c.payload['adapter']=='cerberus' and (body.format!='tum' or body.role not in ('online','offline','reference','offline_reference')): raise HTTPException(422,'Trajectory source requires TUM and an explicit trajectory role')
    if c.payload['adapter']=='industrial' and (body.format=='tum' or body.role not in ('observation','baseline')): raise HTTPException(422,'Industrial source requires CSV/JSON observation or baseline')
    try: samples,warnings=parse_source(body.content,c.payload['adapter'],body.format,body.mapping,c.payload['asset_id'])
    except ValueError as e: raise HTTPException(422,str(e))
    raw=body.content.encode();digest=hashlib.sha256(raw).hexdigest()
    s=RuntimeSource(id=uid('source'),case_id=c.id,sha256=digest,content=body.content,samples=samples,payload={k:v for k,v in body.model_dump().items() if k!='content'})
    s.payload={**s.payload,'machine_context_snapshot':snapshot(c),'normalized_sha256':hashlib.sha256(json.dumps(samples,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),'sample_count':len(samples),'time_start':samples[0]['t'],'time_end':samples[-1]['t'],'warnings':warnings,
               'role_boundary':'evaluation_only_not_known_at_deployment' if 'reference' in body.role else 'source_observation_unreviewed','bytes':len(raw)}
    db.add(s);db.commit();return source_info(s)


def eligible_memory(db,c,before):
    before=min(before,c.created_at)
    found=[]
    for p in db.scalars(select(RuntimeCase).where(RuntimeCase.created_at<before)):
        if p.id==c.id: continue
        pe=[e for e in events(db,p.id) if e.occurred_at<before and e.event_kind=='runtime_outcome']
        if not pe or p.payload['adapter']!=c.payload['adapter']:continue
        outcome=pe[-1].payload;close=[e for e in events(db,p.id) if e.occurred_at<before and e.event_kind=='runtime_lifecycle']
        if not close or close[-1].payload.get('status')!='closed':continue
        keys=['system','model','environment'];left=c.payload['context'];right=p.payload['context'];unknown=[];different=[]
        for k in keys:
            a=left.get(k,'').strip().casefold();b=right.get(k,'').strip().casefold()
            if not a or not b or a in ('unknown','未记录') or b in ('unknown','未记录'): unknown.append(k)
            elif a!=b:different.append(k)
        reviewed=[e for e in events(db,p.id) if e.occurred_at<before and e.event_kind=='runtime_review_outcome' and e.payload.get('outcome_id')==pe[-1].id]
        verified=outcome.get('verification')=='evidence_attached' and bool(reviewed) and reviewed[-1].payload['verdict']=='accepted' and reviewed[-1].payload['actor']!=outcome['actor']
        conditions_match=all(outcome.get('conditions',{}).get(k)==c.payload['context'].get(k) and c.payload['context'].get(k) not in ('',None,'unknown') for k in ('system','model','environment'))
        overlap=bool({s.sha256 for s in sources(db,p.id)} & {s.sha256 for s in sources(db,c.id)})
        context_check={'comparable':True,'missing':[],'mismatched':[]}
        if snapshot(c)['version'] or outcome.get('machine_context_snapshot',{}).get('version',0):
            keys=('oem','model','kit_version','control_version','vehicle_interface','network_path','environment','operating_conditions')
            context_check=compatibility({k:value(snapshot(c),k) for k in keys},{k:value(outcome.get('machine_context_snapshot',{}),k) for k in keys},keys)
        eligible=context_check['comparable'] and not overlap and not outcome.get('simulated',False) and conditions_match and not unknown and not different and verified and outcome['result']=='recovered' and not outcome.get('exclusions','').strip()==''
        found.append({'case_id':p.id,'title':p.payload['title'],'eligible':eligible,'machine_context_compatibility':context_check,'duplicate_evidence':overlap,'verification':'human_reviewed_evidence_attached' if verified else 'unverified_similarity','unknown_conditions':unknown,'mismatched_conditions':different,'outcome':outcome,'review_ids':[e.id for e in reviewed],
                      'boundary':'Context match is retrieval, not causal proof or automatic repair advice. No training or performance improvement established.'})
    return found


def downsample(rows,limit=800):
    if len(rows)<=limit:return rows
    indices=set(np.linspace(0,len(rows)-1,limit-1).astype(int));indices.add(max(range(len(rows)),key=lambda i:rows[i].get('error_m',0)))
    return [rows[i] for i in sorted(indices)]


def execute(db,c,body):
    if c.payload['status']=='closed': raise HTTPException(409,'Reopen before investigating')
    if len(runs(db,c.id))>=40:raise HTTPException(422,'Local case limit: 40 investigation runs')
    start=time.perf_counter(); recorder=ToolRecorder(); ss=sources(db,c.id);trace=recorder.trace;findings=[];hypotheses=[];checks=[];charts=[];warnings=[]
    def integrity():
        for s in ss:
            if hashlib.sha256(s.content.encode()).hexdigest()!=s.sha256 or hashlib.sha256(json.dumps(s.samples,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()!=s.payload.get('normalized_sha256','missing'):
                raise ValueError('Stored source integrity mismatch: '+s.id)
        return {'verified_sources':len(ss),'hashes':{s.id:s.sha256 for s in ss}}
    checked=recorder.call('verify_source_integrity',{},integrity,[s.id for s in ss])
    if checked is None:
        record(db,c.id,'integrity_failure',{'actor':body.actor,'source':'local://source-integrity','trace':recorder.trace,'summary':'Stored source checksum mismatch; numerical investigation refused'})
        raise HTTPException(409,'Source integrity mismatch; investigation refused')
    permitted=[s for s in ss if body.mode=='retrospective_evaluation' or 'reference' not in s.payload['role']]
    recorder.call('inspect_signals',{'case_id':c.id,'mode':body.mode},lambda:{'sources':[source_info(s) for s in permitted]},[s.id for s in permitted])
    if c.payload['adapter']=='cerberus':
        byrole={s.payload['role']:s for s in permitted}
        warnings.append('Body/sensor pose definitions and extrinsics are not independently validated in this subset; residuals may include reference or frame effects.')
        if body.mode!='retrospective_evaluation': warnings.append('Reference trajectories are withheld in operational mode. Position-error metrics unavailable.')
        elif not body.frames_confirmed: warnings.append('Coordinate frame/unit metadata must be reviewed before reference comparison. No metric computed.')
        for role in ('online','offline'):
            est=byrole.get(role); ref=byrole.get('offline_reference' if role=='offline' else 'reference')
            if role=='offline' and not est:continue
            if not est or not ref: warnings.append(role+': missing estimate or corresponding evaluation reference');continue
            if not body.frames_confirmed or body.mode!='retrospective_evaluation':continue
            if body.alignment=='known_common_frame' and est.payload['frame']!=ref.payload['frame']:
                warnings.append('Known-common-frame comparison requires identical reviewed frame labels');continue
            inputs={'estimate_id':est.id,'reference_id':ref.id,'threshold_m':body.position_threshold_m,'tolerance_s':body.tolerance_s,'alignment':body.alignment,'prefix_seconds':body.prefix_seconds,'estimate_frame':est.payload['frame'],'reference_frame':ref.payload['frame']}
            result=recorder.call('trajectory_position_error',inputs,lambda:trajectory_error(est.samples,ref.samples,body.tolerance_s,body.alignment,body.prefix_seconds),[est.id,ref.id])
            if result is None:continue
            times=[r['t'] for r in result['rows']]; errors=[r['error_m'] for r in result['rows']];median_dt=float(np.median(np.diff(times))) if len(times)>1 else 0
            candidates=recorder.call('detect_intervals',{'threshold':body.position_threshold_m,'max_gap_s':max(.2,median_dt*3),'source_run':role},lambda:{'intervals':intervals(times,errors,body.position_threshold_m,max(.2,median_dt*3))},[est.id,ref.id])
            gap_data=recorder.call('timestamp_gap_check',{'source_id':est.id,'factor':5},lambda:{'median_dt_s':median_dt,'large_gap_count':int(np.sum(np.diff([r['t'] for r in est.samples])>max(.5,median_dt*5)))},[est.id])
            findings.append({'id':uid('finding'),'kind':'trajectory_error','label':role+' estimate vs retrospective reference','metrics':result['metrics'],'held_out_prefix_metrics':result['post_alignment_prefix_metrics'],'alignment':result['alignment'],'pairing':{k:v for k,v in result.items() if k in ('paired_count','unpaired_estimate_count','max_pair_dt_s','tolerance_s')},'intervals':candidates['intervals'],'source_ids':[est.id,ref.id], 'observation':'Computed position residual after the selected rigid registration; not a robot failure label or a diagnosed component fault.'})
            charts.append({'label':role,'kind':'position_error','points':downsample(result['rows']),'source_ids':[est.id,ref.id],'full_sample_count':len(result['rows'])})
        hypotheses=[{'id':'localization_degradation','claim':'Localization estimate diverged from retrospective reference','status':'supported_observation_not_cause' if any(f['intervals'] for f in findings) else 'unresolved','support_ids':[f['id'] for f in findings if f['intervals']],'against':[],'missing':['Time-aligned LiDAR/IMU diagnostics, estimator resets and covariance; trajectory alone cannot explain mechanism']},
                    {'id':'alignment_or_timing','claim':'Frame registration, timestamp gaps or reference uncertainty contribute to residual','status':'still_possible','support_ids':[],'against':['Source timestamps paired only within the explicit tolerance'],'missing':['Calibration/frame review and independent reference-quality assessment']}]
        checks=[{'id':'recheck_pairing','question':'Does the finding persist with a stricter timestamp tolerance?','tool':'rerun_stricter_pairing','executable':True,'evidence_ids':[s.id for s in permitted],'required':['Overlapping original pose timestamps'],'why':'Tests pairing sensitivity; persistence does not identify the physical cause.','expected':{'timing_artifact':'Pairs/error change materially','localization_or_reference_difference':'Residual can persist'},'constraints':'Read-only retrospective evaluation; may lose pairs; no robot command'},
                {'id':'inspect_sensors','question':'Were sensor gaps or estimator resets present during high residual intervals?','tool':None,'executable':False,'evidence_ids':[s.id for s in permitted if s.payload['role']=='online'],'required':['Corresponding LiDAR/IMU/estimator diagnostics not loaded in this subset'],'why':'Could distinguish degraded sensing from estimator reset or frame issues','expected':{'sensor_gap':'Aligned dropout in diagnostic records','reset':'Reset/frame event; sensor dropout not necessary'},'constraints':'Engineer supplies authorized source; do not infer a bad sensor from error'}]
    else:
        observations=[s for s in permitted if s.payload['role']=='observation'];baselines=[s for s in permitted if s.payload['role']=='baseline'];groups={}
        for s in observations:
            for row in s.samples: groups.setdefault((row['signal'],row['unit'],row['scope']),[]).append((s.id,row))
        for (signal,unit,scope),group in groups.items():
            group.sort(key=lambda x:x[1]['t']);values=[r['value'] for _,r in group];ids=sorted({sid for sid,_ in group});threshold={'rtt':200,'packet_loss':5}.get(signal)
            stats=recorder.call('compute_statistics',{'signal':signal,'unit':unit,'scope':scope,'count':len(group)},lambda:statistics(values),ids)
            candidate=recorder.call('detect_intervals',{'signal':signal,'threshold':threshold,'unit':unit,'max_gap_s':5},lambda:{'intervals':intervals([r['t'] for _,r in group],values,threshold,5) if threshold is not None else [],'threshold_basis':'Illustrative screening rule, not OEM safety limit' if threshold is not None else 'No configured threshold'},ids)
            comparison=None
            for b in baselines:
                known=lambda v:bool(v and str(v).strip().casefold() not in ('unknown','not recorded'))
                comparable=all(known(b.payload['context'].get(k)) and b.payload['context'].get(k)==c.payload['context'].get(k) for k in ('system','model','environment')) and scope!='unspecified'
                if snapshot(c)['version']:
                    keys=('oem','model','kit_version','control_version','vehicle_interface','network_path','measurement_endpoints','environment','operating_conditions')
                    comparable=comparable and compatibility({k:value(context_at(snapshot(c),group[-1][1]['t']),k) for k in keys},{k:value(context_at(b.payload.get('machine_context_snapshot',{}),b.samples[-1]['t']),k) for k in keys},keys)['comparable']
                bs=[r['value'] for r in b.samples if r['signal']==signal and r['unit']==unit and r['scope']==scope]
                if not comparable or not bs: warnings.append('Baseline '+b.id+' not comparable for '+signal+': missing/mismatched context, unit or measured scope');continue
                comparison=recorder.call('compare_baseline',{'baseline_id':b.id,'signal':signal,'scope':scope},lambda:{'baseline':statistics(bs),'observed':stats,'mean_difference':stats['mean']-float(np.mean(bs)),'boundary':'Difference, not causal effect; workload and window completeness require review'},ids+[b.id])
            findings.append({'id':uid('finding'),'kind':'signal_statistics','label':signal+' · '+scope,'unit':unit,'metrics':stats,'baseline':comparison,'intervals':candidate['intervals'],'source_ids':ids,'threshold':threshold,'observation':('Threshold crossing in measured scope; other systems are not excluded by missing alerts.' if candidate['intervals'] else 'No configured threshold crossing in available samples; coverage and unmeasured domains remain unresolved.')})
            charts.append({'label':signal,'kind':'signal','unit':unit,'source_ids':ids,'full_sample_count':len(group),'points':[{'t':r['t'],'value':r['value'],'row':r['row'],'source_id':sid} for sid,r in group[::max(1,len(group)//800)]][:1000]})
        abnormal=[f for f in findings if f['intervals']]
        hypotheses=[{'id':'network_path','claim':'Measured communication path contributed to reported delay','status':'investigate' if abnormal else 'unresolved','support_ids':[f['id'] for f in abnormal],'against':[],'missing':['Actual control-flow endpoints, symptom window, complete packet/command records']}, {'id':'controller_or_interface','claim':'Controller processing or downstream interface delay','status':'still_possible','support_ids':[],'against':[],'missing':['Application send/receive timestamps, command sequence IDs and physical feedback']}]
        checks=[{'id':'window_query','question':'What measurements exist in the reported anomaly window?','tool':'query_anomaly_window','executable':bool(observations),'evidence_ids':[s.id for s in observations],'required':['Imported observation samples'],'why':'Checks exact raw rows and actual coverage before attributing delay','expected':{'network':'Anomaly in the measured path','other_domain':'Network can be normal or co-occur'},'constraints':'Read-only; does not validate unmeasured endpoints'}, {'id':'command_chain','question':'Where does the same command first show delay?','tool':None,'executable':False,'evidence_ids':[s.id for s in observations],'required':['Matched command ID, send, receive, controller output and physical feedback'],'why':'Distinguishes delivery from processing and actuation delay','expected':{'network':'Delay before receive','controller':'Delay after receive','interface':'Output exists but physical response delayed'},'constraints':'Existing logs only; no actuation; request engineer/OEM approval for any physical inspection'}]
    industrial=None
    if c.payload['adapter']=='industrial':
        industrial=recorder.call('select_diagnostic_checks',{'goal':body.goal,'context_version':snapshot(c)['version'],'policy_version':POLICY_VERSION},lambda:plan(context_at(snapshot(c),max((row['t'] for src in permitted if src.payload['role']=='observation' for row in src.samples),default=time.time())),findings,[s.id for s in permitted if s.payload['role']=='observation'],body.goal),[s.id for s in permitted if s.payload['role']=='observation'])
        checks=industrial['checks']
    retained=eligible_memory(db,c,datetime.utcnow()) if body.use_memory and (c.payload['adapter']!='cerberus' or body.mode=='retrospective_evaluation') else []
    for match in retained:
        if match['eligible']: checks.append({'id':'memory-'+match['case_id'],'question':'Review prior validated check: '+match['outcome']['summary'],'tool':None,'executable':False,'evidence_ids':match['outcome']['evidence_ids'],'required':['Confirm current conditions and prior exclusions'],'why':'Prior human-reviewed case matches known context; not proof of benefit','expected':{'applicable':'Can inform next check','excluded':'Do not reuse'},'constraints':match['outcome']['exclusions']})
    payload={'case_id':c.id,'actor':body.actor,'created_at':now(),'parameters':body.model_dump(),'machine_context_snapshot':snapshot(c),'industrial_support':industrial,'source_ids':[s.id for s in permitted],'source_hashes':{s.id:s.sha256 for s in permitted},'mode':body.mode,'model':{'status':'deterministic_no_llm','external_calls':0,'reason':'No external data transmission authorized'},'trace':trace,'findings':findings,'hypotheses':hypotheses,'checks':checks,'charts':charts,'warnings':sorted(set(warnings)),'memory':retained,'root_cause':'unconfirmed','duration_ms':round((time.perf_counter()-start)*1000,3),'boundary':'Observations, hypotheses, human reports and evaluation references remain distinct. No model training or measured memory improvement claimed.'}
    if c.payload['adapter']=='industrial':payload['presentation']=summarize(payload,ss)
    run=RuntimeRun(id=uid('run'),case_id=c.id,payload=payload);db.add(run);db.commit();return {'id':run.id,**payload}


@router.get('/catalog')
def catalog():
    manifest=ROOT/'manifest.json'
    return {'cerberus':json.loads(manifest.read_text()) if manifest.exists() else {'status':'unavailable','reason':'Run scripts/fetch_cerberus.py after checking source terms'},'industrial':{'status':'synthetic_fixture_available','customer_data':'No BuilderX data or pilot agreement supplied'},'model':{'status':'not_configured','external_calls':False}}


@router.post('/cases')
def create_case(body:CaseIn,db:Session=Depends(get_db)):
    cid=uid('case');asset=uid('asset');trigger=uid('trigger');when=datetime.utcnow()
    db.add(Asset(id=asset,name=body.asset_id,asset_type='Investigation source',family='Local runtime',site='Local preview',environment=body.context.get('environment','unknown')));db.flush()
    db.add(Evidence(id=trigger,asset_id=asset,event_type='case_created',event_time=when,known_at=when,ingested_at=when,source='local://case-created',evidence_class='human_observation',payload={'case_type':'remote_support','synthetic':body.synthetic}));db.flush()
    db.add(Investigation(id=cid,asset_id=asset,trigger_id=trigger,title=body.title,status='active'));db.flush()
    c=RuntimeCase(id=cid,payload={**body.model_dump(),'status':'open','machine_context':{'version':0,'attributes':{},'boundary':'Legacy values are unverified until explicitly recorded'}});db.add(c);db.commit();return {'id':cid,**c.payload}


def support_metrics(case_events, synthetic_case=False):
    points={}
    for event in case_events:
        p=event.payload
        if event.event_kind=='runtime_support_milestone' and not p.get('simulated'):
            points.setdefault(p['milestone'],[]).append({'time':p['observed_at'],'source':p['source'],'event_id':event.id})
    results=[]
    for start,end in [('issue_reported','triage_started'),('issue_reported','dispatch_requested'),('issue_reported','service_reported_restored')]:
        a=points.get(start,[]);b=points.get(end,[])
        # Multiple/conflicting reports are exposed for adjudication, never silently selected.
        duration=(datetime.fromisoformat(b[0]['time'])-datetime.fromisoformat(a[0]['time'])).total_seconds() if len(a)==len(b)==1 else None
        results.append({'from':start,'to':end,'seconds':duration if duration is not None and duration>=0 else None,'status':('synthetic_elapsed' if synthetic_case else 'reported_elapsed') if duration is not None and duration>=0 else 'missing_conflicting_or_out_of_order'})
    return {'data_class':'synthetic_case' if synthetic_case else 'engineer_reported','milestones':points,'durations':results,'handling_time_reduction':None,'boundary':'Reported elapsed time only; restoration is not verified resolution. No comparable baseline, no claimed time saving. Simulated milestones excluded.'}


def present_run(run, case, db):
    payload={'id':run.id,**run.payload}
    if case.payload['adapter']=='industrial':payload['presentation']=summarize(run.payload,sources(db,case.id))
    return payload


@router.get('/cases/{cid}')
def get_case(cid:str,db:Session=Depends(get_db)):
    c=case_required(db,cid);rr=runs(db,cid)
    return {'id':cid,**c.payload,'sources':[source_info(s) for s in sources(db,cid)],'runs':[{'id':r.id,'created_at':r.created_at.isoformat(),'source_ids':r.payload['source_ids']} for r in rr], 'latest':present_run(rr[-1],c,db) if rr else None,'events':[event_dict(e) for e in events(db,cid)],'support_metrics':support_metrics(events(db,cid),c.payload.get('synthetic',False))}


class ContextAttribute(Strict):
    value:str=Field(min_length=1,max_length=500)
    status:Literal['reported','engineer_confirmed','unknown']='reported'

class ContextIn(Strict):
    actor:str=Field(min_length=1,max_length=150)
    source:str=Field(min_length=1,max_length=2000)
    reason:str=Field(min_length=1,max_length=2000)
    expected_version:int=Field(ge=0)
    effective_at:datetime
    attributes:dict[str,ContextAttribute]=Field(min_length=1,max_length=20)

@router.post('/cases/{cid}/context')
def update_context(cid:str,body:ContextIn,db:Session=Depends(get_db)):
    c=case_required(db,cid)
    if c.payload['adapter']!='industrial' or c.payload['status']=='closed':raise HTTPException(409,'Open an industrial case before recording context')
    if body.expected_version!=snapshot(c)['version']:raise HTTPException(409,'Context changed; reload before saving')
    if body.effective_at.tzinfo is None or body.effective_at.timestamp()>datetime.now().timestamp():raise HTTPException(422,'Context effective time needs a timezone and cannot be future')
    if not all(t.strip() for t in (body.actor,body.source,body.reason)) or any(k not in CONTEXT_FIELDS for k in body.attributes):raise HTTPException(422,'Use documented context attributes and a named source/actor/reason')
    if any(not a.value.strip() for a in body.attributes.values()):raise HTTPException(422,'Context values cannot be blank')
    attrs={**snapshot(c)['attributes']}
    for k,a in body.attributes.items():
        if k=='remote_access' and a.value not in ('unknown','not_authorized','read_only_authorized'):raise HTTPException(422,'Remote access must be unknown, not_authorized or read_only_authorized')
        attrs[k]={**a.model_dump(),'source':body.source,'actor':body.actor,'effective_at':body.effective_at.isoformat(),'recorded_at':now()}
    updated={'version':body.expected_version+1,'attributes':attrs}
    c.payload={**c.payload,'machine_context':updated};db.add(c)
    return record(db,cid,'context_update',{'actor':body.actor,'source':body.source,'summary':body.reason,'machine_context_snapshot':updated})


@router.post('/cases/{cid}/sources')
def import_source(cid:str,body:SourceIn,db:Session=Depends(get_db)): return add_source(db,case_required(db,cid),body)


@router.post('/cases/{cid}/cerberus')
def import_cerberus(cid:str,db:Session=Depends(get_db)):
    c=case_required(db,cid)
    if c.payload['adapter']!='cerberus': raise HTTPException(422,'Choose a CERBERUS case')
    manifest=ROOT/'manifest.json'
    if not manifest.exists():raise HTTPException(503,'Real public files unavailable; no synthetic replacement')
    m=json.loads(manifest.read_text());made=[]
    # Verify every hash and parse every file before mutating this case.
    prepared=[]
    for f in m['files']:
        if f['name'] not in ('online.csv','reference.csv','offline.csv','offline_reference.csv'):raise HTTPException(422,'Invalid local dataset manifest')
        content=(ROOT/f['name']).read_text()
        if hashlib.sha256(content.encode()).hexdigest()!=f['sha256']:raise HTTPException(409,'CERBERUS file hash changed; refetch or import as a new source explicitly')
        body=SourceIn(name=f['name'],content=content,format='tum',role=f['name'][:-4],frame='DARPA_reference' if 'reference' in f['name'] else 'published_estimator_frame_requires_registration',source_uri=f['url'],actor=c.payload['actor'],context={'system':'ANYmal C','model':'ANYmal 1','environment':'DARPA SubT finals'},synthetic=False)
        parse_source(content,'cerberus','tum',{},c.payload['asset_id']);prepared.append(body)
    if len(sources(db,cid))+len(prepared)>12:raise HTTPException(422,'Source limit reached')
    for body in prepared:made.append(add_source(db,c,body))
    return {'sources':made,'terms':m['license'],'commercial_use':m['commercial_use']}


@router.post('/cases/{cid}/runs')
def run_case(cid:str,body:RunIn,db:Session=Depends(get_db)):return execute(db,case_required(db,cid),body)


@router.post('/cases/{cid}/tools')
def tool(cid:str,body:ToolIn,db:Session=Depends(get_db)):
    case_required(db,cid);s=db.get(RuntimeSource,body.source_id)
    if not s or s.case_id!=cid:raise HTTPException(422,'Source is not in this case')
    if 'reference' in s.payload['role'] and body.mode!='retrospective_evaluation':raise HTTPException(403,'Evaluation-only reference is unavailable to operational tools')
    if hashlib.sha256(s.content.encode()).hexdigest()!=s.sha256 or hashlib.sha256(json.dumps(s.samples,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()!=s.payload.get('normalized_sha256','missing'):
        raise HTTPException(409,'Source integrity mismatch; query refused')
    if body.start is not None and body.end is not None and body.end<body.start:raise HTTPException(422,'Window end precedes start')
    selected=[r for r in s.samples if (body.start is None or r['t']>=body.start) and (body.end is None or r['t']<=body.end) and (body.signal is None or r.get('signal')==body.signal)]
    recorder=ToolRecorder()
    def compute():
        if body.tool=='inspect_signals':return {'signals':sorted({r.get('signal','pose_xyz') for r in selected}),'count':len(selected),'role':s.payload['role']}
        if body.tool=='query_window':
            raw=s.content.splitlines()
            originals=json.loads(s.content) if s.payload['format']=='json' else None
            def original(r):
                # Exact physical line when the importer recorded it; legacy CSV rows are offset by the header line.
                if r.get('line'):return raw[r['line']-1] if r['line']<=len(raw) else None
                if originals is not None:return originals[r['row']-1]
                index=r['row'] if s.payload['format']=='csv' else r['row']-1
                return raw[index] if 0<=index<len(raw) else None
            return {'count':len(selected),'returned':min(body.limit,len(selected)),'samples':[{**r,'original_record':original(r)} for r in selected[:body.limit]],'truncated':len(selected)>body.limit}
        if any('value' not in r for r in selected):raise ValueError('Select an industrial scalar signal for scalar statistics')
        if len({(r.get('signal'),r.get('unit'),r.get('scope')) for r in selected})>1:raise ValueError('Statistics require one signal/unit/scope group')
        return statistics([r['value'] for r in selected])
    value=recorder.call(body.tool,body.model_dump(),compute,[s.id]);entry=record(db,cid,'tool',{'actor':body.actor,'source':s.id,'trace':recorder.trace,'source_hash':s.sha256})
    return {'result':value,'trace':recorder.trace,'event_id':entry['id']}


class FeedbackIn(Strict):
    kind:Literal['check_review','manual_check','decision','action','outcome','review_outcome','lifecycle','dispatch_plan','support_milestone']
    actor:str=Field(min_length=1,max_length=150)
    source:str=Field(min_length=1,max_length=2000)
    summary:str=Field(min_length=1,max_length=4000)
    run_id:Optional[str]=None
    check_id:Optional[str]=None
    verdict:Optional[Literal['accepted','rejected','edited']]=None
    linked_id:Optional[str]=None
    evidence_ids:list[str]=Field(default_factory=list,max_length=12)
    result:Optional[Literal['recovered','not_recovered','recurred','unknown']]=None
    verification:Literal['unverified','evidence_attached']='unverified'
    executed_at:Optional[datetime]=None
    window_start:Optional[datetime]=None
    window_end:Optional[datetime]=None
    conditions:dict[str,str]=Field(default_factory=dict,max_length=20)
    exclusions:str=Field(default='',max_length=3000)
    status:Optional[Literal['closed','open','unresolved']]=None
    simulated:bool=False
    reason:Optional[str]=Field(default=None,max_length=2000)
    result_status:Optional[Literal['observed','inconclusive','not_performed']]=None
    milestone:Optional[Literal['issue_reported','triage_started','dispatch_requested','engineer_arrived','service_reported_restored']]=None
    observed_at:Optional[datetime]=None
    remote_resolution:Optional[Literal['unknown','possible','not_possible','reported_resolved']]=None
    preparation:list[str]=Field(default_factory=list,max_length=20)
    blockers:list[str]=Field(default_factory=list,max_length=20)


def linked_event(db,cid,eid,kind):
    e=db.get(CaseEvent,eid or '')
    if not e or e.investigation_id!=cid or e.event_kind!='runtime_'+kind:raise HTTPException(422,'Linked record must belong to this case and have the correct type')
    return e


@router.post('/cases/{cid}/feedback')
def feedback(cid:str,body:FeedbackIn,db:Session=Depends(get_db)):
    c=case_required(db,cid)
    if c.payload['status']=='closed' and body.kind!='lifecycle':raise HTTPException(409,'Reopen before modifying this case')
    if not all(v.strip() for v in (body.actor,body.source,body.summary)):raise HTTPException(422,'Actor, source and summary required')
    available={s.id for s in sources(db,cid)}
    if any(e not in available for e in body.evidence_ids):raise HTTPException(422,'Evidence must belong to this case')
    payload=body.model_dump(mode='json');payload['timestamp']=now()
    if body.kind in ('decision','check_review','manual_check'):
        r=db.get(RuntimeRun,body.run_id or '')
        if not r or r.case_id!=cid:raise HTTPException(422,'Select an investigation run in this case')
        payload['investigation_snapshot']={'run_id':r.id,'source_hashes':r.payload['source_hashes'],'parameters':r.payload['parameters'],'hypotheses':r.payload['hypotheses'],'machine_context':r.payload.get('machine_context_snapshot'),'checks':r.payload['checks']}
        if body.kind in ('check_review','manual_check'):
            if body.check_id not in {ch['id'] for ch in r.payload['checks']}:raise HTTPException(422,'Select an actual recommended check')
            if r.payload.get('industrial_support') and body.kind=='check_review' and not (body.reason or '').strip():raise HTTPException(422,'Explain why this check is accepted, rejected or edited')
            if r.payload.get('industrial_support') and body.kind=='manual_check' and (body.result_status is None or body.result_status=='observed' and not body.evidence_ids):raise HTTPException(422,'Record result status; observed results require case evidence')
            if body.kind=='check_review' and not body.verdict:raise HTTPException(422,'Accept, reject or edit explicitly')
    if body.kind=='manual_check' and body.result_status=='observed':
        if not body.executed_at or body.executed_at.tzinfo is None or body.executed_at.timestamp()>datetime.now().timestamp():raise HTTPException(422,'Observed check requires a timezone-aware actual check time')
        payload['grade']='engineer_reported_evidence_attached'
    payload['machine_context_snapshot']=snapshot(c)
    if body.kind=='support_milestone':
        if c.payload['adapter']!='industrial' or not body.milestone or not body.observed_at or body.observed_at.tzinfo is None or body.observed_at.timestamp()>datetime.now().timestamp():raise HTTPException(422,'Industrial milestone requires an explicit type and non-future timezone-aware observation time')
        payload['grade']='engineer_reported_not_independently_verified'
    if body.kind=='dispatch_plan':
        if c.payload['adapter']!='industrial' or body.remote_resolution is None:raise HTTPException(422,'Industrial dispatch plan needs an explicit remote-resolution assessment')
        r=db.get(RuntimeRun,body.run_id or '')
        if not r or r.case_id!=cid:raise HTTPException(422,'Select this case investigation run')
        if r.payload.get('machine_context_snapshot',{}).get('version',0)!=snapshot(c)['version'] or set(r.payload['source_ids'])!={s.id for s in sources(db,cid) if r.payload['mode']=='retrospective_evaluation' or 'reference' not in s.payload['role']}:raise HTTPException(409,'Evidence/context changed; run a new investigation before preparing dispatch')
        payload['support_snapshot']=r.payload.get('industrial_support');payload['boundary']='Engineer-reported preparation; no dispatch approval or confirmed part failure'
    if body.kind=='action':
        linked_event(db,cid,body.linked_id,'decision')
        if body.executed_at is None or body.executed_at.tzinfo is None or body.executed_at.timestamp()>datetime.now().timestamp():raise HTTPException(422,'Actual action time needs a timezone and cannot be in the future')
        payload['execution_grade']='explicitly_simulated' if body.simulated else 'engineer_reported_action'
    if body.kind=='outcome':
        a=linked_event(db,cid,body.linked_id,'action')
        if body.result is None:raise HTTPException(422,'Outcome requires an explicit result')
        if body.verification=='evidence_attached' and not body.evidence_ids:raise HTTPException(422,'Evidence-attached outcome requires case source IDs')
        if (body.window_start is None)!=(body.window_end is None):raise HTTPException(422,'Provide both observation-window endpoints')
        if body.window_start is not None:
            if body.window_start.tzinfo is None or body.window_end.tzinfo is None:raise HTTPException(422,'Observation times require timezones')
            if a.payload.get('executed_at') and body.window_start<datetime.fromisoformat(a.payload['executed_at']):raise HTTPException(422,'Outcome window cannot precede the reported action')
            if body.window_end<=body.window_start:raise HTTPException(422,'Observation window must have positive duration')
            if body.window_end.timestamp()>datetime.now().timestamp():raise HTTPException(422,'Observation window must already be complete')
        if body.verification=='evidence_attached' and (not body.window_start or not body.conditions or not body.exclusions.strip()):raise HTTPException(422,'Verification needs a completed window, applicability conditions and exclusions')
        if body.verification=='evidence_attached':
            evidence_sources=[s for s in sources(db,cid) if s.id in body.evidence_ids]
            if not any(any(body.window_start.timestamp()<=r['t']<=body.window_end.timestamp() for r in s.samples) for s in evidence_sources):raise HTTPException(422,'Attached source has no measurement in the reported observation window')
        payload['simulated']=body.simulated or a.payload.get('simulated',False)
        payload['verification_grade']='engineer_reported_not_independently_attested'
    if body.kind=='review_outcome':
        outcome=linked_event(db,cid,body.linked_id,'outcome')
        if body.actor==outcome.payload['actor']:raise HTTPException(422,'A distinct named reviewer is required')
        if body.verdict not in ('accepted','rejected'):raise HTTPException(422,'Outcome review requires accepted or rejected')
        payload['outcome_id']=outcome.id;payload['boundary']='Second-person reported review; identity is not authenticated in this local prototype'
    if body.kind=='lifecycle':
        if body.status is None:raise HTTPException(422,'Choose closed, open or unresolved')
        if body.status=='closed' and not any(e.event_kind=='runtime_outcome' for e in events(db,cid)):raise HTTPException(422,'Record an explicitly verified or unverified outcome before closing')
        c.payload={**c.payload,'status':body.status};db.add(c)
    return record(db,cid,body.kind,payload)


class CheckExecuteIn(Strict):
    run_id:str
    check_id:str
    actor:str=Field(min_length=1,max_length=150)
    source:str=Field(min_length=1,max_length=2000)


@router.post('/cases/{cid}/execute-check')
def execute_check(cid:str,body:CheckExecuteIn,db:Session=Depends(get_db)):
    c=case_required(db,cid);r=db.get(RuntimeRun,body.run_id)
    if not r or r.case_id!=cid:raise HTTPException(422,'Run must belong to this case')
    check=next((ch for ch in r.payload['checks'] if ch['id']==body.check_id),None)
    if not check or not check['executable']:raise HTTPException(422,'This check requires an engineer or additional evidence')
    reviews=[e for e in events(db,cid) if e.event_kind=='runtime_check_review' and e.payload.get('run_id')==r.id and e.payload.get('check_id')==body.check_id]
    if not reviews or reviews[-1].payload['verdict']!='accepted':raise HTTPException(409,'Accept the original read-only check before executing; edited checks require manual review')
    if c.payload['status']=='closed':raise HTTPException(409,'Reopen before executing a new diagnostic check')
    if r.payload.get('machine_context_snapshot',{}).get('version',0)!=snapshot(c)['version'] or set(r.payload['source_ids'])!={s.id for s in sources(db,cid) if r.payload['mode']=='retrospective_evaluation' or 'reference' not in s.payload['role']}:raise HTTPException(409,'Evidence or context changed; investigate and review the new check first')
    if check['tool']=='rerun_stricter_pairing':
        params={**r.payload['parameters'],'actor':body.actor,'tolerance_s':min(r.payload['parameters']['tolerance_s']/2,.01)}
        result=execute(db,c,RunIn(**params))
        record(db,cid,'check_execution',{'actor':body.actor,'source':body.source,'run_id':r.id,'check_id':body.check_id,'result_run_id':result['id'],'status':'executed_read_only'})
        return result
    ss=[s for s in sources(db,cid) if s.payload['role']=='observation']
    if not ss:raise HTTPException(422,'No observation source')
    abnormal=next((f for f in r.payload['findings'] if f['intervals']),None)
    target=next((s for s in ss if abnormal and s.id in abnormal['source_ids']),ss[0])
    start=abnormal['intervals'][0]['start'] if abnormal else target.samples[0]['t'];end=abnormal['intervals'][0]['end'] if abnormal else target.samples[-1]['t']
    result=tool(cid,ToolIn(tool='query_window',source_id=target.id,actor=body.actor,start=start,end=end,limit=100),db)
    record(db,cid,'check_execution',{'actor':body.actor,'source':body.source,'run_id':r.id,'check_id':body.check_id,'tool_event_id':result['event_id'],'status':'executed_read_only','machine_context_snapshot':r.payload.get('machine_context_snapshot'),'summary':'Queried original rows; no repair or recovery inferred'})
    return result


@router.get('/cases/{cid}/memory')
def memory(cid:str,db:Session=Depends(get_db)):
    c=case_required(db,cid);return {'matches':eligible_memory(db,c,datetime.utcnow()),'boundary':'Only prior closed cases with distinct-person reviewed evidence and matching known context are eligible; no training/performance gain established'}


@router.get('/cases/{cid}/evaluation')
def evaluation(cid:str,db:Session=Depends(get_db)):
    c=case_required(db,cid);rr=runs(db,cid);latest=rr[-1] if rr else None
    metrics={'detection_precision':None,'detection_recall':None,'causal_accuracy':None,'unsupported_claim_rate':None,'next_check_quality':None,'memory_improvement':None}
    if not latest:return {'status':'no_run','metrics':metrics}
    p=latest.payload;eligible=[m for m in p['memory'] if m['eligible']]
    return {'status':'measured_subset_only','metrics':metrics,'unavailable_reason':'No independently labeled fault intervals, causal outcomes or held-out multi-case benchmark. Reference position is not a fault label.',
            'arms':{'A':{'status':'same_fixed_analytics_available','claim':'Numerical baseline; not an independently timed run'},'B':{'status':'unavailable','reason':'General LLM tool runner not configured or authorized; no model result fabricated'},
                    'C':{'status':'executed','run_id':latest.id,'elapsed_ms':p['duration_ms'],'tool_count':len(p['trace']),'failed_tools':sum(t['status']=='failed' for t in p['trace']),'external_model_calls':0},
                    'D':{'status':'retrieval_executed' if p['parameters']['use_memory'] else 'not_run','eligible_cases':len(eligible),'improvement':None}},
            'numerical_metrics':[{'label':f['label'],'metrics':f['metrics']} for f in p['findings']], 'trace_source_coverage':sum(bool(t['source_ids']) for t in p['trace'])/len(p['trace']) if p['trace'] else None,
            'reference_boundary':'Reference accessible only in explicit retrospective evaluation. It is excluded from operational tools and operational investigations. Prior cases are filtered by recorded time; no future/self retrieval.'}


@router.get('/cases/{cid}/runs/{rid}')
def historical_run(cid:str,rid:str,db:Session=Depends(get_db)):
    case_required(db,cid);r=db.get(RuntimeRun,rid)
    if not r or r.case_id!=cid:raise HTTPException(404,'Investigation run not found in this case')
    return present_run(r,case_required(db,cid),db)
