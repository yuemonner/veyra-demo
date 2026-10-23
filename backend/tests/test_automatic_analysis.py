import json
from test_veyra_v0 import client, reset
from test_diagnostic_engine import post
from test_decision_workflow import fresh
from app.automatic_analysis import first_pass


def remote():
    response=client.post('/demo/remote-support-sessions')
    assert response.status_code==200, response.text
    return response.json()


def import_rows(case,asset,rows,source='synthetic://test/network'):
    response=post(case,'evidence',{'owner':'Test engineer','source':source,'asset_id':asset,'evidence_type':'network_metrics','format':'json','content':json.dumps(rows)})
    assert response.status_code==200,response.text
    return response.json()


def analysis(case):
    return client.get(f'/investigations/{case}/engineering').json()['diagnostics']


ANOMALY=[{'timestamp':'2026-10-16T14:02:00Z','rtt_ms':42,'endpoint':'vehicle-router'},
         {'timestamp':'2026-10-16T14:06:00Z','rtt_ms':470,'packet_loss_pct':18,'endpoint':'vehicle-router'},
         {'timestamp':'2026-10-16T14:07:00Z','message':'Operator reports delayed response'}]


def test_import_detects_patterns_aligns_symptom_and_changes_checks_with_source_lineage():
    reset(); r=remote();case=r['investigation_id'];asset=r['cohorts'][0]['asset_id']
    before=analysis(case)
    assert all(d['status']=='insufficient_evidence' for d in before['automatic_analysis']['domains'])
    assert len(before['checks'])==4 and all('EX11' not in c['label'] for c in before['checks'])
    saved=import_rows(case,asset,ANOMALY)
    after=analysis(case);auto=after['automatic_analysis']
    assert {f['kind'] for f in auto['findings']}=={'threshold_exceeded','latency_increase'}
    assert {f['import_id'] for f in auto['findings']}=={saved['id']}
    assert all(f['record']==2 for f in auto['findings'])
    assert len(auto['temporal_links'])==3
    assert auto['domains'][0]['status']=='prioritize_investigation'
    assert all(not d['excluded'] for d in auto['domains'])
    assert after['checks'][0]['domain']=='network' and 'base-station' in after['checks'][0]['label']
    assert saved['id'] in after['checks'][0]['available_evidence_ids']
    assert after['assessment']['root_cause_status']=='unresolved'
    assert auto['case_draft'][0]['kind']=='symptom_report'
    assert client.get(f'/investigations/{case}/workflow').json()['actions']==[]
    reread=analysis(case)['automatic_analysis']
    assert reread==auto


def test_normal_data_does_not_erase_earlier_anomaly_and_overlapping_conflict_is_explicit():
    reset();r=remote();case=r['investigation_id'];asset=r['cohorts'][0]['asset_id']
    import_rows(case,asset,ANOMALY)
    import_rows(case,asset,[{'timestamp':'2026-10-16T14:10:00Z','rtt_ms':44,'packet_loss_pct':0,'endpoint':'vehicle-router'}])
    auto=analysis(case)['automatic_analysis']
    assert auto['domains'][0]['status']=='prioritize_investigation' and not auto['conflicts']
    assert auto['normal_sample_count']==3 and 'completeness' in auto['coverage']
    import_rows(case,asset,[{'timestamp':'2026-10-16T14:06:00Z','rtt_ms':44,'endpoint':'vehicle-router'}])
    diag=analysis(case)
    assert diag['automatic_analysis']['domains'][0]['status']=='conflicting_measurements'
    assert diag['checks'][0]['priority']=='review_conflict_first'


def test_units_unknown_fields_assets_and_timezones_do_not_fabricate_findings():
    def run(rows):return first_pass([{'id':'import-1','source':'synthetic://test','asset_id':'A','records':rows}],'A')
    assert run([{'timestamp':'2026-10-16T14:06:00Z','signal':'rtt','value':0.470,'unit':'s'}])['findings'][0]['value']==470
    unknown=run([{'timestamp':'2026-10-16T14:06:00Z','latency':470},{'timestamp':'14:06','signal':'rtt','value':470,'unit':'ticks'}, {'timestamp':'2026-10-16T14:06:00','rtt_ms':470}, {'timestamp':'14:06','rtt_ms':470,'asset_id':'B'}])
    assert not unknown['findings'] and len(unknown['parse_gaps'])==3
    mixed=run([{'timestamp':'14:06','rtt_ms':470},{'timestamp':'2026-10-16T14:07:00Z','message':'Operator reports delayed response'}])
    assert not mixed['temporal_links']
    assert not run([{'timestamp':'14:06','packet_loss_pct':180}])['findings']
    assert not run([{'timestamp':'14:06','rtt_ms':float('nan')}])['findings']


def test_text_logs_restart_code_changes_and_drafts_keep_plans_separate_from_execution():
    raw=[{'original_text':'14:02 RTT 42 ms'},{'original_text':'14:06 RTT 470 ms; packet loss 18%'},
         {'original_text':'14:07 Operator reports delayed response'}, {'original_text':'14:08 We will restart controller; not recovered'},
         {'original_text':'14:09 Controller restarted'}, {'timestamp':'14:10','fault_code':'U0100'}, {'timestamp':'14:11','fault_code':'U0101'}]
    auto=first_pass([{'id':'raw','source':'synthetic://log','asset_id':'A','records':raw}],'A')
    assert any(f['kind']=='controller_restart' and f['record']==5 for f in auto['findings'])
    assert any(f['kind']=='fault_code_changed' for f in auto['findings'])
    plan=next(n for n in auto['case_draft'] if n['record']==4)
    assert plan['kind']=='planned_or_uncertain' and not plan['creates_workflow_record']
    assert auto['temporal_links'] and all('provisional' in l['boundary'] for l in auto['temporal_links'])


def test_review_draft_is_persisted_source_bound_and_does_not_create_actions_or_verification():
    reset();r=remote();case=r['investigation_id'];asset=r['cohorts'][0]['asset_id']
    saved=import_rows(case,asset,[{'timestamp':'2026-10-16T14:07:00Z','message':'We will restart controller; not recovered'}])
    payload={'owner':'Engineer','import_id':saved['id'],'record':1,'text':'Restart is planned, not executed.'}
    assert post(case,'draft-reviews',payload).status_code==200
    state=client.get(f'/investigations/{case}/engineering').json()
    assert state['draft_reviews'][0]['source_draft']['original']==saved['records'][0]
    assert not state['draft_reviews'][0]['creates_workflow_record']
    workflow=client.get(f'/investigations/{case}/workflow').json()
    assert workflow['actions']==[] and workflow['verifications']==[]
    assert post(remote()['investigation_id'],'draft-reviews',payload).status_code==422


def test_case_templates_isolate_evidence_decisions_and_immutable_diagnostic_snapshot():
    reset();fleet=fresh();a=remote();b=remote();case=a['investigation_id'];asset=a['cohorts'][0]['asset_id']
    assert len(client.get(f'/investigations/{fleet}/engineering').json()['diagnostics']['checks'])==1
    assert 'EX11' in client.get(f'/investigations/{fleet}/engineering').json()['diagnostics']['checks'][0]['label']
    saved=import_rows(case,asset,ANOMALY)
    assert not analysis(b['investigation_id'])['automatic_analysis']['findings']
    wf=client.get(f'/investigations/{case}/workflow').json()
    assert not {'rollback','inspect'} & {o['id'] for o in wf['options']}
    decision={'option_id':'await_evidence','owner':'Engineer','rationale':'Review router logs','constraints':'Uncertain network scope','approval':'not_required'}
    frozen=client.post(f'/investigations/{case}/workflow/decisions',json=decision).json()
    assert frozen['diagnostic_snapshot']['automatic_analysis']['findings'][0]['import_id']==saved['id']
    assert client.post(f'/investigations/{case}/workflow/decisions',json={**decision,'option_id':'rollback'}).status_code==422
    import_rows(case,asset,[{'timestamp':'2026-10-16T14:10:00Z','rtt_ms':40}])
    assert client.get(f'/investigations/{case}/workflow').json()['decisions'][0]['diagnostic_snapshot']==frozen['diagnostic_snapshot']
    main=client.get(f'/investigations/{case}/analysis').json()
    assert main['case_type']=='remote_support' and not main['hypotheses'] and main['historical_reference'] is None


def test_csv_log_clock_quality_and_planned_metrics_are_not_invented_observations():
    reset();r=remote();case=r['investigation_id'];asset=r['cohorts'][0]['asset_id']
    content='timestamp,signal,value,unit,endpoint\n2026-10-16T14:06:00Z,rtt,0.47,s,vehicle-router\n'
    saved=post(case,'evidence',{'owner':'Test engineer','source':'synthetic://csv','asset_id':asset,'evidence_type':'network_metrics','format':'csv','content':content}).json()
    auto=analysis(case)['automatic_analysis']
    assert auto['findings'][0]['value']==470 and auto['findings'][0]['original']['unit']=='s'
    assert auto['findings'][0]['import_id']==saved['id']
    rows=[{'original_text':'2026-10-16T14:07:00Z RTT 480 ms'}, {'original_text':'14:08 We will test RTT 470 ms'}, {'timestamp':'14:08','rtt_ms':470,'status':'planned'}]
    checked=first_pass([{'id':'log','source':'synthetic://log','asset_id':asset,'records':rows}],asset)
    assert len(checked['findings'])==1 and checked['findings'][0]['time']['axis']=='utc'
    assert all(n['creates_workflow_record'] is False for n in checked['case_draft'])
