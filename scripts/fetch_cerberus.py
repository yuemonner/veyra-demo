"""Fetch a bounded official public trajectory subset for local, non-commercial evaluation."""
import base64, hashlib, json, pathlib, urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
DEST=ROOT/'work'/'cerberus'
BASE='https://libdrive.ethz.ch/public.php/webdav/CERBERUS_Darpa_Subt_Final_Event/anymal_1/trajectories/'
FILES={'online.csv':'1_compslam/compslam_tum.csv','reference.csv':'1_compslam/gt.csv','offline.csv':'2_compslam_post/compslam_post_tum.csv','offline_reference.csv':'2_compslam_post/gt.csv'}
def main():
 DEST.mkdir(parents=True,exist_ok=True); sources=[]
 for name,suffix in FILES.items():
  request=urllib.request.Request(BASE+suffix,headers={'Authorization':'Basic '+base64.b64encode(b'W626tMEBkROF8hG:').decode()})
  with urllib.request.urlopen(request,timeout=40) as response: raw=response.read(5_000_001)
  if len(raw)>5_000_000: raise ValueError('Source exceeds 5 MB cap')
  (DEST/name).write_bytes(raw)
  sources.append({'name':name,'url':BASE+suffix,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
  print(name,len(raw),raw[:180])
 manifest={'dataset':'CERBERUS DARPA SubT Final Event · ANYmal 1','doi':'10.3929/ethz-b-000552248','repository_commit':'37d0cca1303673fef34fcb32157d2037a42f5389','license':'In Copyright — Non-Commercial Use Permitted','rights_uri':'http://rightsstatements.org/page/InC-NC/1.0/','commercial_use':'not_cleared','reference_role':'retrospective_evaluation_only','files':sources}
 (DEST/'manifest.json').write_text(json.dumps(manifest,indent=2))
if __name__=='__main__': main()
