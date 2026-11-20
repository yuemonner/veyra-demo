import json
from pathlib import Path
import numpy as np
import pytest
from datetime import datetime,timedelta,timezone
from test_veyra_v0 import client,reset
from app.runtime_adapters import parse_tum,parse_industrial
from app.runtime_tools import trajectory_error

@pytest.fixture(autouse=True)
def local_runtime(monkeypatch):
    reset();monkeypatch.setenv('VEYRA_ENABLE_RUNTIME','true')

CONTEXT={'system':'remote control','model':'M1','environment':'yard'}
MAPPING={'timestamp':'time','asset':'machine','signal':'metric','value':'reading','unit':'units','scope':'endpoint','version':'release','time_unit':'iso8601'}
DATA=[{'time':'2026-01-01T14:02:00Z','machine':'EX1','metric':'rtt','reading':40,'units':'ms','endpoint':'router'}, {'time':'2026-01-01T14:06:00Z','machine':'EX1','metric':'rtt','reading':470,'units':'ms','endpoint':'router'}]

def create(adapter='industrial',context=None):
    res=client.post('/runtime/cases',json={'title':'Local test','adapter':adapter,'asset_id':'EX1','actor':'Engineer','context':context or CONTEXT,'synthetic':adapter=='industrial'})
    assert res.status_code==200,res.text
    return res.json()['id']

def post(cid,path,data=None):return client.post('/runtime/cases/'+cid+'/'+path,json=data)
def source(cid,rows=DATA,**extra):
    res=post(cid,'sources',{'name':'source.json','content':json.dumps(rows),'format':'json','role':'observation','frame':'not_applicable','source_uri':'synthetic://test','actor':'Engineer','mapping':MAPPING,'context':CONTEXT,'synthetic':True,**extra});assert res.status_code==200,res.text;return res.json()
def run(cid,**extra):
    r=post(cid,'runs',{'actor':'Engineer',**extra});assert r.status_code==200,r.text;return r.json()
def feedback(cid,kind,**extra):return post(cid,'feedback',{'actor':'Engineer','source':'synthetic://report','summary':'Test recorded statement','kind':kind,'reason':'Scope and feasibility reviewed',**extra})


def test_real_cerberus_parser_alignment_and_trace():
    root=Path(__file__).resolve().parents[2]/'work'/'cerberus'
    if not (root/'manifest.json').exists():pytest.skip('Run scripts/fetch_cerberus.py; no synthetic CERBERUS replacement')
    c=create('cerberus');res=post(c,'cerberus');assert res.status_code==200,res.text
    infos=res.json()['sources'];assert len(infos)==4 and all(s['synthetic'] is False for s in infos)
    result=run(c,mode='retrospective_evaluation',frames_confirmed=True)
    assert len(result['findings'])==2
    online=result['findings'][0]
    assert online['metrics']['count']==14246 and .4<online['metrics']['rmse']<.6
    assert online['alignment']['scale']==1 and online['alignment']['fit_pairs']>2000
    assert all(t['status']=='succeeded' and t['duration_ms']>=0 for t in result['trace'])
    assert all(result['source_hashes'][s['id']]==s['sha256'] for s in infos)
    operational=run(c)
    assert not operational['findings'] and 'reference' not in str([s for s in next(t for t in operational['trace'] if t['tool']=='inspect_signals')['output']['sources'] if 'reference' in s['role']])
    ref=next(s for s in infos if s['role']=='reference')
    assert post(c,'tools',{'tool':'query_window','source_id':ref['id'],'actor':'Engineer'}).status_code==403
    assert post(c,'runs',{'actor':'Engineer','mode':'retrospective_evaluation','frames_confirmed':True,'prefix_seconds':60}).json()['trace'][2]['status']=='failed'


def test_geometry_reference_pairing_and_malformed_sources():
    t=np.linspace(0,20,201);truth=[{'t':float(v),'p':[float(3*np.cos(v)),float(3*np.sin(v)),float(v/10)],'q':[0,0,0,1],'row':i+1} for i,v in enumerate(t)]
    moved=[{**r,'p':[r['p'][0]+12,r['p'][1]-7,r['p'][2]+2]} for r in truth]
    computed=trajectory_error(moved,truth,prefix_seconds=10)
    assert computed['metrics']['rmse']<1e-12
    assert computed['raw_unaligned_residual']['rmse']>10
    with pytest.raises(ValueError):trajectory_error(moved,[{**r,'t':r['t']+50} for r in truth])
    for text in ['1 0 0 nan 0 0 0 1','1 0 0 0 0 0 0 4','1 0 0','1 0 0 0 0 0 0 1\n1 0 0 0 0 0 0 1']:
        with pytest.raises(ValueError):parse_tum(text)


def test_mapping_units_csv_json_new_evidence_and_case_isolation():
    c=create();s=source(c);r1=run(c)
    assert r1['findings'][0]['metrics']['mean']==255
    assert r1['findings'][0]['intervals'][0]['peak']==470
    source(c,[{**DATA[0],'reading':100}]);r2=run(c)
    assert r2['findings'][0]['metrics']['mean']!=r1['findings'][0]['metrics']['mean']
    assert client.get('/runtime/cases/'+c).json()['runs'][0]['id']==r1['id']
    assert post(create(),'tools',{'source_id':s['id'],'tool':'query_window','actor':'Engineer'}).status_code==422
    assert post(c,'tools',{'source_id':s['id'],'tool':'shell','actor':'Engineer'}).status_code==422
    rows,_=parse_industrial('time,machine,metric,reading,units\n2026-01-01T14:02:00Z,EX1,rtt,0.47,s','csv',MAPPING,'EX1')
    assert rows[0]['value']==470
    with pytest.raises(ValueError):parse_industrial(json.dumps([{**DATA[0],'machine':'OTHER'}]),'json',MAPPING,'EX1')
    bad=post(c,'sources',{'name':'../escape','content':'[]','format':'json','frame':'x','source_uri':'x','actor':'x'})
    assert bad.status_code==422
    query=post(c,'tools',{'source_id':s['id'],'tool':'query_window','actor':'Engineer','limit':1}).json()
    assert query['result']['truncated'] and query['result']['samples'][0]['original_record']==DATA[0]


def test_check_authorization_feedback_recalculation_and_no_inferred_action():
    c=create();source(c);r=run(c)
    assert post(c,'execute-check',{'run_id':r['id'],'check_id':'window_query','actor':'Engineer','source':'test://approval'}).status_code==409
    assert feedback(c,'check_review',run_id=r['id'],check_id='window_query',verdict='accepted').status_code==200
    checked=post(c,'execute-check',{'run_id':r['id'],'check_id':'window_query','actor':'Engineer','source':'test://approval'}).json()
    assert checked['trace'][0]['status']=='succeeded' and checked['result']['count']==1
    assert all(e['kind'] not in ('action','outcome') for e in client.get('/runtime/cases/'+c).json()['events'])
    assert feedback(c,'lifecycle',status='closed').status_code==422
    assert feedback(c,'action',linked_id='missing').status_code==422


def close_case(c,verification='unverified',simulated=False):
    ss=source(c,[{**row,'reading':40} for row in DATA]);r=run(c);d=feedback(c,'decision',run_id=r['id']).json();a=feedback(c,'action',linked_id=d['id'],simulated=simulated,executed_at='2026-01-01T14:01:00Z').json()
    start=datetime(2026,1,1,14,2,tzinfo=timezone.utc);end=datetime(2026,1,1,14,6,tzinfo=timezone.utc)
    o=feedback(c,'outcome',linked_id=a['id'],result='recovered',verification=verification,evidence_ids=[ss['id']] if verification=='evidence_attached' else [],conditions=CONTEXT,exclusions='Only this measured router path and operating context',window_start=start.isoformat(),window_end=end.isoformat()).json()
    if verification=='evidence_attached':assert feedback(c,'review_outcome',linked_id=o['id'],actor='Reviewer',verdict='accepted').status_code==200
    assert feedback(c,'lifecycle',status='closed').status_code==200
    return o


def test_outcome_memory_chronology_review_recurrence_and_reopening():
    verified=create();close_case(verified,'evidence_attached')
    unverified=create();close_case(unverified)
    simulated=create();close_case(simulated,'evidence_attached',True)
    current=create();source(current);r=run(current,use_memory=True)
    matches={m['case_id']:m for m in r['memory']}
    assert matches[verified]['eligible'] and not matches[unverified]['eligible'] and not matches[simulated]['eligible']
    future=create();close_case(future,'evidence_attached')
    assert future not in {m['case_id'] for m in client.get('/runtime/cases/'+current+'/memory').json()['matches']}
    assert run(current,use_memory=True)['memory']==r['memory']
    assert feedback(verified,'lifecycle',status='open').status_code==200
    later=create();source(later)
    assert verified not in {m['case_id'] for m in client.get('/runtime/cases/'+later+'/memory').json()['matches']}
    snapshot=client.get('/runtime/cases/'+verified).json()['events'][0]['investigation_snapshot']
    assert snapshot['run_id']


def test_no_llm_eval_reports_unknown_metrics_and_restricts_origins(monkeypatch):
    c=create();source(c);run(c)
    ev=client.get('/runtime/cases/'+c+'/evaluation').json()
    assert ev['arms']['B']['status']=='unavailable' and ev['arms']['C']['external_model_calls']==0
    assert all(v is None for v in ev['metrics'].values())
    assert client.get('/runtime/catalog',headers={'origin':'https://untrusted.example'}).status_code==403
    monkeypatch.setenv('VEYRA_ENABLE_RUNTIME','false')
    assert client.get('/runtime/catalog').status_code==404


def test_baseline_context_guards_history_and_integrity_refusal():
    c=create();s=source(c);source(c,[{**DATA[0],'reading':40}],name='baseline.json',role='baseline')
    first=run(c);assert first['findings'][0]['baseline']['mean_difference']==215
    assert client.get('/runtime/cases/'+c+'/runs/'+first['id']).json()['source_hashes']==first['source_hashes']
    assert client.get('/runtime/cases/'+create()+'/runs/'+first['id']).status_code==404
    from app.db import SessionLocal
    from app.models import RuntimeSource
    with SessionLocal() as db:
        saved=db.get(RuntimeSource,s['id']);saved.content+=' ';db.commit()
    assert post(c,'runs',{'actor':'Engineer'}).status_code==409
    assert post(c,'tools',{'tool':'query_window','source_id':s['id'],'actor':'Engineer'}).status_code==409
    audit=client.get('/runtime/cases/'+c).json()['events'][-1]
    assert audit['kind']=='integrity_failure' and audit['trace'][0]['status']=='failed'
    with pytest.raises(ValueError):parse_industrial(json.dumps([{**DATA[0],'reading':1e308}]),'json',MAPPING,'EX1')


def context_update(c,attributes,version=0,**extra):
    return post(c,'context',{'actor':'Engineer','source':'local://reviewed-machine-sheet','reason':'Checked against available installation records','expected_version':version,'effective_at':'2026-01-01T00:00:00Z','attributes':{k:{'value':v,'status':'engineer_confirmed'} for k,v in attributes.items()},**extra})


def test_context_version_check_ranking_reason_and_stale_execution():
    c=create();source(c);r=run(c)
    assert len(r['industrial_support']['domains'])==4
    assert all(d['root_cause']=='unconfirmed' for d in r['industrial_support']['domains'])
    assert r['checks'][1]['id']=='endpoint_records'
    assert r['checks'][1]['readiness']=='authorization_required'
    assert post(c,'feedback',{'actor':'Engineer','source':'test://review','summary':'Accept','kind':'check_review','run_id':r['id'],'check_id':'window_query','verdict':'accepted'}).status_code==422
    assert feedback(c,'check_review',run_id=r['id'],check_id='window_query',verdict='accepted').status_code==200
    result=context_update(c,{'remote_access':'read_only_authorized','measurement_endpoints':'operator console -> vehicle router'})
    assert result.status_code==200,result.text
    assert context_update(c,{'oem':'Example OEM'}).status_code==409
    assert context_update(c,{'root_cause':'network'},version=1).status_code==422
    assert post(c,'execute-check',{'actor':'Engineer','source':'test://approval','run_id':r['id'],'check_id':'window_query'}).status_code==409
    updated=run(c)
    assert updated['machine_context_snapshot']['version']==1
    assert updated['checks'][1]['readiness']=='manual_ready'
    assert updated['checks'][2]['readiness']=='evidence_collection_required'
    assert client.get('/runtime/cases/'+c+'/runs/'+r['id']).json()['machine_context_snapshot']['version']==0
    restore=run(c,goal='restore_operations')
    assert restore['checks'][0]['id']=='service_continuity' and not restore['checks'][0]['executable']
    check=restore['checks'][1]
    assert check['policy_version']=='industrial-support-v2' and check['context_version']==1


def test_manual_results_dispatch_and_observed_time_not_time_saving():
    c=create();s=source(c);r=run(c)
    assert feedback(c,'manual_check',run_id=r['id'],check_id='command_chain',result_status='observed').status_code==422
    observed=feedback(c,'manual_check',run_id=r['id'],check_id='command_chain',result_status='observed',evidence_ids=[s['id']],executed_at='2026-01-01T14:07:00Z')
    assert observed.status_code==200 and observed.json()['grade']=='engineer_reported_evidence_attached'
    dispatch=feedback(c,'dispatch_plan',run_id=r['id'],remote_resolution='unknown',preparation=['Confirm cable compatibility'],blockers=['Vehicle feedback missing'])
    assert dispatch.status_code==200 and dispatch.json()['support_snapshot']['dispatch']['remote_resolution']=='not_established'
    assert 'approval' in dispatch.json()['boundary']
    assert context_update(c,{'vehicle_interface':'interface-A'}).status_code==200
    assert feedback(c,'dispatch_plan',run_id=r['id'],remote_resolution='possible').status_code==409
    for milestone,when in [('issue_reported','2026-01-01T14:00:00Z'),('triage_started','2026-01-01T14:05:00Z')]:
        assert feedback(c,'support_milestone',milestone=milestone,observed_at=when).status_code==200
    assert feedback(c,'support_milestone',milestone='service_reported_restored',observed_at='2026-01-01T14:10:00Z',simulated=True).status_code==200
    state=client.get('/runtime/cases/'+c).json()
    assert state['support_metrics']['durations'][0]['seconds']==300
    assert state['support_metrics']['durations'][2]['seconds'] is None
    assert state['support_metrics']['handling_time_reduction'] is None
    assert feedback(c,'support_milestone',milestone='triage_started',observed_at='2026-01-01T14:06:00Z').status_code==200
    assert client.get('/runtime/cases/'+c).json()['support_metrics']['durations'][0]['seconds'] is None


def test_contextual_baseline_and_memory_reject_missing_retrofit_conditions():
    c=create();source(c)
    assert context_update(c,{'oem':'OEM A','kit_version':'K1'}).status_code==200
    source(c,[{**DATA[0],'reading':40}],name='baseline.json',role='baseline')
    result=run(c)
    assert result['findings'][0]['baseline'] is None
    assert any('not comparable' in w for w in result['warnings'])
    prior=create();close_case(prior,'evidence_attached')
    current=create();source(current)
    assert context_update(current,{'kit_version':'K2'}).status_code==200
    matches=client.get('/runtime/cases/'+current+'/memory').json()['matches']
    match=next(m for m in matches if m['case_id']==prior)
    assert not match['eligible'] and 'kit_version' in match['machine_context_compatibility']['missing']


def test_context_effective_time_does_not_rewrite_old_incident():
    c=create();source(c)
    result=context_update(c,{'remote_access':'read_only_authorized','measurement_endpoints':'operator -> router'},effective_at='2026-02-01T00:00:00Z')
    assert result.status_code==200
    r=run(c)
    assert r['checks'][1]['readiness']=='authorization_required'
    assert 'measurement_endpoints' in r['industrial_support']['missing_context']
    assert r['machine_context_snapshot']['attributes']['remote_access']['status']=='engineer_confirmed'


def test_presentation_separates_later_normal_window_from_historical_peak():
    c=create();source(c)
    first=run(c)
    followup=[{**DATA[0],'time':'2026-01-01T14:10:00Z','reading':42},{**DATA[0],'time':'2026-01-01T14:11:00Z','reading':44}]
    source(c,followup,name='followup.json')
    second=run(c)
    view=second['presentation'];signal=view['signals'][0]
    assert view['status']=='followup_below_screening_threshold'
    assert signal['historical_peak']==470 and signal['latest']['min']==42 and signal['latest']['max']==44
    assert view['row_count']==4 and not view['recovery_verified']
    old=client.get('/runtime/cases/'+c+'/runs/'+first['id']).json()['presentation']
    assert old['row_count']==2 and old['signals'][0]['trend']=='single_or_overlapping_window'
    overlap=[{**DATA[0],'reading':43}];source(c,overlap,name='overlap.json')
    third=run(c)
    assert third['presentation']['signals'][0]['historical_peak']==470
    from app.db import SessionLocal
    from app.models import RuntimeSource
    with SessionLocal() as db:
        saved=db.get(RuntimeSource,second['source_ids'][0]);saved.content+=' ';db.commit()
    assert client.get('/runtime/cases/'+c).json()['latest']['presentation']['status']=='source_integrity_unavailable'


def test_presentation_does_not_compare_different_probe_paths_or_overlapping_windows():
    c=create();source(c)
    source(c,[{**DATA[0],'reading':40,'endpoint':'unrelated-probe'}],name='other-path.json')
    r=run(c)
    assert len(r['presentation']['signals'])==2
    assert all(not s['sequential_comparison'] for s in r['presentation']['signals'])
    other=create();source(other)
    source(other,[{**DATA[0],'reading':42}],name='overlap.json')
    assert run(other)['presentation']['status']=='needs_review'
