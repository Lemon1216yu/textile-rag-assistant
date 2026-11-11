"""Read-only, offline retrieval comparison on the local development corpus."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

from enterprise.config import ROOT, Settings
from enterprise.db import Database
from enterprise.rag import RAG


def evaluate(cfg, cases):
    if cfg.mode!='development' or not cfg.database_url.startswith('sqlite:') or cfg.milvus_uri or cfg.embedding_base!='local://onnx' or cfg.rerank_url!='local://onnx':
        raise SystemExit('Requires local development SQLite + LOCAL_RAG_MODELS=1; no remote inference.')
    db=Database(cfg.database_url)
    results={}
    try:
        for label,settings in [('bm25',replace(cfg,embedding_base='',embedding_key='',rerank_url='',rerank_key='')),('hybrid',cfg)]:
            pipeline=RAG(db,settings,None); rows=[]
            for c in cases:
                user=SimpleNamespace(role='member',department=c['department'])
                if label=='bm25':
                    from sqlalchemy import select
                    from enterprise.db import Chunk,Document
                    from enterprise.lexical import LexicalIndex
                    with db.session() as session:
                        allowed=select(Document.id).where(Document.status=='published',Document.department.in_(['共享',user.department]))
                        if c['fabric']: allowed=allowed.where(Document.fabric.in_(['',c['fabric']]))
                        hits=LexicalIndex(cfg.upload_dir).search(c['query'],set(session.scalars(allowed)))[:6]
                        refs=[]
                        for ident,_ in hits:
                            chunk=session.get(Chunk,ident)
                            if chunk: refs.append(dict(text=chunk.text,page=chunk.page,title=session.get(Document,chunk.doc_id).title))
                else:
                    refs,_=pipeline.retrieve(c['query'],user,c['fabric'])
                gold=c['evidence']
                ranks=[n for n,r in enumerate(refs,1) if gold is not None and c['title'] in r['title'] and (gold==r['page'] if isinstance(gold,int) else gold in r['text'])]
                rows.append(dict(id=c['id'],answerable=gold is not None,rank=min(ranks,default=None),returned=len(refs)))
            pos=[r for r in rows if r['answerable']]; neg=[r for r in rows if not r['answerable']]
            results[label]={'answerable':len(pos),'hit_at_6':round(sum(bool(r['rank']) for r in pos)/len(pos),3),'mrr_at_6':round(sum(1/r['rank'] for r in pos if r['rank'])/len(pos),3),'no_answer_correct':sum(not r['returned'] for r in neg),'no_answer_total':len(neg),'cases':rows}
    finally: db.engine.dispose()
    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT/'tmp/retrieval-evaluation.json')
    args=parser.parse_args()
    result=evaluate(Settings.from_env(),json.loads((ROOT/'examples/retrieval_cases.json').read_text('utf-8')))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n','utf-8')
    for label,metrics in result.items(): print(label,{k:v for k,v in metrics.items() if k!='cases'})
