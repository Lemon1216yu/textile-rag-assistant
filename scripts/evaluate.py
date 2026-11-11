"""Tiny synthetic evaluation, explicitly Hit@4 rather than enterprise recall."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.core import Assistant

for key in ['LLM_API_KEY','EMBEDDING_API_KEY','REDIS_URL']:
    os.environ.pop(key,None)

cases=[
    ('C240 的成分和克重是多少','跟单','C240','spec-c240'),
    ('C180 的缩水率是多少','跟单','C180','spec-c180'),
    ('TC210 成分是什么','营销','TC210','spec-tc210'),
    ('客户来样打样需要登记什么','跟单','','merch-sop'),
    ('报价需要谁审批','营销','','sales-sop'),
    ('C240 色差要排查哪些条件','研发','C240','rd-process'),
    ('色牢度包含哪些指标','跟单','','quality-guide'),
]
scratch=Path(__file__).resolve().parents[1]/'tmp'
scratch.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=scratch) as directory:
    engine=Assistant(Path(directory)/'eval.db')
    results=[]
    for q,department,fabric,target in cases:
        sources,trace=engine.retrieve(q,department,fabric)
        found=list(dict.fromkeys(s['doc_id'] for s in sources))
        results.append({'question':q,'department':department,'target_doc':target,'retrieved':found,'hit':target in found})
output={'dataset':'synthetic-v1','metric':'target-document Hit@4 chunks','cases':len(cases),'hits':sum(r['hit'] for r in results),'hit_rate':sum(r['hit'] for r in results)/len(cases),'limitations':'Tiny synthetic smoke set. Not enterprise Recall@K, answer accuracy,. No model API called.','results':results}
path=Path(__file__).resolve().parents[1]/'docs'/'evaluation.json'
path.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(output,ensure_ascii=False,indent=2))
