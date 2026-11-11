"""Exercise this project's local Docker stack. No external model calls.

Creates synthetic test rows in the zhizhi database and an isolated test collection.
Requires deploy/compose.local-infra.yml. Never point this at production.
"""
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from enterprise.config import Settings
    from enterprise.db import Database, User, Conversation, uid
    from enterprise.ingestion import Ingestion
    from enterprise.main import create_app
    from enterprise.providers import Models, VectorStore
    from enterprise.security import PASSWORDS, Runtime
    values=dict(line.split('=',1) for line in (ROOT/'deploy/secrets/compose.env').read_text().splitlines() if '=' in line)
    cfg=Settings(mode='test',database_url=f"mysql+pymysql://zhizhi:{values['MYSQL_PASSWORD']}@127.0.0.1:13366/zhizhi?charset=utf8mb4",
        redis_url=f"redis://:{values['REDIS_PASSWORD']}@127.0.0.1:16386/0",milvus_uri='http://127.0.0.1:19536',
        milvus_token='root:'+values['MILVUS_PASSWORD'],collection='zhizhi_test_'+uid(),embedding_dimension=8,
        embedding_key='synthetic-test-only',upload_dir=str(ROOT/'tmp/infra-files'))
    os.environ['APP_ENV']='test'; os.environ['DATABASE_URL']=cfg.database_url
    command.upgrade(Config(str(ROOT/'alembic.ini')),'head')
    db=Database(cfg.database_url); rt=Runtime(cfg); store=VectorStore(cfg)
    suffix=uid(); password=secrets.token_urlsafe(24)
    with db.session() as s:
        admin=User(email=suffix+'@test.local',name='基础设施验收',department='管理',role='admin',password=PASSWORDS.hash(password))
        member=User(email='member-'+suffix+'@test.local',name='隔离验收',department='营销',role='member',password=PASSWORDS.hash(password))
        s.add_all([admin,member]); s.flush()
    checks=[]
    def embed(self,texts):
        return [[(v+1)/256 for v in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]
    try:
        with patch.object(Models,'embed',embed),TestClient(create_app(cfg,db,rt)) as c:
            def login(email):
                r=c.post('/api/auth/login',json={'email':email,'password':password}); assert r.status_code==200
                c.headers['X-CSRF-Token']=r.json()['csrf']
            login(admin.email)
            r=c.post('/api/documents',files={'file':('C880.md','# C880 模拟规格\n成分纯棉，克重 280 克，用于验收。'.encode())},data={'department':'研发','fabric':'C880'})
            assert r.status_code==200,r.text
            did=r.json()['id']; assert Ingestion(db,cfg).run_one()
            assert c.get('/api/documents/'+did).json()['status']=='review'
            assert c.post('/api/documents/'+did+'/publish').status_code==200
            sid=c.post('/api/conversations').json()['id']
            result=c.post('/api/chat',json={'conversation_id':sid,'question':'C880 的克重是多少？','request_id':uid()})
            assert result.status_code==200,result.text
            assert any(x['doc_id']==did for x in result.json()['sources'])
            checks.append('MySQL migration + authenticated upload + durable worker + publication + RAG')
            with db.session() as s:
                stamp=s.get(Conversation,sid).updated
                assert abs(stamp-time.time())<10, 'Timestamp precision loss'
            checks.append('MySQL timestamp precision')
            assert rt.redis.ttl(f'zhizhi:facts:{admin.id}:{sid}')>0
            second=Runtime(cfg)
            with rt.conversation_lock(sid):
                try:
                    with second.conversation_lock(sid): raise AssertionError('Concurrent lock accepted')
                except RuntimeError: pass
            checks.append('Redis fact persistence, expiry and distributed lock')
            vector=embed(None,['C880'])[0]
            assert store.search(vector,'研发','C880',allowed_doc_ids={did})
            assert not store.search(vector,'营销','C880',allowed_doc_ids={did})
            assert not store.search(vector,'研发','C880',allowed_doc_ids=set())
            checks.append('Milvus real vector writes, search and department/publication filters')
            login(member.email)
            assert c.get('/api/documents/'+did).status_code==404
            checks.append('Cross-department API isolation on MySQL')
            login(admin.email)
            assert c.post('/api/documents/'+did+'/retire').status_code==200
            assert not c.get('/api/conversations/'+sid).json()['turns'][0]['sources']
            checks.append('Retired document history redaction')
    finally:
        if store.client.has_collection(cfg.collection): store.client.drop_collection(cfg.collection)
        with db.session() as s:
            s.get(User,admin.id).active=False; s.get(User,member.id).active=False
        db.engine.dispose()
    report={'checks':checks,'passed':len(checks),'external_models':'NOT TESTED; deterministic synthetic vectors only','timestamp':time.time()}
    (ROOT/'docs/infrastructure-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
