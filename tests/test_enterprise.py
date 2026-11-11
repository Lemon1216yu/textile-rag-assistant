import hashlib
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from enterprise.config import Settings
from enterprise.db import AuthSession, Base, Chunk, Conversation, Database, Document, IngestJob, Ticket, Turn, User
from enterprise.ingestion import Ingestion, split_pages
from enterprise.main import create_app
from enterprise.providers import Models, ProviderError
from enterprise.security import PASSWORDS

PASSWORD='Test-only-passphrase-2026!'


@pytest.fixture
def env(tmp_path):
    cfg=Settings(mode='test',database_url='sqlite:///'+str(tmp_path/'enterprise.db'),upload_dir=str(tmp_path/'files'))
    db=Database(cfg.database_url); Base.metadata.create_all(db.engine)
    with db.session() as s:
        for email,department,role in [('admin@test.local','管理','admin'),('sales@test.local','营销','member'),('rd@test.local','研发','editor')]:
            s.add(User(email=email,name=email.split('@')[0],department=department,role=role,password=PASSWORDS.hash(PASSWORD)))
    app=create_app(cfg,db)
    with TestClient(app) as client:
        yield client,db,cfg,app.state.rag
    db.engine.dispose()


def login(client,email='admin@test.local'):
    response=client.post('/api/auth/login',json={'email':email,'password':PASSWORD})
    assert response.status_code==200,response.text
    client.headers['X-CSRF-Token']=response.json()['csrf']
    return response.json()['user']


def ingest(client,db,cfg,text='成分是纯棉，克重为 240 克。',department='共享',replaces='',version='1.0'):
    r=client.post('/api/documents',files={'file':('C240.md',('# C240 产品规格\n'+text).encode())},data={'department':department,'fabric':'C240','version':version,'replaces_id':replaces})
    assert r.status_code==200,r.text
    did=r.json()['id']; assert Ingestion(db,cfg).run_one()
    return did


def ask(client,sid,q='C240 的克重是多少？',request_id=None):
    from uuid import uuid4
    return client.post('/api/chat',json={'conversation_id':sid,'question':q,'request_id':request_id or uuid4().hex})


def test_production_fails_closed():
    with pytest.raises(ValueError,match='生产配置缺失'): Settings(mode='production').validate()


def test_revise_split_without_uploading_again(env):
    c,db,cfg,_=env; login(c)
    did=c.post('/api/documents',files={'file':('sample.txt',b'1234567890'*35)},data={'department':'共享'}).json()['id']
    path='/api/documents/'+did
    assert Ingestion(db,cfg).run_one()
    assert c.get(path).json()['chunks']==1
    login(c,'rd@test.local')
    opts={'chunk_size':100,'overlap':20}
    assert c.post(path+'/reindex',json=opts).status_code==404
    login(c)
    assert c.post(path+'/reindex',json={**opts,'overlap':75}).status_code==422
    assert c.post(path+'/reindex',json=opts).status_code==200
    assert c.post(path+'/reindex',json=opts).status_code==409
    assert c.get(path).json()['content']==[]
    assert Ingestion(db,cfg).run_one()
    doc=c.get(path).json()
    assert doc['status']=='review' and doc['chunks']==5 and doc['index_options']['overlap']==20
    assert c.post(path+'/publish').status_code==200
    assert c.post(path+'/reindex',json=opts).status_code==409


def test_upload_options_drive_worker_and_review_preview(env):
    c,db,cfg,_=env; login(c)
    body='ABCDEFGHIJ'*25
    def upload(overlap):
        return c.post('/api/documents',files={'file':('guide.txt',body.encode())},
                      data={'chunk_size':100,'overlap':overlap})
    assert upload(51).status_code==422
    did=upload(20).json()['id']
    assert Ingestion(db,cfg).run_one()
    preview=c.get('/api/documents/'+did).json()
    assert preview['status']=='review'
    assert preview['index_options']=={'chunk_size':100,'overlap':20,'dimension':None}
    assert [r['text'] for r in preview['content']]==[body[:100],body[80:180],body[160:]]
    assert c.get('/api/index-config').json()['model'] is None


def test_low_evidence_is_withheld_only_for_local_reranker(env,monkeypatch):
    client,db,cfg,rag=env
    login(client); did=ingest(client,db,cfg)
    client.post('/api/documents/'+did+'/publish')
    user=type('Member',(),{'role':'member','department':'跟单'})()
    monkeypatch.setattr(rag.models,'rerank',lambda q,rows:([{**r,'rerank_score':0.001} for r in rows],True))
    cfg.rerank_url='local://onnx'
    refs,trace=rag.retrieve('C240 克重',user,'C240')
    assert refs==[] and trace['counts']['low_evidence']>0
    assert rag.answer('克重？',refs,[],{})[2]=='insufficient'
    cfg.rerank_url='https://provider.test/rerank'
    refs,_=rag.retrieve('C240 克重',user,'C240')
    assert refs and 'source_url' not in refs[0]


def test_crashed_worker_exhaustion_is_visible(env):
    c,db,cfg,rag=env; login(c)
    did=ingest(c,db,cfg)
    with db.session() as s:
        job=s.scalar(select(IngestJob).where(IngestJob.doc_id==did))
        job.status='processing'; job.attempts=3; job.lease_until=time.time()-10
        s.get(Document,did).status='indexing'
    assert Ingestion(db,cfg).run_one() is False
    with db.session() as s:
        assert s.get(Document,did).status=='failed'


def test_migration_matches_models(tmp_path,monkeypatch):
    from alembic import command
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.autogenerate import compare_metadata
    from sqlalchemy import create_engine, inspect
    url='sqlite:///'+str(tmp_path/'migration.db')
    monkeypatch.setenv('DATABASE_URL',url)
    monkeypatch.setenv('APP_ENV','test')
    config=Config('alembic.ini')
    command.upgrade(config,'head')
    engine=create_engine(url)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection),Base.metadata)==[]
    command.downgrade(config,'base')
    assert inspect(engine).get_table_names()==['alembic_version']
    engine.dispose()


def test_password_csrf_origin_and_logout(env):
    c,db,cfg,rag=env
    assert c.get('/api/conversations').status_code==401
    assert c.post('/api/auth/login',json={'email':'admin@test.local','password':'wrong'}).status_code==401
    login(c)
    saved=c.headers.pop('X-CSRF-Token')
    assert c.post('/api/conversations').status_code==403
    c.headers['X-CSRF-Token']=saved
    assert c.post('/api/conversations',headers={'Origin':'https://evil.test'}).status_code==403
    assert c.post('/api/auth/logout').status_code==200
    assert c.get('/api/auth/me').status_code==401


def test_document_publish_and_department_acl(env):
    c,db,cfg,rag=env; login(c)
    did=ingest(c,db,cfg,department='研发')
    assert c.get('/api/documents/'+did).json()['status']=='review'
    sid=c.post('/api/conversations').json()['id']
    assert ask(c,sid).json()['sources']==[]
    assert c.post('/api/documents/'+did+'/publish').status_code==200
    assert ask(c,sid).json()['sources']
    login(c,'sales@test.local')
    assert c.get('/api/documents/'+did).status_code==404
    assert c.get('/api/documents').json()==[]
    assert c.get('/api/admin/users').status_code==403
    assert c.post('/api/documents/'+did+'/publish').status_code==403


def test_retirement_redacts_history_and_new_version(env):
    c,db,cfg,rag=env; login(c)
    first=ingest(c,db,cfg); c.post('/api/documents/'+first+'/publish')
    sid=c.post('/api/conversations').json()['id']; assert ask(c,sid).json()['sources']
    second=ingest(c,db,cfg,text='成分是纯棉，克重为 245 克。',replaces=first,version='2.0')
    c.post('/api/documents/'+second+'/publish')
    history=c.get('/api/conversations/'+sid).json()['turns']
    assert '不再展示' in history[0]['answer']
    result=ask(c,sid).json(); assert '245' in result['answer']
    assert all(x['doc_id']==second for x in result['sources'])


def test_idempotency_and_foreign_session(env):
    c,db,cfg,rag=env; login(c)
    sid=c.post('/api/conversations').json()['id']; key='x'*32
    a=ask(c,sid,'你好',key); b=ask(c,sid,'你好',key)
    assert a.json()['id']==b.json()['id']
    assert ask(c,sid,'C240',key).status_code==400
    login(c,'sales@test.local')
    assert c.get('/api/conversations/'+sid).status_code==404
    assert ask(c,sid).status_code==404


def test_window_summary_and_clear_boundary(env):
    c,db,cfg,rag=env; login(c)
    sid=c.post('/api/conversations').json()['id']
    assert ask(c,sid,'C240 成分').status_code==200
    for i in range(16): assert ask(c,sid,'你好').status_code==200
    state=c.get('/api/conversations/'+sid).json()
    assert state['memory']['window']==15
    assert 'C240' in state['memory']['summary']
    assert c.delete('/api/conversations/'+sid+'/memory').status_code==200
    result=ask(c,sid,'它的缩水率？').json()
    assert 'C240' not in result['trace']['rewritten']
    assert len(c.get('/api/conversations/'+sid).json()['turns'])==18


def test_ingestion_failure_retry_and_long_text(env):
    c,db,cfg,rag=env; login(c)
    r=c.post('/api/documents',files={'file':('short.txt',b'abc')})
    did=r.json()['id']; Ingestion(db,cfg).run_one()
    assert c.get('/api/documents/'+did).json()['status']=='failed'
    assert c.post('/api/documents/'+did+'/retry').status_code==200
    chunks=split_pages([(1,'开头'*1000+'结尾独有标记')],'长文档')
    assert any('结尾独有标记' in x['text'] for x in chunks)


def test_editor_cannot_publish_or_upload_shared(env):
    c,db,cfg,rag=env; login(c,'rd@test.local')
    assert c.post('/api/documents',files={'file':('x.md','研发内部记录'.encode())},data={'department':'共享'}).status_code==403
    did=ingest(c,db,cfg,department='研发')
    assert c.post('/api/documents/'+did+'/publish').status_code==403


def test_ticket_resolution_feedback_and_audit(env):
    c,db,cfg,rag=env; login(c,'sales@test.local')
    sid=c.post('/api/conversations').json()['id']; r=ask(c,sid,'可以放行吗？').json()
    tid=r['trace']['ticket_id']; assert tid
    assert c.post('/api/tickets/'+tid+'/resolve',json={'resolution':'请先完成批次复验'}).status_code==403
    assert c.post('/api/feedback',json={'turn_id':r['id'],'rating':1}).status_code==200
    assert c.post('/api/feedback',json={'turn_id':r['id'],'rating':-1}).status_code==200
    login(c); assert c.post('/api/tickets/'+tid+'/resolve',json={'resolution':'请先完成批次复验'}).status_code==200
    assert any(a['action']=='ticket.resolved' for a in c.get('/api/admin/audit').json())


def test_disable_revokes_sessions_and_password_rotation(env):
    c,db,cfg,rag=env; user=login(c,'sales@test.local')
    token=c.cookies.get('zhizhi_enterprise'); login(c)
    assert c.post('/api/admin/users/'+user['id']+'/disable').status_code==200
    c.cookies.set('zhizhi_enterprise',token)
    assert c.get('/api/auth/me').status_code==401
    c.cookies.clear(); login(c)
    assert c.post('/api/auth/password',json={'current_password':PASSWORD,'new_password':'Replacement-test-passphrase!'}).status_code==200
    assert c.get('/api/auth/me').status_code==401


def test_stream_and_security_headers(env):
    c,db,cfg,rag=env; login(c)
    sid=c.post('/api/conversations').json()['id']
    r=c.post('/api/chat/stream',json={'conversation_id':sid,'question':'你好','request_id':'s'*32})
    assert 'event: result' in r.text
    assert 'frame-ancestors' in r.headers['Content-Security-Policy']
    assert c.get('/health/ready').json()['status']=='ready'


def test_rerank_contract_and_embedding_dimension(monkeypatch):
    cfg=Settings(rerank_key='fake',rerank_url='https://provider.test/rerank',embedding_key='fake',embedding_dimension=2)
    models=Models(cfg); bodies=[]
    def response(url,key,body,timeout=40):
        bodies.append(body)
        if 'embeddings' in url: return {'data':[{'index':0,'embedding':[.1,.2]}]}
        return {'output':{'results':[{'index':1,'relevance_score':.9},{'index':0,'relevance_score':.2}]}}
    monkeypatch.setattr(models,'post',response)
    ranked,used=models.rerank('问题',[{'text':'a'},{'text':'b'}]); assert used and ranked[0]['text']=='b'
    assert bodies[0]['input']['query']=='问题'
    assert models.embed(['测试'])==[[.1,.2]]
    cfg.embedding_dimension=3
    with pytest.raises(ProviderError): models.embed(['测试'])


def test_llm_empty_and_bad_citation_rejected(env,monkeypatch):
    c,db,cfg,rag=env; cfg.llm_key='fake'
    monkeypatch.setattr(rag.models,'post',lambda *args,**kwargs:{'choices':[{'message':{'content':''}}]})
    with pytest.raises(ProviderError): rag.models.complete('system',{})
    monkeypatch.setattr(rag.models,'complete',lambda *args,**kwargs:('编造答案[S99]',{}))
    with pytest.raises(ProviderError): rag.answer('问题',[{'citation':'S1','title':'文档','version':'1','page':1,'text':'事实'}],[],{})


def test_scan_pdf_rejected_and_file_size(env):
    import io
    from pypdf import PdfWriter
    c,db,cfg,rag=env; login(c)
    writer=PdfWriter(); writer.add_blank_page(width=100,height=100); stream=io.BytesIO(); writer.write(stream)
    did=c.post('/api/documents',files={'file':('scan.pdf',stream.getvalue())}).json()['id']
    Ingestion(db,cfg).run_one()
    assert 'OCR' in c.get('/api/documents/'+did).json()['error']
    assert c.post('/api/documents',files={'file':('large.txt',b'x'*(10*1024*1024+1))}).status_code==413
