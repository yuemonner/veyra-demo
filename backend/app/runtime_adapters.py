"""Two read-only adapters; no source text can become executable instructions."""
import csv
import io
import json
import math
from datetime import datetime, timezone
import numpy as np

MAX_BYTES=5_000_000
MAX_ROWS=50000


def finite(value):
    try: result=float(value)
    except (ValueError,TypeError,OverflowError): raise ValueError('Expected a finite number')
    if not math.isfinite(result): raise ValueError('Expected a finite number')
    return result


def parse_tum(content):
    samples=[]
    for number,line in enumerate(content.splitlines(),1):
        if not line.strip() or line.lstrip().startswith('#'): continue
        parts=line.replace(',',' ').split()
        if len(parts)!=8: raise ValueError(f'TUM row {number}: expected timestamp x y z qx qy qz qw')
        values=[finite(v) for v in parts]
        if abs(values[0])>1e11 or any(abs(v)>1e7 for v in values[1:4]):raise ValueError(f'TUM row {number}: timestamp/position outside supported magnitude')
        if abs(np.linalg.norm(values[4:])-1)>0.02: raise ValueError(f'TUM row {number}: invalid quaternion norm')
        if samples and values[0]<=samples[-1]['t']: raise ValueError(f'TUM row {number}: timestamps must be strictly increasing')
        samples.append({'t':values[0],'p':values[1:4],'q':values[4:],'row':number})
        if len(samples)>MAX_ROWS: raise ValueError('TUM exceeds 50,000 samples')
    if len(samples)<3: raise ValueError('At least three trajectory poses required')
    return samples,[]


def timestamp(value, unit):
    if unit=='iso8601':
        try: dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        except ValueError: raise ValueError('Invalid ISO timestamp')
        if dt.tzinfo is None: raise ValueError('ISO timestamps require a timezone')
        return dt.astimezone(timezone.utc).timestamp()
    if unit not in ('s','ms','us','ns'): raise ValueError('Explicit timestamp unit required')
    result=finite(value)/{'s':1,'ms':1000,'us':1e6,'ns':1e9}[unit]
    if abs(result)>1e11:raise ValueError('Normalized timestamp outside supported magnitude')
    return result


def parse_industrial(content,fmt,mapping,asset):
    if fmt=='csv':
        reader=csv.DictReader(io.StringIO(content));headers=reader.fieldnames or []
        if not headers or len(headers)>60 or len(headers)!=len(set(headers)) or any(not h.strip() for h in headers): raise ValueError('CSV headers must be unique and nonempty')
        records=list(reader)
        if any(None in r or any(v is None for v in r.values()) for r in records): raise ValueError('CSV row width mismatch')
    else:
        try: records=json.loads(content,parse_float=finite,parse_constant=finite)
        except (ValueError,RecursionError): raise ValueError('Invalid finite JSON')
        if not isinstance(records,list) or not all(isinstance(r,dict) for r in records): raise ValueError('Industrial JSON must be an array of objects')
    if not 1<=len(records)<=MAX_ROWS: raise ValueError('Import 1–50,000 rows')
    required=('timestamp','asset','signal','value','unit')
    if not all(isinstance(mapping.get(k),str) and mapping[k] for k in required): raise ValueError('Map timestamp, asset, signal, value and unit columns explicitly')
    samples=[];warnings=[]
    for i,row in enumerate(records,1):
        if any(mapping[k] not in row for k in required): raise ValueError(f'Row {i}: missing a mapped column')
        if str(row[mapping['asset']])!=asset: raise ValueError(f'Row {i}: asset differs from this case; split machines before import')
        signal=str(row[mapping['signal']]); unit=str(row[mapping['unit']]); value=finite(row[mapping['value']]);original_unit=unit
        if abs(value)>1e12:raise ValueError(f'Row {i}: scalar value exceeds supported magnitude')
        if signal=='rtt' and unit in ('s','ms','us'): value*= {'s':1000,'ms':1,'us':.001}[unit];unit='ms'
        if signal=='packet_loss' and unit=='ratio': value*=100;unit='%'
        if signal=='packet_loss' and (unit!='%' or not 0<=value<=100): raise ValueError(f'Row {i}: packet_loss requires percent or ratio in range')
        if signal=='rtt' and (unit!='ms' or value<0): raise ValueError(f'Row {i}: RTT requires ms/s/us and nonnegative value')
        if signal not in ('rtt','packet_loss','controller_cycle'): warnings.append(f'{signal}: no anomaly threshold configured; statistics only')
        samples.append({'t':timestamp(row[mapping['timestamp']],mapping.get('time_unit','iso8601')),'asset':asset,'signal':signal,'value':value,'unit':unit,'original_unit':original_unit,'row':i,
                        'scope':str(row.get(mapping.get('scope',''),'unspecified')),'event_type':str(row.get(mapping.get('event_type',''),'')),
                        'version':str(row.get(mapping.get('version',''),''))})
    samples.sort(key=lambda x:x['t'])
    return samples,sorted(set(warnings))


def parse_source(content,adapter,fmt,mapping,asset):
    if not content or len(content.encode())>MAX_BYTES: raise ValueError('Source must be nonempty and at most 5 MB')
    return parse_tum(content) if adapter=='cerberus' else parse_industrial(content,fmt,mapping,asset)
