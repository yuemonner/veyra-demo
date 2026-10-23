"""Bounded first-pass analysis. Explicit field/unit patterns; findings are not causes."""
import math
import re
from datetime import datetime, timezone

VERSION = 'source-patterns-v1'
# Illustrative screening thresholds, not OEM safety limits or calibrated fault probabilities.
RULES = {'network_rtt': {'domain':'network','threshold':200,'unit':'ms'},
         'packet_loss': {'domain':'network','threshold':5,'unit':'%'}}
ALIASES = {'rtt_ms':('network_rtt','ms'), 'rtt_s':('network_rtt','s'),
           'packet_loss_pct':('packet_loss','%'), 'packet_loss_percent':('packet_loss','%')}


def stamp(value):
    value = str(value or '').strip()
    try:
        if re.fullmatch(r'\d\d:\d\d(?::\d\d)?',value):
            dt = datetime.strptime(value, '%H:%M:%S' if len(value)==8 else '%H:%M')
            return {'label':value,'axis':'time_of_day_unverified_date','seconds':dt.hour*3600+dt.minute*60+dt.second}
        dt = datetime.fromisoformat(value.replace('Z','+00:00'))
        if dt.tzinfo is None: return None
        dt = dt.astimezone(timezone.utc)
        return {'label':value,'axis':'utc','seconds':dt.timestamp()}
    except (ValueError, OverflowError):
        return None


def text_stamp(text):
    iso = re.search(r'\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:\d\d)?',text)
    if iso: return stamp(iso.group())
    clock = re.search(r'\b\d\d:\d\d(?::\d\d)?\b',text)
    return stamp(clock.group()) if clock else None


def first_pass(imports, asset_id, conversations=()):
    findings, samples, notes, gaps = [], [], [], []
    for imported in imports:
        if imported.get('asset_id') != asset_id: continue
        for number, record in enumerate(imported['records'],1):
            ref = {'import_id':imported['id'],'record':number,'source':imported['source'], 'asset_id':asset_id,'original':record}
            if record.get('asset_id') and str(record['asset_id']) != asset_id:
                gaps.append({**ref,'reason':'Row asset differs from import asset; not analyzed'}); continue
            raw = str(record.get('original_text',''))
            timestamp = stamp(record.get('timestamp') or record.get('time') or record.get('event_time')) if any(record.get(k) for k in ('timestamp','time','event_time')) else text_stamp(raw)
            if str(record.get('status','')).casefold() in ('planned','proposed','forecast'):
                gaps.append({**ref,'reason':'Planned/forecast record; not treated as an observed measurement'}); continue
            values=[]
            for key,(concept,unit) in ALIASES.items():
                if key in record: values.append((concept,record[key],unit,key))
            signal = str(record.get('signal','')).casefold()
            if signal in ('rtt','network_rtt','packet_loss'):
                values.append(('network_rtt' if signal=='rtt' else signal,record.get('value'),record.get('unit'),signal))
            raw_uncertain = bool(re.search(r'\b(will|plan|planned|should|might|maybe|not)\b|计划|可能|未|没有|[?？]',raw,re.I))
            for match in ([] if raw_uncertain else re.finditer(r'\b(RTT|packet[ _]loss)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(ms|s|%|percent|ratio)(?![a-zA-Z])',raw,re.I)):
                values.append(('network_rtt' if match[1].lower()=='rtt' else 'packet_loss',match[2],match[3].lower(),match[1]))
            for concept,value,unit,key in values:
                unit = str(unit).strip()
                try:
                    value = float(value)
                    if not math.isfinite(value) or value<0: raise ValueError()
                    if concept=='network_rtt' and unit in ('s','ms'): normalized=value*(1000 if unit=='s' else 1)
                    elif concept=='packet_loss' and unit in ('%','percent','ratio'): normalized=value*(100 if unit=='ratio' else 1)
                    else: raise ValueError()
                    if concept=='packet_loss' and normalized>100: raise ValueError()
                except (ValueError,TypeError,OverflowError):
                    gaps.append({**ref,'reason':f'Unsupported unit/value for {key}; retained without analysis'}); continue
                if not timestamp:
                    gaps.append({**ref,'reason':'Missing/unusable timestamp; cannot align measurement'}); continue
                sample={**ref,'concept':concept,'value':normalized,'unit':RULES[concept]['unit'],'time':timestamp,
                        'measurement_scope':str(record.get('measurement_scope') or record.get('endpoint') or 'unspecified')}
                samples.append(sample)
                if normalized>RULES[concept]['threshold']:
                    findings.append({**sample,'kind':'threshold_exceeded','domain':'network','rule_id':f'{VERSION}:{concept}',
                                     'threshold':RULES[concept]['threshold'],'summary':f"{concept}: {normalized:g} {sample['unit']} exceeds screening threshold {RULES[concept]['threshold']}", 'grade':'system_detected_requires_review'})
            text = raw or str(record.get('message') or record.get('event') or record.get('description') or '')
            uncertain=bool(re.search(r'\b(will|plan|planned|should|might|maybe|not|never|no|suspect)\b|计划|准备|可能|未|没有|怀疑',text,re.I))
            if text:
                kinds=[]
                if not uncertain and re.search(r'\bcontroller (restarted|rebooted)\b|控制器已重启',text,re.I):
                    kinds.append('controller_restart')
                    findings.append({**ref,'time':timestamp,'kind':'controller_restart','domain':'control_suite','rule_id':VERSION+':explicit-controller-restart','summary':'Source reports controller restart; reason unknown','grade':'source_report_requires_review'})
                if re.search(r'\b(delayed response|control delay|response delay)\b|响应延迟|控制延迟',text,re.I) and not uncertain: kinds.append('symptom_report')
                if re.search(r'\b(checked|inspected|tested)\b|已检查|检查了',text,re.I): kinds.append('reported_check')
                if re.search(r'\b(decided|agreed to)\b|决定|选择',text,re.I): kinds.append('reported_decision')
                if re.search(r'\b(restarted|replaced|rolled back|dispatched)\b|已更换|已回滚|已派遣',text,re.I): kinds.append('reported_action')
                if re.search(r'\b(recovered|resolved|recurred)\b|已恢复|再次发生',text,re.I): kinds.append('reported_outcome')
                if re.search(r'\b(suspect|possibly|hypothesis)\b|怀疑|可能',text,re.I): kinds.append('candidate_judgment')
                if kinds or uncertain:
                    notes.append({**ref,'time':timestamp,'kind':'planned_or_uncertain' if uncertain else kinds[0] if len(kinds)==1 else 'mixed_statement',
                                  'reported_kinds':kinds,'text':text,'status':'draft_requires_engineer_review','creates_workflow_record':False})
            code=record.get('fault_code')
            if code is not None and timestamp:
                findings.append({**ref,'time':timestamp,'kind':'fault_code_observed','domain':'unknown','rule_id':VERSION+':fault-code','summary':f'Fault code {code}; OEM meaning and baseline required','grade':'source_report_requires_review'})
    # Compare only identical asset/concept/scope/time basis. New normal samples never erase old anomalies.
    for concept in RULES:
        group=[s for s in samples if s['concept']==concept]
        for sample in group:
            earlier=[s for s in group if s['measurement_scope']==sample['measurement_scope'] and s['time']['axis']==sample['time']['axis'] and s['time']['seconds']<sample['time']['seconds']]
            if earlier:
                previous=max(earlier,key=lambda s:s['time']['seconds'])
                if concept=='network_rtt' and previous['value']>0 and sample['value']>=3*previous['value'] and sample['value']>200:
                    findings.append({**sample,'kind':'latency_increase','domain':'network','rule_id':VERSION+':rtt-rise','baseline_ref':previous,
                                     'summary':f"RTT rose from {previous['value']:g} to {sample['value']:g} ms within the measured scope",'grade':'system_detected_requires_review'})
    codes=[f for f in findings if f['kind']=='fault_code_observed']
    for current in codes:
        earlier=[f for f in codes if f['time']['axis']==current['time']['axis'] and f['time']['seconds']<current['time']['seconds']]
        if earlier:
            previous=max(earlier,key=lambda f:f['time']['seconds'])
            if previous['original']['fault_code']!=current['original']['fault_code']:
                findings.append({**current,'kind':'fault_code_changed','baseline_ref':previous,'summary':f"Reported fault code changed from {previous['original']['fault_code']} to {current['original']['fault_code']}; OEM interpretation required"})
    # Contradiction means same timestamp and measurement scope; a later normal window is not a refutation.
    conflicts=[]
    for i,a in enumerate(samples):
        for b in samples[i+1:]:
            if a['concept']==b['concept'] and a['measurement_scope']==b['measurement_scope'] and a['time']['axis']==b['time']['axis'] and a['time']['seconds']==b['time']['seconds'] and (a['value']>RULES[a['concept']]['threshold']) != (b['value']>RULES[b['concept']]['threshold']):
                conflicts.append({'kind':'conflicting_measurements','left':a,'right':b,'summary':'Same reported time/scope has normal and abnormal values; resolve source conflict'})
    for convo in conversations:
        for item in convo.get('candidates',[]):
            notes.append({'import_id':convo['id'],'source':convo.get('source'),'record':item['line'],'original':item['excerpt'],'time':text_stamp(item['excerpt']),'kind':'symptom_report' if re.search(r'delayed response|control delay|响应延迟',item['excerpt'],re.I) and not re.search(r'not |will |might|未|计划',item['excerpt'],re.I) else item['kind'],'text':item['proposed_text'],'status':'draft_requires_engineer_review','creates_workflow_record':False})
    correlations=[]
    for finding in findings:
        if finding['domain']!='network' or not finding.get('time'): continue
        for note in notes:
            t=note.get('time')
            if note['kind']=='symptom_report' and t and t['axis']==finding['time']['axis'] and abs(t['seconds']-finding['time']['seconds'])<=120:
                correlations.append({'finding':finding,'symptom':note,'relationship':'within_120_seconds_not_causal','boundary':'Time-only records lack verified date/timezone; proximity is provisional' if t['axis']!='utc' else 'Temporal proximity is not causal proof'})
    domains=[]
    for domain in ('network','control_suite','vehicle_interface','vehicle_body'):
        evidence=[f for f in findings if f['domain']==domain]
        state='conflicting_measurements' if domain=='network' and conflicts else 'prioritize_investigation' if evidence else 'insufficient_evidence'
        domains.append({'domain':domain,'status':state,'finding_count':len(evidence),'excluded':False,'root_cause':'unresolved'})
    normal=[s for s in samples if s['value']<=RULES[s['concept']]['threshold']]
    return {'version':VERSION,'asset_id':asset_id,'findings':findings,'domains':domains,'samples':samples,'normal_sample_count':len(normal),
            'conflicts':conflicts,'temporal_links':correlations,'parse_gaps':gaps,'case_draft':notes,
            'coverage':'Sparse imported samples only. Normal samples constrain only their reported path/timestamps; completeness and symptom coverage are not established.',
            'thresholds':RULES,'boundary':'First-pass pattern detection, not causal inference. Drafts do not execute actions or verify recovery. Unknown vendor fields remain unmapped.'}
