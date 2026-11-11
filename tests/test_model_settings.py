import json

from sqlalchemy import select

from test_enterprise import env, login, ingest, ask
from enterprise.db import Audit
from enterprise.ingestion import Ingestion
from enterprise.model_settings import ModelSettings
from enterprise.providers import Models


def save(c,group,values,**extra):
    revision=c.get('/api/admin/models').json()['revision']
    return c.put('/api/admin/models/'+group,json={'revision':revision,'values':values,**extra})


def test_secrets_encrypted_masked_and_reloaded(env):
    c,db,cfg,rag=env; login(c)
    secret='private-test-key-do-not-expose'
    r=save(c,'chat',{'llm_model':'my-model','llm_base':'https://models.example/v1'},key_action='replace',api_key=secret)
    assert r.status_code==200,r.text
    assert secret not in r.text and r.json()['groups']['chat']['key_configured']
    store=ModelSettings(cfg)
    assert secret.encode() not in store.path.read_bytes()
    assert store.effective().llm_key==secret
    assert store.effective().llm_model=='my-model'
    assert c.get('/api/auth/me').json()['model']=='my-model'
    assert save(c,'chat',{'llm_model':'my-new-model'}).status_code==200
    assert store.effective().llm_key==secret
    with db.session() as s:
        assert secret not in json.dumps([a.details for a in s.scalars(select(Audit))])
    assert save(c,'chat',{},key_action='clear').status_code==200
    assert not store.effective().llm_key


def test_admin_csrf_and_revision(env):
    c,db,cfg,rag=env; login(c)
    original=c.get('/api/admin/models').json()['revision']
    assert save(c,'chat',{'llm_model':'first'}).status_code==200
    assert c.put('/api/admin/models/chat',json={'revision':original,'values':{'llm_model':'second'}}).status_code==409
    assert ModelSettings(cfg).effective().llm_model=='first'
    login(c,'sales@test.local')
    assert c.get('/api/admin/models').status_code==403
    assert c.put('/api/admin/models/chat',json={}).status_code==403
    assert c.post('/api/admin/models/chat/test',json={}).status_code==403
    login(c)
    c.headers.pop('X-CSRF-Token')
    assert c.put('/api/admin/models/chat',json={}).status_code==403


def test_endpoint_change_never_reuses_old_secret(env):
    c,db,cfg,rag=env; login(c)
    assert save(c,'chat',{},key_action='replace',api_key='original-secret').status_code==200
    r=save(c,'chat',{'llm_base':'https://different.example/v1'})
    assert r.status_code==400 and '重新输入密钥' in r.text
    for url in ['http://169.254.169.254/latest','https://user:password@example.com/v1','https://api.example/v1?key=secret']:
        assert save(c,'chat',{'llm_base':url},key_action='clear').status_code==400
    assert save(c,'chat',{'llm_key':'not-allowed'}).status_code==400
    assert save(c,'chat',{'model_timeout':9999}).status_code==400
    assert ModelSettings(cfg).effective().llm_key=='original-secret'


def test_connection_probe_uses_draft_without_saving(env,monkeypatch):
    c,db,cfg,rag=env; login(c); calls=[]
    def complete(self,system,payload,*args,**kwargs):
        calls.append((self.s.llm_model,self.s.llm_key,payload)); return '{"ok":true}',{}
    monkeypatch.setattr(Models,'complete',complete)
    r=c.post('/api/admin/models/chat/test',json={'values':{'llm_model':'draft'},'key_action':'replace','api_key':'test-only-key'})
    assert r.status_code==200 and r.json()['ok']
    assert calls[0][0:2]==('draft','test-only-key')
    assert 'no enterprise data' in calls[0][2]['test']
    assert not ModelSettings(cfg).path.exists()
    monkeypatch.setattr(Models,'complete',lambda *a,**kw:('not-json',{}))
    assert not c.post('/api/admin/models/chat/test',json={'key_action':'replace','api_key':'test-only-key'}).json()['ok']


def test_new_chat_uses_saved_model_parameters(env,monkeypatch):
    c,db,cfg,rag=env; login(c)
    did=ingest(c,db,cfg); c.post('/api/documents/'+did+'/publish')
    assert save(c,'chat',{'llm_model':'custom-chat','llm_temperature':0.4,'llm_max_tokens':2300},key_action='replace',api_key='fake-key').status_code==200
    calls=[]
    def complete(self,system,payload,json_mode=False,max_tokens=None):
        calls.append((self.s.llm_model,self.s.llm_temperature,self.s.llm_max_tokens))
        if json_mode: return json.dumps({'route':'knowledge','intent':'面料参数','query':'C240 克重','reason':''}),{}
        return '模拟资料的克重为 240 克。[S1]',{}
    monkeypatch.setattr(Models,'complete',complete)
    sid=c.post('/api/conversations').json()['id']
    answer=ask(c,sid)
    assert answer.status_code==200 and answer.json()['trace']['model']=='custom-chat'
    assert calls and all(x==('custom-chat',0.4,2300) for x in calls)


def test_worker_refresh_and_embedding_index_guard(env,monkeypatch):
    c,db,cfg,rag=env; login(c)
    worker=Ingestion(db,cfg)
    assert save(c,'embedding',{'embedding_model':'embed-custom','embedding_dimension':2},key_action='replace',api_key='fake-embedding-key').status_code==200
    seen=[]
    def embed(self,texts):
        seen.append(self.s.embedding_model); return [[.2,.8] for _ in texts]
    monkeypatch.setattr(Models,'embed',embed)
    r=c.post('/api/documents',files={'file':('C340.md','# C340\n用于验证配置动态生效的模拟资料。'.encode())})
    assert r.status_code==200
    assert worker.run_one()
    assert seen==['embed-custom']
    assert save(c,'embedding',{'embedding_dimension':4}).status_code==409
    assert save(c,'embedding',{'embedding_model':'new-embedding'}).status_code==409
    assert save(c,'embedding',{},key_action='replace',api_key='rotated-key').status_code==200


def test_corrupt_configuration_does_not_fallback(env):
    c,db,cfg,rag=env; login(c)
    assert save(c,'chat',{'llm_model':'saved'}).status_code==200
    store=ModelSettings(cfg); store.path.write_bytes(b'invalid-encrypted-data')
    r=c.get('/api/admin/models')
    assert r.status_code==503
    assert cfg.llm_model not in r.text
