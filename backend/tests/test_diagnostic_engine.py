from datetime import datetime
from test_veyra_v0 import client, reset
from test_decision_workflow import fresh, decision, action, verification
from test_field_capabilities import domain, signal


def post(case, path, data):
    return client.post(f'/investigations/{case}/engineering/{path}', json=data)


def profile(**changes):
    return {'owner':'Engineer','source':'report://machine-context','asset_id':'EX03','oem':'OEM A','model':'Model A','retrofit_version':'K2','environment':'yard','vehicle_interface':'CAN-A','harness_revision':'H3','control_software':'C2','network_configuration':'private APN','location':'Yard A','customer_impact':'Three machines unavailable; production loss not measured','remote_status':'accessible','dispatch_risk':'Site access and specialist availability unresolved','exposure_scope':'Yard low-load shift A','observation_window':'09:00-09:30 UTC','window_complete':True,'signal_state':'stop_reported',**changes}


def test_machine_context_blocks_invalid_fleet_controls_without_forcing_comparison():
    reset(); case=fresh()
    assert client.get(f'/investigations/{case}/engineering').json()['fleet']['comparison_status']=='no_valid_control_do_not_force_comparison'
    assert post(case,'context',profile()).status_code==200
    assert post(case,'context',profile(asset_id='EX11',signal_state='healthy_observed',window_complete=False)).status_code==422
    assert post(case,'context',profile(asset_id='EX11',signal_state='healthy_observed',observation_window='unknown')).status_code==422
    assert post(case,'context',profile(asset_id='EX11',signal_state='healthy_observed',retrofit_version='K9')).status_code==200
    fleet=client.get(f'/investigations/{case}/engineering').json()['fleet']
    assert fleet['valid_control_count']==0 and 'retrofit_version' in fleet['machines'][0]['mismatched']
    assert post(case,'context',profile(asset_id='EX11',signal_state='healthy_observed')).status_code==200
    assert client.get(f'/investigations/{case}/engineering').json()['fleet']['valid_control_count']==1
    post(case,'context',profile(asset_id='EX11',signal_state='healthy_observed',model='unknown'))
    assert client.get(f'/investigations/{case}/engineering').json()['fleet']['valid_control_count']==0


def test_import_preserves_csv_units_raw_fault_codes_and_rejects_malformed_records():
    reset(); case=fresh()
    body={'owner':'Engineer','source':'log://controller/1','asset_id':'EX03','evidence_type':'network_metrics','format':'csv','content':'vendor_field,value,unit,timestamp\nrtt,0.125,s,09:01\n'}
    imported=post(case,'evidence',body)
    assert imported.status_code==200
    record=imported.json()
    assert record['content']==body['content'] and record['records'][0]['unit']=='s'
    assert record['semantic_mapping']=='not_inferred'
    assert post(case,'evidence',{**body,'content':'x,x\n1,2'}).status_code==422
    assert post(case,'evidence',{**body,'format':'json','content':'[1,2]'}).status_code==422
    assert post(case,'evidence',{**body,'format':'json','content':'{"value":1e999}'}).status_code==422
    assert post(case,'evidence',{**body,'format':'json','content':'{"value":NaN}'}).status_code==422
    assert post(case,'evidence',{**body,'format':'json','evidence_type':'fault_codes','content':'{"vendor_code":"U-0100","unit":"vendor-code"}'}).json()['records'][0]['vendor_code']=='U-0100'
    analysis=client.get(f'/investigations/{case}/analysis').json()
    assert any(e['id']==record['id'] for e in analysis['evidence'])
    assert all(record['id'] not in [e['id'] for e in h['supporting_evidence']] for h in analysis['hypotheses'])


def test_fault_domain_isolation_does_not_promote_a_root_cause():
    reset(); case=fresh()
    assessment={'owner':'Engineer','fault_domain':'vehicle_interface','domain_status':'engineer_reported_isolated','rationale':'Network path tested; interface findings remain to investigate','root_cause_status':'unresolved','root_cause':None,'evidence_ids':[],'source':'report://triage','limitations':'Exact failed connector/module is not known'}
    assert post(case,'assessments',assessment).status_code==422
    ref=client.post(f'/investigations/{case}/field-tools/domain-checks',json=domain()).json()
    assessment['evidence_ids']=[ref['id']]
    assert post(case,'assessments',assessment).status_code==200
    assert client.get(f'/investigations/{case}/analysis').json()['diagnostic_assessment']['root_cause_status']=='unresolved'
    state=client.get(f'/investigations/{case}/engineering').json()['diagnostics']
    assert state['assessment']['fault_domain']=='vehicle_interface'
    assert state['assessment']['root_cause_status']=='unresolved' and state['assessment']['root_cause'] is None
    assert state['checks'][0]['domain']=='vehicle_interface'
    preparation=client.get(f'/investigations/{case}/field-tools').json()['dispatch']
    assert preparation['fault_domain_assessment']['fault_domain']=='vehicle_interface'
    assert preparation['domain_tool_suggestions'][0]['domain']=='Vehicle interface'
    assert post(case,'assessments',{**assessment,'root_cause_status':'engineer_reported_confirmed'}).status_code==422
    assert post(fresh(),'assessments',assessment).status_code==422


def test_diagnostic_planner_explains_evidence_gaps_constraints_and_asset_linkage():
    reset(); case=fresh()
    base=client.get(f'/investigations/{case}/engineering').json()['diagnostics']
    assert len(base['checks'])==1 and base['checks'][0]['domain']=='fleet_comparison'
    assert all(c['missing_evidence'] for c in base['checks'])
    ref=post(case,'evidence',{'owner':'Engineer','source':'log://network','asset_id':'EX03','evidence_type':'network_metrics','format':'text','content':'rtt=125ms during symptom'}).json()['id']
    post(case,'context',profile())
    unlinked=client.post(f'/investigations/{case}/field-tools/signals',json=signal()).json()['id']
    linked=client.post(f'/investigations/{case}/field-tools/signals',json=signal(asset_id='EX03')).json()['id']
    net=next(c for c in client.get(f'/investigations/{case}/engineering').json()['diagnostics']['checks'] if c['domain']=='network')
    assert ref in net['available_evidence_ids'] and linked in net['available_evidence_ids'] and unlinked not in net['available_evidence_ids']
    assert net['rule_id'] and net['personnel'] and net['risk'] and net['separates']==['network','control_suite']
    assert net['remote_feasibility']=='read_only_if_access_confirmed'


def test_decision_snapshot_preserves_context_and_diagnostic_state_at_decision_time():
    reset(); case=fresh()
    post(case,'context',profile())
    saved=decision(case).json()
    assert saved['context_at_decision']['retrofit_version']=='K2'
    assert saved['diagnostic_snapshot']['assessment']['root_cause_status']=='unresolved'
    assert saved['diagnostic_snapshot']['machine_context']['customer_impact']==profile()['customer_impact']
    assert saved['diagnostic_snapshot']['machine_context']['dispatch_risk']==profile()['dispatch_risk']
    post(case,'context',profile(retrofit_version='K3'))
    reread=client.get(f'/investigations/{case}/workflow').json()['decisions'][0]
    assert reread['context_at_decision']['retrofit_version']=='K2'
    assert reread['diagnostic_snapshot']==saved['diagnostic_snapshot']


def test_changes_do_not_invent_execution_and_costs_require_traceable_basis():
    reset(); case=fresh()
    changed=post(case,'changes',{'owner':'Engineer','source':'note://plan','asset_id':'EX03','change_type':'control_software','before':'C1','after':'C2','effective_time':'planned tomorrow','status':'planned','constraints':'Approval pending'}).json()
    assert changed['context_automatically_updated'] is False
    assert client.get(f'/investigations/{case}/workflow').json()['actions']==[]
    d=decision(case).json();a=action(case,d).json()
    assert verification(case,a,field_cost=500).status_code==422
    v=verification(case,a,field_cost=500,currency='EUR',cost_source='invoice://field-visit',cost_basis='engineer_reported_actual',engineering_hours=2.5)
    assert v.status_code==200
    assert v.json()['observed_window_minutes']>0 and v.json()['field_cost']==500
    replacement={key:value for key,value in a.items() if key not in ['id','recorded_at','status']}
    replacement['action_type']='part_replacement';replacement['executed_at']=datetime.utcnow().isoformat()
    assert client.post(f'/investigations/{case}/workflow/actions',json=replacement).status_code==422
