"""Local reproducible A/C/D evaluation; B and unlabelled quality metrics remain unavailable."""
import argparse, hashlib, json, time
from pathlib import Path
import numpy as np
from app.runtime_adapters import parse_tum
from app.runtime_tools import trajectory_error

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='work/runtime-evaluation.json');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];folder=root/'work'/'cerberus';manifest=json.loads((folder/'manifest.json').read_text())
    for file in manifest['files']:
        if hashlib.sha256((folder/file['name']).read_bytes()).hexdigest()!=file['sha256']:raise ValueError('Dataset hash changed')
    a,_=parse_tum((folder/'online.csv').read_text());b,_=parse_tum((folder/'reference.csv').read_text())
    start=time.perf_counter();result=trajectory_error(a,b);elapsed=(time.perf_counter()-start)*1000
    output={'dataset':manifest,'alignment':result['alignment'],'metrics':result['metrics'],'post_prefix_metrics':result['post_alignment_prefix_metrics'],
            'A':{'status':'executed_deterministic_baseline','elapsed_ms':elapsed,'position_rmse_m':result['metrics']['rmse']},
            'B':{'status':'unavailable','reason':'No authorized general LLM provider/tool runner; no external model calls'},
            'C':{'status':'covered_by_integration_test','command':'PYTHONPATH=backend python -m pytest backend/tests/test_runtime.py','same_numerical_tools':True},
            'D':{'status':'chronological_retrieval_test_only','quality_improvement':None,'reason':'No independent labeled multi-case evaluation set; synthetic workflow records are not performance evidence'},
            'detection_precision':None,'detection_recall':None,'causal_accuracy':None,'next_check_quality':None,'unsupported_claim_rate':None,
            'reference_boundary':'Retrospective reference used only by evaluator; not a deployment-time observation'}
    target=root/args.output;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(output,indent=2));print(json.dumps({'output':str(target),'rmse_m':output['metrics']['rmse'],'pairs':output['metrics']['count'],'model_calls':0}))
if __name__=='__main__':main()
