import argparse
import hashlib
import os
import secrets
import time
from pathlib import Path

from sqlalchemy import select

from .config import ROOT, Settings
from .db import Base, Database, Document, IngestJob, User, uid
from .ingestion import Ingestion
from .security import PASSWORDS


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['init-dev','bootstrap-admin','worker','work-once','rebuild-vectors','rebuild-bm25'])
    args=parser.parse_args()
    settings=Settings.from_env(); db=Database(settings.database_url)
    if args.command=='rebuild-bm25':
        from .lexical import LexicalIndex
        from .db import Chunk
        with db.session() as s:
            for doc in s.scalars(select(Document).where(Document.status.in_(['published','review']))):
                rows=s.scalars(select(Chunk).where(Chunk.doc_id==doc.id))
                LexicalIndex(settings.upload_dir).replace(doc,[{'id':c.id,'section':c.section,'text':c.text} for c in rows])
        return
    if args.command=='rebuild-vectors':
        from .db import Chunk
        from .providers import VectorStore
        if not settings.milvus_uri: raise SystemExit('MILVUS_URI is required')
        store=VectorStore(settings); store.ensure(); count=0
        with db.session() as s:
            for doc in s.scalars(select(Document).where(Document.status.in_(['published','review']))):
                rows=list(s.scalars(select(Chunk).where(Chunk.doc_id==doc.id)))
                if any(len(c.vector)!=settings.embedding_dimension for c in rows):
                    raise SystemExit('向量维度不匹配，需使用原模型配置或重新索引文档：'+doc.id)
                for offset in range(0,len(rows),100):
                    store.upsert(doc,[{'id':c.id,'vector':c.vector} for c in rows[offset:offset+100]])
                count+=len(rows)
        print(f'Rebuilt {count} vectors from the SQL source of truth')
        return
    if args.command in {'worker','work-once'}:
        worker=Ingestion(db,settings)
        while True:
            worked=worker.run_one()
            if args.command=='work-once': break
            if not worked: time.sleep(2)
        return
    if args.command=='init-dev':
        if settings.production: raise SystemExit('生产环境禁止初始化开发数据，请执行数据库迁移')
        Base.metadata.create_all(db.engine)
    email=os.getenv('BOOTSTRAP_EMAIL','admin@zhizhi.local')
    password=os.getenv('BOOTSTRAP_PASSWORD')
    if settings.production and (not password or len(password)<16): raise SystemExit('请通过安全环境变量设置至少 16 位 BOOTSTRAP_PASSWORD')
    password=password or secrets.token_urlsafe(18)
    created=False
    with db.session() as s:
        user=s.scalar(select(User).where(User.email==email))
        if not user:
            user=User(email=email,name='工作空间管理员',department='管理',role='admin',password=PASSWORDS.hash(password))
            s.add(user); s.flush(); created=True
    if created and not settings.production:
        out=ROOT/'tmp'/'enterprise-access.txt'; out.parent.mkdir(exist_ok=True)
        out.write_text(f'本地开发账号（不要提交或公开）\n地址：http://127.0.0.1:8766\n账号：{email}\n密码：{password}\n',encoding='utf-8')
        print('本地账号已创建，登录信息保存到 tmp/enterprise-access.txt')
    if args.command=='init-dev':
        from app.seed import DOCUMENTS
        root=Path(settings.upload_dir); root.mkdir(parents=True,exist_ok=True)
        created_seeds=[]
        for seed in DOCUMENTS:
            with db.session() as s:
                exists=s.scalar(select(Document).where(Document.title==seed['title']))
                if exists: continue
                did=uid(); created_seeds.append(did); path=root/(did+'.md'); raw=seed['content'].encode('utf-8'); path.write_bytes(raw)
                s.add(Document(id=did,title=seed['title'],department=seed['department'],fabric=seed['fabric'],version=seed['version'],path=str(path.resolve()),checksum=hashlib.sha256(raw).hexdigest(),owner_id=user.id))
                s.flush(); s.add(IngestJob(doc_id=did))
        worker=Ingestion(db,settings)
        while worker.run_one(): pass
        with db.session() as s:
            for d in s.scalars(select(Document).where(Document.id.in_(created_seeds), Document.status=='review')):
                d.status='published'; d.approved_by=user.id
        db.audit(user.id,'development.seeded',details={'synthetic':True})
        print('开发数据库与模拟资料已准备。生产模式不会创建模拟数据。')


if __name__=='__main__': main()
