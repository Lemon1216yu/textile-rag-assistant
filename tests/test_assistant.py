import json
import pytest
from app.core import Assistant


@pytest.fixture
def engine(tmp_path,monkeypatch):
    for key in ['REDIS_URL','LLM_API_KEY','EMBEDDING_API_KEY']:
        monkeypatch.delenv(key,raising=False)
    return Assistant(tmp_path/'test.db')


def test_specs_and_followup(engine):
    sid=engine.create_session('alice')
    r=engine.chat(sid,'alice','跟单','C240 的成分和克重是多少？')
    assert '240' in r['answer']
    assert any(s['doc_id']=='spec-c240' for s in r['sources'])
    r=engine.chat(sid,'alice','跟单','那它的缩水率呢？')
    assert r['rewritten'].startswith('C240')
    assert '-3.0%' in r['answer']
    assert not any(s['fabric']=='C180' for s in r['sources'])


def test_correction(engine):
    sid=engine.create_session('alice')
    engine.chat(sid,'alice','跟单','C240 成分')
    r=engine.chat(sid,'alice','跟单','改成 C180，它的缩水率？')
    assert r['memory']['facts']['面料编号']['value']=='C180'
    assert '-4.0%' in r['answer']
    assert all(s['fabric']!='C240' for s in r['sources'])


def test_department_permissions(engine):
    public,_=engine.retrieve('C240 工艺试样 色差 排查','营销','C240')
    rd,_=engine.retrieve('C240 工艺试样 色差 排查','研发','C240')
    assert all(s['doc_id']!='rd-process' for s in public)
    assert any(s['doc_id']=='rd-process' for s in rd)
    assert all(d['id']!='rd-process' for d in engine.documents('营销'))


def test_session_isolation(engine):
    sid=engine.create_session('alice')
    engine.chat(sid,'alice','跟单','C240 成分')
    with pytest.raises(KeyError):
        engine.chat(sid,'bob','研发','那它的缩水率呢？')
    other=engine.create_session('alice')
    assert not engine.session(other,'alice')['facts']


def test_memory_window(engine):
    sid=engine.create_session('alice')
    engine.chat(sid,'alice','跟单','C240 成分')
    for i in range(16):
        engine.chat(sid,'alice','跟单','你好')
    state=engine.session(sid,'alice')
    assert len(state['turns'])==15
    assert 'C240' in state['summary']
    assert state['facts']['面料编号']['value']=='C240'
    engine.clear(sid,'alice')
    assert engine.session(sid,'alice')=={'turns':[],'summary':'','facts':{},'total':0}


@pytest.mark.parametrize('q,route',[('这批面料能否放行？','人工确认'),('本月利润是多少','系统边界'),('你好','寒暄')])
def test_routes(engine,q,route):
    r=engine.chat(engine.create_session('alice'),'alice','跟单',q)
    assert r['route']==route
    if route=='人工确认':
        assert r['ticket'].startswith('T-')
        with engine.db() as db:
            assert db.execute('SELECT count(*) FROM tickets').fetchone()[0]==1


def test_unknown_and_nonexistent_fabric(engine):
    r=engine.chat(engine.create_session('alice'),'alice','跟单','火星轨道半径是多少？')
    assert not r['sources']
    r=engine.chat(engine.create_session('alice'),'alice','跟单','C999 成分和克重')
    assert all(s['fabric'] not in ['C180','C240','TC210'] for s in r['sources'])


def test_long_paragraph_keeps_suffix(engine):
    engine.ingest(title='长文档',department='研发',content='正文知识'*700+'末尾独有标记XYZ')
    text=''.join(c['text'] for c in engine.chunks('研发') if c['title']=='长文档')
    assert '末尾独有标记XYZ' in text


def test_pdf_empty_rejected(engine):
    with pytest.raises(ValueError):
        engine.ingest(title='扫描文件',department='研发',pages=[(1,'')])


def test_ttl(engine):
    sid=engine.create_session('alice')
    engine.chat(sid,'alice','跟单','C240 成分')
    with engine.db() as db:
        db.execute('UPDATE sessions SET updated=0 WHERE id=?',(sid,))
    assert not engine.session(sid,'alice')['facts']


def test_llm_failure_visible(engine,monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL','https://invalid.test/v1')
    monkeypatch.setenv('LLM_MODEL','qwen3.5-flash')
    monkeypatch.setenv('LLM_API_KEY','test-placeholder')
    def fail(*args,**kwargs):
        raise RuntimeError('provider failure')
    monkeypatch.setattr('app.core.httpx.post',fail)
    r=engine.chat(engine.create_session('alice'),'alice','跟单','C240 成分')
    assert r['mode']=='模型失败后摘录'
    assert '模型调用失败' in r['answer']


def test_upload_visibility_and_provenance(engine):
    doc=engine.ingest(title='研发记录',department='研发',pages=[(2,'新面料试样记录，标记为内部研发验证资料。')])
    assert doc['id'] not in [d['id'] for d in engine.documents('营销')]
    chunks=[c for c in engine.chunks('研发') if c['doc_id']==doc['id']]
    assert chunks[0]['page']==2
