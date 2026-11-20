"""Bounded analytical primitives. No eval, generated code, shell or arbitrary SQL."""
import time
import numpy as np


def statistics(values):
    v=np.asarray(values,dtype=float)
    if len(v)==0: return {'available':False,'reason':'No samples'}
    return {'available':True,'count':len(v),'mean':float(v.mean()),'min':float(v.min()),'max':float(v.max()),'p95':float(np.quantile(v,.95)),'std':float(v.std()),'rmse':float(np.sqrt(np.mean(v*v)))}


def intervals(times,values,threshold,gap):
    result=[]; current=None
    for t,v in zip(times,values):
        if v<=threshold: current=None;continue
        if current is None or t-current['end']>gap:
            current={'start':float(t),'end':float(t),'count':1,'peak':float(v)};result.append(current)
        else: current.update(end=float(t),count=current['count']+1,peak=max(current['peak'],float(v)))
    return result


def associate(estimate,reference,tolerance):
    rt=np.array([r['t'] for r in reference]);et=np.array([r['t'] for r in estimate]); idx=np.searchsorted(rt,et)
    right=np.clip(idx,0,len(rt)-1);left=np.clip(idx-1,0,len(rt)-1)
    chosen=np.where(abs(rt[left]-et)<=abs(rt[right]-et),left,right)
    good=(abs(rt[chosen]-et)<=tolerance)&(et>=rt[0])&(et<=rt[-1])
    # Each reference sample used at most once; nearest estimate wins.
    candidates=sorted(np.where(good)[0],key=lambda j:abs(rt[chosen[j]]-et[j]));used=set();pairs=[]
    for i in candidates:
        if int(chosen[i]) not in used: pairs.append((int(i),int(chosen[i])));used.add(int(chosen[i]))
    return sorted(pairs)


def trajectory_error(estimate,reference,tolerance=.02,alignment='se3_prefix',prefix_seconds=300):
    pairs=associate(estimate,reference,tolerance)
    if len(pairs)<3: raise ValueError('Fewer than three unique timestamp pairs; no trajectory error computed')
    times=np.array([estimate[i]['t'] for i,j in pairs]); x=np.array([estimate[i]['p'] for i,j in pairs]);y=np.array([reference[j]['p'] for i,j in pairs])
    rotation=np.eye(3); translation=np.zeros(3);fit=np.zeros(len(times),dtype=bool)
    if alignment=='se3_prefix':
        fit=times<=times[0]+prefix_seconds
        if fit.sum()<3: raise ValueError('Alignment prefix needs three poses')
        xc=x[fit]-x[fit].mean(0); yc=y[fit]-y[fit].mean(0)
        if any(np.linalg.matrix_rank(z,tol=1e-5)<2 or np.linalg.svd(z,compute_uv=False)[1]/max(np.linalg.svd(z,compute_uv=False)[0],1e-9)<.05 for z in (xc,yc)): raise ValueError('Alignment prefix is geometrically degenerate; increase the prefix or supply validated common-frame data')
        u,_,vt=np.linalg.svd(xc.T@yc);rotation=vt.T@u.T
        if np.linalg.det(rotation)<0: vt[-1]*=-1;rotation=vt.T@u.T
        translation=y[fit].mean(0)-rotation@x[fit].mean(0)
    elif alignment!='known_common_frame': raise ValueError('Choose se3_prefix or known_common_frame')
    aligned=x@rotation.T+translation; error=np.linalg.norm(aligned-y,axis=1); raw=np.linalg.norm(x-y,axis=1)
    rows=[{'t':float(t),'error_m':float(e),'estimate_row':estimate[i]['row'],'reference_row':reference[j]['row'],'reference_t':reference[j]['t'],'dt_s':abs(estimate[i]['t']-reference[j]['t']),
           'estimate_xyz':aligned[k].tolist(),'reference_xyz':y[k].tolist(),'in_alignment_prefix':bool(fit[k])} for k,((i,j),t,e) in enumerate(zip(pairs,times,error))]
    return {'metrics':statistics(error),'raw_unaligned_residual':statistics(raw),'post_alignment_prefix_metrics':statistics(error[~fit]) if alignment=='se3_prefix' else statistics(error),
            'alignment':{'method':alignment,'scale':1,'rotation':rotation.tolist(),'translation_m':translation.tolist(),'fit_pairs':int(fit.sum()),'prefix_seconds':prefix_seconds,'determinant':float(np.linalg.det(rotation)),
                         'boundary':'Retrospective evaluation registration; not a transform known to the deployed robot'},
            'paired_count':len(pairs),'unpaired_estimate_count':len(estimate)-len(pairs),'tolerance_s':tolerance,'max_pair_dt_s':max(r['dt_s'] for r in rows),'rows':rows}


class ToolRecorder:
    def __init__(self): self.trace=[]
    def call(self,name,inputs,fn,source_ids):
        start=time.perf_counter();entry={'tool':name,'inputs':inputs,'source_ids':source_ids,'status':'running'}
        self.trace.append(entry)
        try:
            value=fn(); entry['status']='succeeded'; entry['output']= {k:v for k,v in value.items() if k!='rows'} if isinstance(value,dict) else value
            return value
        except ValueError as error:
            entry.update(status='failed',error=str(error));return None
        finally: entry['duration_ms']=round((time.perf_counter()-start)*1000,3)
