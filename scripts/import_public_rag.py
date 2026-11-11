"""Development-only: build the pinned public reference corpus and upgrade lexical seed vectors."""
import hashlib
import json
import shutil
import time
from pathlib import Path
from sqlalchemy import select
from enterprise.config import ROOT, Settings
from enterprise.db import Database, Document, IngestJob, Chunk, User, uid
from enterprise.ingestion import Ingestion
from enterprise.providers import Models, VectorStore


def main():
    cfg=Settings.from_env()
    if cfg.mode!='development' or not cfg.database_url.startswith('sqlite:') or cfg.embedding_base!='local://onnx':
        raise SystemExit('This fixture requires development SQLite and LOCAL_RAG_MODELS=1.')
    db=Database(cfg.database_url); model=Models(cfg); store=VectorStore(cfg)
    root=Path(cfg.upload_dir)
    manifest=json.loads((ROOT/'examples/public_sources.json').read_text('utf-8'))
    for source in manifest:
        path=ROOT/'data/public-pdfs'/source['filename']
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if digest!=source['sha256']: raise ValueError('PDF checksum mismatch')
        with db.session() as s:
            admin=s.scalar(select(User).where(User.role=='admin',User.active==True))
            doc=s.scalar(select(Document).where(Document.checksum==digest))
            if doc is None:
                did=uid(); dest=root/(did+'.pdf'); shutil.copyfile(path,dest)
                doc=Document(id=did,title=source['title'],department='共享',version=source['version'],path=str(dest.resolve()),checksum=digest,owner_id=admin.id)
                s.add(doc); s.flush(); s.add(IngestJob(doc_id=doc.id))
            did=doc.id
        worker=Ingestion(db,cfg)
        deadline=time.monotonic()+600
        while time.monotonic()<deadline:
            with db.session() as s:
                doc=s.get(Document,did)
                if doc.status=='failed': raise RuntimeError(doc.error)
                if doc.status in ('review','published'):
                    doc.status='published'; doc.approved_by=admin.id
                    print(source['filename'],doc.chunks,doc.embedding_model,flush=True); break
            if not worker.run_one(): time.sleep(1)
        else: raise TimeoutError(did)
    with db.session() as s:
        for doc in s.scalars(select(Document).where(Document.embedding_model=='lexical-development',Document.status=='published')):
            rows=list(s.scalars(select(Chunk).where(Chunk.doc_id==doc.id)))
            for start in range(0,len(rows),8):
                batch=rows[start:start+8]
                vectors=model.embed([doc.title+'\n'+c.section+'\n'+c.text for c in batch])
                for c,v in zip(batch,vectors): c.vector=v
                store.upsert(doc,[{'id':c.id,'vector':c.vector} for c in batch])
            doc.embedding_model=cfg.embedding_model


if __name__=='__main__': main()
