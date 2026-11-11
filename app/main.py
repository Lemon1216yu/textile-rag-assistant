import io
import os
import secrets
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader

from .core import Assistant, ROOT

app = FastAPI(title='织知 · 企业知识助手')
engine = Assistant()
lock = threading.Lock()
logins = {}
USERS = {'merch':{'name':'陈佳 · 跟单','department':'跟单'},'sales':{'name':'林晓 · 营销','department':'营销'},'rd':{'name':'周宁 · 研发','department':'研发'}}


def identity(request):
    uid = logins.get(request.cookies.get('zhizhi_session'))
    if not uid:
        raise HTTPException(401,'请先选择演示身份')
    return uid,USERS[uid]['department']


@app.get('/api/health')
def health():
    return {'status':'ok','mode':'local-demo','llm_configured':all(os.getenv(x) for x in ['LLM_BASE_URL','LLM_MODEL','LLM_API_KEY']),'model':os.getenv('LLM_MODEL') or '未配置','semantic_embeddings':all(os.getenv(x) for x in ['EMBEDDING_BASE_URL','EMBEDDING_MODEL','EMBEDDING_API_KEY']),'memory_backend':'Redis' if engine.redis else 'SQLite'}


@app.get('/api/users')
def users():
    return USERS


class Login(BaseModel):
    user: str


@app.post('/api/login')
def login(body:Login,request:Request,response:Response):
    if body.user not in USERS:
        raise HTTPException(400,'无效演示身份')
    logins.pop(request.cookies.get('zhizhi_session'),None)
    token = secrets.token_urlsafe(32)
    logins[token] = body.user
    response.set_cookie('zhizhi_session',token,httponly=True,samesite='strict',max_age=86400)
    return USERS[body.user]


@app.get('/api/sessions')
def sessions(request:Request):
    uid,_ = identity(request)
    return engine.sessions(uid)


@app.post('/api/sessions')
def create_session(request:Request):
    uid,_ = identity(request)
    return {'id':engine.create_session(uid)}


@app.get('/api/sessions/{sid}')
def session(sid:str,request:Request):
    uid,_ = identity(request)
    try:
        state=engine.session(sid,uid)
        return {**state,'memory':engine.memory(state)}
    except KeyError:
        raise HTTPException(404,'会话不存在')


@app.delete('/api/sessions/{sid}/memory')
def clear(sid:str,request:Request):
    uid,_ = identity(request)
    try:
        with lock:
            engine.clear(sid,uid)
        return {'ok':True}
    except KeyError:
        raise HTTPException(404,'会话不存在')


class Chat(BaseModel):
    session_id:str
    question:str=Field(min_length=1,max_length=2000)


@app.post('/api/chat')
def chat(body:Chat,request:Request):
    uid,dept=identity(request)
    try:
        with lock:
            return engine.chat(body.session_id,uid,dept,body.question)
    except KeyError:
        raise HTTPException(404,'会话不存在')
    except ValueError as e:
        raise HTTPException(400,str(e))


@app.get('/api/documents')
def documents(request:Request):
    _,dept=identity(request)
    return engine.documents(dept)


@app.get('/api/documents/{doc_id}')
def document(doc_id:str,request:Request):
    _,dept=identity(request)
    chunks=[c for c in engine.chunks(dept) if c['doc_id']==doc_id]
    if not chunks:
        raise HTTPException(404,'文档不存在或无权限')
    return chunks


@app.post('/api/documents')
async def upload(request:Request,file:UploadFile=File(...),fabric:str=Form('')):
    _,dept=identity(request)
    raw=await file.read(5*1024*1024+1)
    if len(raw)>5*1024*1024:
        raise HTTPException(413,'文件不能超过 5 MB')
    name=Path(file.filename or 'document').name
    try:
        if name.lower().endswith('.pdf'):
            pdf=PdfReader(io.BytesIO(raw))
            if len(pdf.pages)>80:
                raise ValueError('演示版单次最多导入 80 页')
            pages=[(i+1,p.extract_text() or '') for i,p in enumerate(pdf.pages)]
            if any(len(t.strip())<10 for _,t in pages):
                raise ValueError('包含无可提取文字的页面，请先 OCR 或移除空白页，避免静默漏页。')
        elif name.lower().endswith(('.md','.txt')):
            pages=[(1,raw.decode('utf-8-sig'))]
        else:
            raise ValueError('目前支持 UTF-8 TXT、Markdown 和文字型 PDF')
        return engine.ingest(title=name,department=dept,pages=pages,fabric=fabric[:30])
    except Exception as e:
        raise HTTPException(400,str(e) if isinstance(e,ValueError) else '文件解析失败，请检查编码或 PDF 是否加密')


class Feedback(BaseModel):
    session_id:str
    turn_id:str
    rating:int=Field(ge=-1,le=1)


@app.post('/api/feedback')
def feedback(body:Feedback,request:Request):
    import time,uuid
    uid,_=identity(request)
    try:
        state=engine.session(body.session_id,uid)
        if not any(t['id']==body.turn_id for t in state['turns']):
            raise KeyError()
    except KeyError:
        raise HTTPException(404,'回答不存在')
    with engine.db() as db:
        db.execute('INSERT INTO feedback VALUES(?,?,?,?,?,?)',(uuid.uuid4().hex,uid,body.session_id,body.turn_id,body.rating,time.time()))
    return {'ok':True}


@app.get('/api/tickets')
def tickets(request:Request):
    uid,_=identity(request)
    with engine.db() as db:
        return [dict(r) for r in db.execute('SELECT id,question,reason,created FROM tickets WHERE user=? ORDER BY created DESC',(uid,))]


app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')


@app.get('/')
def index():
    return FileResponse(ROOT/'static'/'index.html')
