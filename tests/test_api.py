from fastapi.testclient import TestClient
import pytest
from app.core import Assistant
from app import main


@pytest.fixture
def client(tmp_path,monkeypatch):
    for key in ['REDIS_URL','LLM_API_KEY','EMBEDDING_API_KEY']:
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setattr(main,'engine',Assistant(tmp_path/'api.db'))
    monkeypatch.setattr(main,'logins',{})
    with TestClient(main.app) as client:
        yield client


def test_login_and_source_permissions(client):
    assert client.get('/api/documents').status_code==401
    assert client.post('/api/login',json={'user':'invalid'}).status_code==400
    client.post('/api/login',json={'user':'sales'})
    assert client.get('/api/documents/rd-process').status_code==404
    client.post('/api/login',json={'user':'rd'})
    assert client.get('/api/documents/rd-process').status_code==200


def test_cross_user_session_and_feedback(client):
    client.post('/api/login',json={'user':'merch'})
    sid=client.post('/api/sessions').json()['id']
    result=client.post('/api/chat',json={'session_id':sid,'question':'C240 克重是多少？'}).json()
    assert '240' in result['answer']
    assert client.post('/api/feedback',json={'session_id':sid,'turn_id':result['id'],'rating':1}).status_code==200
    client.post('/api/login',json={'user':'rd'})
    assert client.get('/api/sessions/'+sid).status_code==404
    assert client.post('/api/chat',json={'session_id':sid,'question':'你好'}).status_code==404
    assert client.delete('/api/sessions/'+sid+'/memory').status_code==404


def test_upload_and_retrieve(client):
    client.post('/api/login',json={'user':'rd'})
    text='# DEMO988 演示记录\nDEMO988 的包装要求是使用蓝色纸箱，每箱放置两份模拟样卡。'
    response=client.post('/api/documents',files={'file':('demo.md',text.encode('utf-8'),'text/markdown')})
    assert response.status_code==200
    doc_id=response.json()['id']
    sid=client.post('/api/sessions').json()['id']
    answer=client.post('/api/chat',json={'session_id':sid,'question':'DEMO988 包装要求是什么'}).json()
    assert any(s['doc_id']==doc_id for s in answer['sources'])
    assert '蓝色纸箱' in answer['answer']
    client.post('/api/login',json={'user':'sales'})
    assert client.get('/api/documents/'+doc_id).status_code==404


def test_invalid_upload_and_question(client):
    client.post('/api/login',json={'user':'rd'})
    assert client.post('/api/documents',files={'file':('bad.exe',b'test')}).status_code==400
    assert client.post('/api/documents',files={'file':('empty.txt',b'')}).status_code==400
    sid=client.post('/api/sessions').json()['id']
    assert client.post('/api/chat',json={'session_id':sid,'question':' '}).status_code==400


def test_ticket_and_clear(client):
    client.post('/api/login',json={'user':'merch'})
    sid=client.post('/api/sessions').json()['id']
    r=client.post('/api/chat',json={'session_id':sid,'question':'C240 是否可以放行？'}).json()
    assert client.get('/api/tickets').json()[0]['id']==r['ticket']
    assert client.delete('/api/sessions/'+sid+'/memory').status_code==200
    assert client.get('/api/sessions/'+sid).json()['turns']==[]
