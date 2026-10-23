"""Presentation summaries from exact run inputs, not from the newest mutable case state."""
from datetime import datetime,timezone
import hashlib
import json

def iso(t):
    return datetime.fromtimestamp(t,timezone.utc).isoformat()

def summarize(run, sources):
    selected=[s for s in sources if s.id in run['source_ids']]
    if len(selected)!=len(run['source_ids']) or any(hashlib.sha256(s.content.encode()).hexdigest()!=run['source_hashes'].get(s.id) or hashlib.sha256(json.dumps(s.samples,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()!=s.payload.get('normalized_sha256') for s in selected):
        return {'status':'source_integrity_unavailable','signals':[],'source_count':0,'row_count':None,'recovery_verified':False,'boundary':'Original run inputs failed integrity validation; no new comparison calculated.'}
    permitted={s.id:s for s in sources if s.id in run['source_ids'] and s.payload['role']=='observation'}
    groups={}
    for sid,source in permitted.items():
        for row in source.samples:
            groups.setdefault((row['signal'],row['unit'],row['scope']),{}).setdefault(sid,[]).append(row)
    signals=[]
    for (signal,unit,scope),batches in groups.items():
        windows=[]
        for sid,rows in batches.items():
            ordered=sorted(rows,key=lambda r:r['t']);values=[r['value'] for r in ordered]
            windows.append({'source_id':sid,'source_name':permitted[sid].payload['name'],'start':iso(ordered[0]['t']),'end':iso(ordered[-1]['t']),'count':len(rows),'min':min(values),'max':max(values),'last':ordered[-1]['value'],'last_row':ordered[-1]['row']})
        windows.sort(key=lambda w:(w['end'],w['source_id']))
        first=min(windows,key=lambda w:(w['start'],w['source_id']));latest=windows[-1]
        finding=next((f for f in run['findings'] if f['label']==signal+' · '+scope),{})
        threshold=finding.get('threshold');comparable=len(windows)>1 and first['source_id']!=latest['source_id'] and latest['start']>first['end']
        trend=('lower_after_threshold_crossing' if comparable and threshold is not None and first['max']>threshold and latest['max']<=threshold else 'later_threshold_crossing' if comparable and threshold is not None and latest['max']>threshold else 'later_window_available' if comparable else 'single_or_overlapping_window')
        signals.append({'signal':signal,'unit':unit,'scope':scope,'historical_peak':max(w['max'] for w in windows),'earlier':first,'latest':latest,'sequential_comparison':comparable,'trend':trend,'threshold':threshold,'windows':windows})
    comparable=[s for s in signals if s['sequential_comparison']]
    status='followup_below_screening_threshold' if comparable and all(s['trend']=='lower_after_threshold_crossing' for s in comparable) and len(comparable)==len(signals) else 'followup_still_has_crossing' if any(s['trend']=='later_threshold_crossing' for s in signals) else 'needs_review'
    return {'method':'same-signal-unit-scope per-source windows, all original samples; sequential only when windows do not overlap','source_hashes':run['source_hashes'],'status':status,'signals':signals,'source_count':len(permitted),'row_count':sum(len(s.samples) for s in permitted.values()),'recovery_verified':False,'boundary':'Sequential source windows in the same signal/unit/scope only. Screening thresholds are illustrative, not OEM limits; later low values do not establish recovery or cause.'}
