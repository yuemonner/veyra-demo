"""Industrial support policy: explicit context and reviewable ordinal check selection."""
from datetime import datetime

CONTEXT_FIELDS = ('oem','model','kit_version','control_version','harness_revision','vehicle_interface','network_path','measurement_endpoints','environment','operating_conditions','site','remote_access','available_tools')
POLICY_VERSION = 'industrial-support-v2'

def known(value):
    return bool(value and str(value).strip().casefold() not in ('unknown','unverified','not recorded','未记录'))

def snapshot(case):
    return case.payload.get('machine_context', {'version':0,'attributes':{},'boundary':'Legacy context; provenance not recorded'})

def value(context, key):
    attribute=context.get('attributes',{}).get(key,{})
    return attribute.get('value') if attribute.get('status')=='engineer_confirmed' else None

def context_at(context, timestamp):
    attributes={}
    for key,attribute in context.get('attributes',{}).items():
        applicable=attribute.get('effective_at') and datetime.fromisoformat(attribute['effective_at']).timestamp()<=timestamp
        attributes[key]={**attribute,'status':attribute.get('status') if applicable else 'not_applicable_at_evidence_time'}
    return {**context,'attributes':attributes,'evidence_window_end':timestamp}


def compatibility(left, right, keys):
    missing=[k for k in keys if not known(left.get(k)) or not known(right.get(k))]
    mismatch=[k for k in keys if known(left.get(k)) and known(right.get(k)) and left[k].strip().casefold()!=right[k].strip().casefold()]
    return {'comparable':not missing and not mismatch,'missing':missing,'mismatched':mismatch,'required':list(keys)}

def plan(context, findings, source_ids, goal):
    abnormal=[f for f in findings if f['intervals'] and f['label'].split(' · ')[0] in ('rtt','packet_loss')]
    keys=('oem','model','kit_version','control_version','vehicle_interface','network_path','measurement_endpoints','environment','operating_conditions')
    missing=[k for k in keys if not known(value(context,k))]
    remote=value(context,'remote_access')=='read_only_authorized'
    endpoints=known(value(context,'measurement_endpoints'))
    domains=[{'id':d,'status':'priority_investigation' if d=='network' and abnormal else 'still_possible','support_ids':[f['id'] for f in abnormal] if d=='network' else [],'root_cause':'unconfirmed'} for d in ('network','control_suite','vehicle_interface','vehicle_body')]
    candidates=[
      {'id':'window_query','question':'Inspect the original measurements in the anomaly window','tool':'query_anomaly_window','executable':bool(source_ids),'required':['Imported measurements'],'required_signals':['rtt','packet_loss'],'distinguishes':['measurement anomaly','coverage gap'],'why':'Review source rows and coverage before attributing delay.','expected':{'measurement_anomaly':'Threshold crossings are present in the measured path','coverage_gap':'Incomplete or conflicting samples limit inference'},'constraints':'Local imported data only; no device connection','readiness':'ready' if source_ids else 'missing_evidence','location':'local','personnel':'Remote support engineer','tools':['Source viewer'],'disruption':'None from reading imported data','priority_reason':'Exact source review is available without device access.'},
      {'id':'endpoint_records','question':'Check whether the RTT probe measures the actual command path','tool':None,'executable':False,'required':['Probe endpoints','Command send/receive endpoints','Router/base-station records'],'required_signals':['rtt','command_send','command_receive'],'distinguishes':['network transport delay','different measurement path'],'why':'A healthy unrelated probe cannot exclude command-path delay.','expected':{'network':'Matched path has delayed receive','different_path':'Existing probe cannot assess this command path'},'constraints':'Read-only logs; Owner authorization required','readiness':'manual_ready' if remote else 'authorization_required','location':'remote','personnel':'Network/support engineer','tools':['Router log export'],'disruption':'Read-only export; load impact must be checked','priority_reason':'Network anomalies raise this check for diagnostic review.' if abnormal else 'Verify endpoint relevance before interpreting normal network metrics.'},
      {'id':'command_chain','question':'Find where the same command first becomes delayed','tool':None,'executable':False,'required':['Command sequence ID','Send/receive timestamps','Controller output','Vehicle feedback'],'required_signals':['command_id','command_send','command_receive','controller_output','vehicle_feedback'],'distinguishes':['network','control_suite','vehicle_interface','vehicle_body'],'why':'Separate delivery, processing and actuation stages; missing feedback keeps downstream domains open.','expected':{'network':'Delay before receipt','control_suite':'Delay between receipt and output','vehicle_interface_or_body':'Output exists but feedback is delayed or absent'},'constraints':'Existing records only; no test actuation','readiness':'manual_ready' if remote and endpoints else 'missing_context' if remote else 'authorization_required','location':'remote','personnel':'Controls engineer','tools':['Controller log export','Command-ID correlation'],'disruption':'No control changes','priority_reason':'Known endpoints permit a meaningful command-chain comparison.' if endpoints else 'Endpoint mapping is required before command-chain interpretation.'},
      {'id':'service_continuity','question':'Review an approved service-restoration option with the site owner','tool':None,'executable':False,'required':['Site impact','OEM-approved procedure','Operational approval','Rollback/safety prerequisites'],'required_signals':[],'distinguishes':['restoration feasible','restoration blocked'],'why':'Restoring operations and reducing uncertainty are separate objectives. Do not assume a diagnostic check restores service.','expected':{'approved_option':'Human team can select an approved intervention','blocked':'Keep equipment state unchanged and escalate'},'constraints':'Planning only; no restart, rollback or machine commands','readiness':'human_approval_required','location':'remote coordination','personnel':'Site owner + authorized controls engineer','tools':['Approved service procedure'],'disruption':'Unknown until reviewed','priority_reason':'Prioritize a human operational decision when the goal is service restoration.'}
    ]
    available={f['label'].split(' · ')[0] for f in findings}
    for check in candidates:
        check['available_signals']=sorted(available & set(check['required_signals']))
        check['missing_signals']=sorted(set(check['required_signals'])-available)
        if check['id']=='command_chain' and check['readiness']=='manual_ready' and check['missing_signals']:
            check['readiness']='evidence_collection_required'
    order=(['service_continuity','window_query','endpoint_records','command_chain'] if goal=='restore_operations' else ['window_query','endpoint_records','command_chain','service_continuity'] if abnormal or not endpoints else ['window_query','command_chain','endpoint_records','service_continuity'])
    for ch in candidates:
        ch.update(evidence_ids=source_ids,policy_version=POLICY_VERSION,context_version=context['version'],goal=goal,rank=order.index(ch['id'])+1,cost='unknown',duration='unknown',ranking_basis='Ordinal policy from goal, measured anomalies, endpoint knowledge and permission; no calibrated information-gain score')
    checks=sorted(candidates,key=lambda ch:ch['rank'])
    dispatch={'status':'preparation_incomplete','remote_resolution':'not_established','before_dispatch':[{'item':k,'status':'missing_context'} for k in missing]+[{'item':'Remote checks and site/OEM approval','status':'engineer_review_required'}], 'bring':[{'item':'Compatible diagnostic cable and OEM-approved service tooling','status':'confirm_against_interface_and_kit','basis':'Preparation candidate; not a confirmed required part'},{'item':'Replacement parts','status':'not_determined','basis':'No component fault has been confirmed'}], 'boundary':'Preparation support only. No dispatch approval, damaged-part identification or safety clearance.'}
    return {'policy_version':POLICY_VERSION,'goal':goal,'context_version':context['version'],'missing_context':missing,'domains':domains,'checks':checks,'dispatch':dispatch}
