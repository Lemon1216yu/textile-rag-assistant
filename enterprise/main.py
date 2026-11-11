import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text, update

from .config import ROOT, Settings
from .db import Audit, AuthSession, Chunk, Conversation, Database, Document, IndexOptions, Feedback, IngestJob, Ticket, Turn, User, uid
from .providers import ProviderError, VectorStore
from .rag import RAG, can_read
from .security import PASSWORDS, Runtime, hash_token, verify_password
from .model_settings import ModelSettings, GROUPS, KEYS
from .providers import Models

log=logging.getLogger('zhizhi.api')
DEPARTMENTS={'跟单','营销','研发','管理'}


class LoginBody(BaseModel):
    email:str=Field(min_length=3,max_length=190)
    password:str=Field(min_length=1,max_length=128)


class UserBody(BaseModel):
    email:str=Field(min_length=3,max_length=190)
    name:str=Field(min_length=1,max_length=80)
    department:str
    role:str='member'
    password:str=Field(min_length=12,max_length=128)


class PasswordBody(BaseModel):
    current_password:str=Field(max_length=128)
    new_password:str=Field(min_length=12,max_length=128)


class ChatBody(BaseModel):
    conversation_id:str=Field(pattern=r'^[a-f0-9]{32}$')
    question:str=Field(min_length=1,max_length=2000)
    request_id:str=Field(min_length=16,max_length=64,pattern=r'^[a-zA-Z0-9-]+$')


class RenameBody(BaseModel):
    title:str=Field(min_length=1,max_length=160)


class FeedbackBody(BaseModel):
    turn_id:str
    rating:int=Field(ge=-1,le=1)
    note:str=Field(default='',max_length=1000)


class ResolutionBody(BaseModel):
    resolution:str=Field(min_length=5,max_length=2000)


class SplitOptionsBody(BaseModel):
    chunk_size:int=Field(strict=True,ge=100,le=480)
    overlap:int=Field(strict=True,ge=0)


class ModelConfigBody(BaseModel):
    values:dict=Field(default_factory=dict)
    revision:str=Field(default='environment',max_length=64)
    key_action:str='keep'
    api_key:str=Field(default='',max_length=4096)


def user_dict(user):
    return {'id':user.id,'name':user.name,'email':user.email,'department':user.department,'role':user.role,'active':user.active}


def doc_dict(doc):
    return {k:getattr(doc,k) for k in ['id','title','department','fabric','version','status','chunks','error','created','updated','embedding_model','replaces_id','approved_by']}


def create_app(settings=None,database=None,runtime=None):
    cfg=settings or Settings.from_env()
    db=database or Database(cfg.database_url)
    rt=runtime or Runtime(cfg)
    rag=RAG(db,cfg,rt)
    model_settings=ModelSettings(cfg)
    app=FastAPI(title='织知企业知识工作空间',version='1.0.0',docs_url=None if cfg.production else '/docs',redoc_url=None)
    app.state.db=db; app.state.settings=cfg; app.state.rag=rag; app.state.runtime=rt

    @app.middleware('http')
    async def boundaries(request,call_next):
        request.state.request_id=uid()
        if request.method not in {'GET','HEAD','OPTIONS'}:
            origin=request.headers.get('origin')
            if origin and origin.rstrip('/')!=cfg.public_origin.rstrip('/'):
                return JSONResponse({'detail':'请求来源不被允许'},status_code=403)
        try:
            response=await call_next(request)
        except Exception as e:
            log.error('request_failed id=%s type=%s',request.state.request_id,type(e).__name__)
            response=JSONResponse({'detail':'服务暂时不可用，请稍后重试','request_id':request.state.request_id},status_code=503)
        response.headers.update({'X-Request-ID':request.state.request_id,'X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin',
            'X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        if request.url.path.startswith('/api'): response.headers['Cache-Control']='no-store'
        if cfg.production: response.headers['Strict-Transport-Security']='max-age=31536000'
        return response

    def current(request:Request):
        token=request.cookies.get('zhizhi_enterprise','')
        with db.session() as s:
            auth=s.get(AuthSession,hash_token(token)) if token else None
            user=s.get(User,auth.user_id) if auth and auth.expires>time.time() else None
            if not user or not user.active: raise HTTPException(401,'请登录工作空间')
            if request.method not in {'GET','HEAD','OPTIONS'} and not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),auth.csrf):
                raise HTTPException(403,'会话校验失败，请刷新页面')
            request.state.auth=auth
            return user

    def editor(user=Depends(current)):
        if user.role not in {'editor','admin'}: raise HTTPException(403,'需要知识维护权限')
        return user

    def admin(user=Depends(current)):
        if user.role!='admin': raise HTTPException(403,'需要管理员权限')
        return user

    def conversation(sid,user):
        with db.session() as s:
            c=s.get(Conversation,sid)
            if not c or c.user_id!=user.id or c.archived: raise HTTPException(404,'对话不存在')
            return c

    @app.get('/health/live')
    def live(): return {'status':'ok'}

    @app.get('/health/ready')
    def ready():
        try:
            with db.session() as s: s.execute(text('SELECT 1 FROM users LIMIT 1'))
            if cfg.redis_url: rt.redis.ping()
            if cfg.milvus_uri: VectorStore(cfg).client.list_collections()
            return {'status':'ready'}
        except Exception: return JSONResponse({'status':'not_ready'},status_code=503)

    @app.post('/api/auth/login')
    def login(body:LoginBody,request:Request,response:Response):
        email=body.email.strip().lower()
        addr=request.client.host if request.client else 'unknown'
        # In production Nginx limits the real client IP; the app sees the proxy IP.
        if (not cfg.production and not rt.rate_limit('login:ip:'+addr,20,300)) or not rt.rate_limit('login:account:'+hash_token(email),10,300):
            raise HTTPException(429,'尝试次数过多，请五分钟后再试')
        with db.session() as s:
            user=s.scalar(select(User).where(User.email==email))
            valid=verify_password(user.password if user else '',body.password)
            if not valid or not user or not user.active:
                s.add(Audit(actor='anonymous',action='auth.failed',target=hash_token(email)))
                # Separate transaction so failure audit is committed before HTTPException.
                s.flush(); s.commit()
                raise HTTPException(401,'账号或密码不正确')
            token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
            old=request.cookies.get('zhizhi_enterprise')
            if old: s.execute(delete(AuthSession).where(AuthSession.token_hash==hash_token(old)))
            s.add(AuthSession(token_hash=hash_token(token),user_id=user.id,csrf=csrf,expires=time.time()+cfg.session_hours*3600))
            s.add(Audit(actor=user.id,action='auth.login'))
        response.set_cookie('zhizhi_enterprise',token,httponly=True,secure=cfg.secure_cookie,samesite='strict',max_age=cfg.session_hours*3600,path='/')
        return {'user':user_dict(user),'csrf':csrf,'organization':cfg.organization,'mode':cfg.mode,'model_configured':bool(model_settings.effective().llm_key),'model':model_settings.effective().llm_model}

    @app.get('/api/auth/me')
    def me(request:Request,user=Depends(current)):
        return {'user':user_dict(user),'csrf':request.state.auth.csrf,'organization':cfg.organization,'mode':cfg.mode,'model_configured':bool(model_settings.effective().llm_key),'model':model_settings.effective().llm_model}

    @app.post('/api/auth/logout')
    def logout(request:Request,response:Response,user=Depends(current)):
        with db.session() as s: s.execute(delete(AuthSession).where(AuthSession.token_hash==request.state.auth.token_hash))
        response.delete_cookie('zhizhi_enterprise',path='/')
        db.audit(user.id,'auth.logout')
        return {'ok':True}

    @app.post('/api/auth/password')
    def password(body:PasswordBody,response:Response,user=Depends(current)):
        if not verify_password(user.password,body.current_password): raise HTTPException(400,'当前密码不正确')
        with db.session() as s:
            s.get(User,user.id).password=PASSWORDS.hash(body.new_password)
            s.execute(delete(AuthSession).where(AuthSession.user_id==user.id))
            s.add(Audit(actor=user.id,action='auth.password_changed'))
        response.delete_cookie('zhizhi_enterprise',path='/')
        return {'ok':True}

    @app.get('/api/conversations')
    def list_conversations(user=Depends(current)):
        with db.session() as s:
            return [{'id':c.id,'title':c.title,'updated':c.updated} for c in s.scalars(select(Conversation).where(Conversation.user_id==user.id,Conversation.archived==False).order_by(Conversation.updated.desc()).limit(200))]

    @app.post('/api/conversations')
    def new_conversation(user=Depends(current)):
        with db.session() as s:
            c=Conversation(user_id=user.id); s.add(c); s.flush()
            return {'id':c.id,'title':c.title}

    @app.get('/api/conversations/{sid}')
    def get_conversation(sid:str,user=Depends(current)):
        c=conversation(sid,user)
        with db.session() as s:
            turns=list(s.scalars(select(Turn).where(Turn.conversation_id==sid).order_by(Turn.created.desc()).limit(100)))[::-1]
        memory=c.memory or {}
        if time.time()-memory.get('updated',0)>cfg.memory_hours*3600: memory={}
        return {'id':sid,'title':c.title,'turns':[rag.visible_turn(t,user) for t in turns],'memory':memory}

    @app.patch('/api/conversations/{sid}')
    def rename(sid:str,body:RenameBody,user=Depends(current)):
        conversation(sid,user)
        with db.session() as s: s.get(Conversation,sid).title=body.title.strip()
        return {'ok':True}

    @app.post('/api/conversations/{sid}/archive')
    def archive(sid:str,user=Depends(current)):
        conversation(sid,user)
        with rt.conversation_lock(sid):
            with db.session() as s: s.get(Conversation,sid).archived=True
        db.audit(user.id,'conversation.archived',sid)
        return {'ok':True}

    @app.delete('/api/conversations/{sid}/memory')
    def clear_memory(sid:str,user=Depends(current)):
        conversation(sid,user)
        with rt.conversation_lock(sid):
            with db.session() as s: s.get(Conversation,sid).memory={'cleared_at':time.time(),'updated':time.time()}
            if rt.redis: rt.redis.delete(f'zhizhi:facts:{user.id}:{sid}')
        db.audit(user.id,'memory.cleared',sid)
        return {'ok':True}

    def do_chat(body,user):
        if not body.question.strip(): raise ValueError('问题不能为空')
        if not rt.rate_limit('chat:'+user.id,30,60): raise HTTPException(429,'请求过于频繁，请稍后再试')
        active=RAG(db,model_settings.effective(),rt) if model_settings.path.exists() else rag
        return active.chat(user,body.conversation_id,body.question.strip(),body.request_id)

    @app.post('/api/chat')
    def chat(body:ChatBody,user=Depends(current)):
        try: return do_chat(body,user)
        except KeyError: raise HTTPException(404,'会话不存在')
        except ValueError as e: raise HTTPException(400,str(e))
        except (ProviderError,RuntimeError) as e: raise HTTPException(503,str(e))

    @app.post('/api/chat/stream')
    async def chat_stream(body:ChatBody,user=Depends(current)):
        conversation(body.conversation_id,user)
        async def events():
            yield 'event: status\ndata: '+json.dumps({'message':'正在检索可访问资料并整理回答'},ensure_ascii=False)+'\n\n'
            task=asyncio.create_task(asyncio.to_thread(do_chat,body,user))
            try:
                while not task.done():
                    done,_=await asyncio.wait([task],timeout=12)
                    if not done: yield ': keepalive\n\n'
                result=task.result()
                yield 'event: result\ndata: '+json.dumps(result,ensure_ascii=False)+'\n\n'
            except Exception as e:
                detail=str(e) if isinstance(e,(ValueError,ProviderError,RuntimeError)) else getattr(e,'detail','回答处理失败，请重试')
                yield 'event: error\ndata: '+json.dumps({'detail':detail},ensure_ascii=False)+'\n\n'
        return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

    @app.get('/api/documents')
    def documents(user=Depends(current)):
        with db.session() as s:
            query=select(Document)
            if user.role!='admin': query=query.where(Document.department.in_(['共享',user.department]))
            if user.role=='member': query=query.where(Document.status=='published')
            query=query.order_by(Document.created.desc()).limit(500)
            docs=list(s.scalars(query))
            return [doc_dict(d) for d in docs if can_read(d,user,published=user.role=='member')]

    @app.get('/api/index-config')
    def index_config(user=Depends(editor)):
        active=model_settings.effective()
        enabled=bool(active.embedding_key or active.embedding_base=='local://onnx')
        return {'model':active.embedding_model if enabled else None,'dimension':active.embedding_dimension if enabled else None,'store':'Milvus' if active.milvus_uri else 'SQL'}

    @app.get('/api/documents/{did}')
    def document(did:str,user=Depends(current)):
        with db.session() as s:
            d=s.get(Document,did)
            if not d or not can_read(d,user,published=user.role=='member'): raise HTTPException(404,'资料不存在或无权限')
            chunks=[{'id':c.id,'page':c.page,'section':c.section,'text':c.text} for c in s.scalars(select(Chunk).where(Chunk.doc_id==did).order_by(Chunk.page,Chunk.id))]
            opts=s.get(IndexOptions,did)
            return {**doc_dict(d),'index_options':{'chunk_size':opts.chunk_size if opts else None,'overlap':opts.overlap if opts else None,'dimension':opts.dimension if opts else None},'content':chunks,'logical_pages':Path(d.path).suffix.lower()!='.pdf'}

    @app.post('/api/documents')
    async def upload(file:UploadFile=File(...),title:str=Form(''),department:str=Form(''),fabric:str=Form(''),version:str=Form('1.0'),replaces_id:str=Form(''),chunk_size:int=Form(400),overlap:int=Form(56),user=Depends(editor)):
        from .ingestion import validate_split
        try: validate_split(chunk_size,overlap)
        except ValueError as e: raise HTTPException(422,str(e)) from e
        department=department or user.department
        if department not in DEPARTMENTS|{'共享'} or (user.role!='admin' and department!=user.department): raise HTTPException(403,'无权向此知识范围上传资料')
        suffix=Path(file.filename or '').suffix.lower()
        if suffix not in {'.txt','.md','.pdf','.docx'}: raise HTTPException(400,'支持 TXT、Markdown、文字 PDF 和 DOCX')
        title=(title.strip() or Path(file.filename or '文档').name)[:240]
        if not re.fullmatch(r'[\w.\-]{1,40}',version) or len(fabric)>40: raise HTTPException(400,'版本或面料编号格式不正确')
        root=Path(cfg.upload_dir); root.mkdir(parents=True,exist_ok=True)
        did=uid(); path=root/(did+suffix); total=0; digest=hashlib.sha256()
        try:
            with path.open('xb') as stream:
                while raw:=await file.read(65536):
                    total+=len(raw)
                    if total>10*1024*1024: raise HTTPException(413,'文件不能超过 10 MB')
                    stream.write(raw); digest.update(raw)
            if total==0: raise HTTPException(400,'文件为空')
            with db.session() as s:
                if replaces_id:
                    previous=s.get(Document,replaces_id)
                    if not previous or previous.status!='published' or not can_read(previous,user) or previous.department!=department:
                        raise HTTPException(400,'被替换资料必须是同一知识范围的已发布版本')
                d=Document(id=did,title=title,department=department,fabric=fabric.upper(),version=version,path=str(path.resolve()),checksum=digest.hexdigest(),owner_id=user.id,replaces_id=replaces_id or None)
                s.add(d); s.flush(); s.add(IndexOptions(doc_id=did,chunk_size=chunk_size,overlap=overlap)); s.add(IngestJob(doc_id=did)); s.add(Audit(actor=user.id,action='document.uploaded',target=did,details={'bytes':total,'department':department}))
            return doc_dict(d)
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @app.post('/api/documents/{did}/publish')
    def publish(did:str,user=Depends(admin)):
        with db.session() as s:
            d=s.scalar(select(Document).where(Document.id==did).with_for_update())
            if not d or d.status!='review' or d.chunks<1: raise HTTPException(409,'只有已完成索引的待审核资料可以发布')
            if cfg.production and d.embedding_model!=model_settings.effective().embedding_model: raise HTTPException(409,'资料索引模型不一致，请重新索引')
            if d.replaces_id:
                old=s.scalar(select(Document).where(Document.id==d.replaces_id).with_for_update())
                if not old or old.status!='published': raise HTTPException(409,'旧版本已发生变化，请重新提交版本')
                old.status='retired'; old.updated=time.time()
            d.status='published'; d.approved_by=user.id; d.updated=time.time()
            s.add(Audit(actor=user.id,action='document.published',target=did,details={'replaces':d.replaces_id}))
        return {'ok':True}

    @app.post('/api/documents/{did}/retire')
    def retire(did:str,user=Depends(admin)):
        with db.session() as s:
            d=s.scalar(select(Document).where(Document.id==did).with_for_update())
            if not d or d.status!='published': raise HTTPException(409,'资料当前不是已发布状态')
            d.status='retired'; d.updated=time.time()
            s.add(Audit(actor=user.id,action='document.retired',target=did))
        return {'ok':True}

    @app.post('/api/documents/{did}/reindex')
    def reindex(did:str,body:SplitOptionsBody,user=Depends(editor)):
        from .ingestion import validate_split
        try: validate_split(body.chunk_size,body.overlap)
        except ValueError as e: raise HTTPException(422,str(e)) from e
        with db.session() as s:
            d=s.scalar(select(Document).where(Document.id==did).with_for_update())
            if not d or (user.role!='admin' and d.department!=user.department):
                raise HTTPException(404,'资料不存在或无维护权限')
            changed=s.execute(update(Document).where(Document.id==did,Document.status.in_(['review','failed'])).values(status='queued',updated=time.time(),chunks=0,error='',embedding_model=''))
            if changed.rowcount!=1: raise HTTPException(409,'只能修改待审核或索引失败的版本，请刷新资料状态')
            opts=s.get(IndexOptions,did)
            if opts is None:
                opts=IndexOptions(doc_id=did); s.add(opts)
            opts.chunk_size=body.chunk_size; opts.overlap=body.overlap; opts.dimension=None
            s.execute(delete(Chunk).where(Chunk.doc_id==did))
            s.add(IngestJob(doc_id=did))
            s.add(Audit(actor=user.id,action='document.reindex',target=did,details=body.model_dump()))
        return {'ok':True}

    @app.post('/api/documents/{did}/retry')
    def retry(did:str,user=Depends(editor)):
        with db.session() as s:
            d=s.scalar(select(Document).where(Document.id==did).with_for_update())
            if not d or not can_read(d,user,False): raise HTTPException(404,'资料不存在')
            if d.status!='failed': raise HTTPException(409,'只能重试失败任务')
            d.status='queued'; d.error=''; s.add(IngestJob(doc_id=did)); s.add(Audit(actor=user.id,action='document.retry',target=did))
        return {'ok':True}

    @app.get('/api/tickets')
    def tickets(user=Depends(current)):
        with db.session() as s:
            query=select(Ticket).order_by(Ticket.created.desc()).limit(200)
            if user.role!='admin': query=query.where(Ticket.user_id==user.id)
            return [{k:getattr(t,k) for k in ['id','department','question','status','resolution','created']} for t in s.scalars(query)]

    @app.post('/api/tickets/{tid}/resolve')
    def resolve(tid:str,body:ResolutionBody,user=Depends(admin)):
        with db.session() as s:
            ticket=s.scalar(select(Ticket).where(Ticket.id==tid).with_for_update())
            if not ticket: raise HTTPException(404,'工单不存在')
            if ticket.status!='open': raise HTTPException(409,'工单已处理')
            ticket.status='resolved'; ticket.resolution=body.resolution; ticket.resolved_by=user.id
            s.add(Audit(actor=user.id,action='ticket.resolved',target=tid))
        return {'ok':True}

    @app.post('/api/feedback')
    def feedback(body:FeedbackBody,user=Depends(current)):
        with db.session() as s:
            turn=s.get(Turn,body.turn_id)
            c=s.get(Conversation,turn.conversation_id) if turn else None
            if not c or c.user_id!=user.id: raise HTTPException(404,'回答不存在')
            f=s.scalar(select(Feedback).where(Feedback.user_id==user.id,Feedback.turn_id==body.turn_id))
            if not f: f=Feedback(user_id=user.id,turn_id=body.turn_id); s.add(f)
            f.rating=body.rating; f.note=body.note
        return {'ok':True}

    @app.get('/api/admin/users')
    def users(user=Depends(admin)):
        with db.session() as s: return [user_dict(u) for u in s.scalars(select(User).order_by(User.created))]

    @app.post('/api/admin/users')
    def add_user(body:UserBody,user=Depends(admin)):
        if body.department not in DEPARTMENTS or body.role not in {'member','editor','admin'}: raise HTTPException(400,'部门或角色不正确')
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',body.email): raise HTTPException(400,'请输入有效邮箱')
        with db.session() as s:
            if s.scalar(select(User).where(User.email==body.email.lower())): raise HTTPException(409,'账号已存在')
            new=User(email=body.email.lower(),name=body.name,department=body.department,role=body.role,password=PASSWORDS.hash(body.password)); s.add(new); s.flush()
            s.add(Audit(actor=user.id,action='user.created',target=new.id,details={'role':new.role,'department':new.department}))
            return user_dict(new)

    @app.post('/api/admin/users/{user_id}/disable')
    def disable(user_id:str,user=Depends(admin)):
        if user_id==user.id: raise HTTPException(400,'不能停用当前账号')
        with db.session() as s:
            target=s.get(User,user_id)
            if not target: raise HTTPException(404,'账号不存在')
            target.active=False; s.execute(delete(AuthSession).where(AuthSession.user_id==user_id)); s.add(Audit(actor=user.id,action='user.disabled',target=user_id))
        return {'ok':True}

    @app.get('/api/admin/audit')
    def audit(user=Depends(admin)):
        with db.session() as s:
            return [{k:getattr(a,k) for k in ['id','actor','action','target','details','created']} for a in s.scalars(select(Audit).order_by(Audit.created.desc()).limit(200))]

    @app.get('/api/admin/models')
    def get_models(user=Depends(admin)):
        return model_settings.public()

    @app.put('/api/admin/models/{group}')
    def save_models(group:str,body:ModelConfigBody,user=Depends(admin)):
        def guard(old,new):
            fields=('embedding_base','embedding_model','embedding_dimension')
            if any(getattr(old,k)!=getattr(new,k) for k in fields):
                with db.session() as s:
                    indexed=s.scalar(select(Document.id).where(Document.chunks>0,Document.embedding_model.notin_(['','lexical-development'])).limit(1))
                    active=s.scalar(select(IngestJob.id).where(IngestJob.status.in_(['queued','processing'])).limit(1))
                if indexed or active: raise RuntimeError('已有向量索引或正在处理的文档。请先由运维迁移索引，再更换 Embedding 模型、地址或维度；当前配置未修改')
        try:
            result=model_settings.save(group,body.values,body.revision,body.key_action,body.api_key,guard)
        except ValueError as e: raise HTTPException(400,str(e))
        except RuntimeError as e: raise HTTPException(409,str(e))
        db.audit(user.id,'models.updated',group,{'fields':sorted(body.values),'key_action':body.key_action})
        return result

    @app.post('/api/admin/models/{group}/test')
    def test_model(group:str,body:ModelConfigBody,user=Depends(admin)):
        if not rt.rate_limit('model-test:'+user.id,6,60): raise HTTPException(429,'连接测试过于频繁，请一分钟后重试')
        started=time.perf_counter()
        try:
            candidate,_=model_settings.candidate(group,body.values,body.key_action,body.api_key)
            local=(group=='embedding' and candidate.embedding_base=='local://onnx') or (group=='rerank' and candidate.rerank_url=='local://onnx')
            if not local and not getattr(candidate,KEYS[group]): raise ValueError('请填写 API Key 后测试')
            models=Models(candidate)
            if group=='chat':
                response,_=models.complete('Return a JSON object with a single property ok set to true.',{'test':'connection probe; no enterprise data'},True,128)
                parsed=json.loads(response)
                if not isinstance(parsed,dict) or parsed.get('ok') is not True: raise ProviderError('服务有响应，但 JSON 指令兼容性测试未通过')
            elif group=='embedding': models.embed(['用于连接测试的合成文本'])
            else:
                _,used=models.rerank('测试面料',[{'text':'测试面料是纯棉。'},{'text':'这是合成连接测试资料。'}])
                if not used: raise ValueError('请填写重排接口完整地址')
        except (ValueError,ProviderError) as e:
            detail=str(e) if not isinstance(e,json.JSONDecodeError) else '服务有响应，但没有返回有效 JSON'
            return {'ok':False,'message':detail,'latency_ms':round((time.perf_counter()-started)*1000)}
        db.audit(user.id,'models.tested',group,{'ok':True})
        return {'ok':True,'message':'连接与响应格式检查通过；未发送企业资料，测试未保存配置','latency_ms':round((time.perf_counter()-started)*1000)}

    @app.get('/api/admin/system')
    def system(user=Depends(admin)):
        cfg=model_settings.effective()
        def check(fn):
            try: fn(); return 'healthy'
            except Exception: return 'unavailable'
        nodes=[{'name':'MySQL' if cfg.database_url.startswith('mysql') else 'SQLite','purpose':'持久化数据','status':check(lambda:db.engine.connect().close())},
               {'name':'Redis','purpose':'事实缓存 / 会话锁 / 限流','status':check(lambda:rt.redis.ping()) if rt.redis else 'not_configured'},
               {'name':'Milvus','purpose':'语义向量检索','status':check(lambda:VectorStore(cfg).client.list_collections()) if cfg.milvus_uri else 'not_configured'},
               {'name':cfg.llm_model,'purpose':'意图识别 / 回答 / 摘要','status':'configured_unverified' if cfg.llm_key else 'not_configured'},
               {'name':cfg.embedding_model,'purpose':'文本向量化','status':'configured_unverified' if cfg.embedding_key or cfg.embedding_base=="local://onnx" else 'not_configured'},
               {'name':cfg.rerank_model,'purpose':'候选精排','status':'configured_unverified' if cfg.rerank_key or cfg.rerank_url=="local://onnx" else 'not_configured'}]
        with db.session() as s:
            counts={k:s.scalar(select(func.count()).select_from(model)) for k,model in [('documents',Document),('conversations',Conversation),('turns',Turn),('users',User)]}
            failed=s.scalar(select(func.count()).select_from(IngestJob).where(IngestJob.status=='failed'))
        return {'mode':cfg.mode,'nodes':nodes,'counts':counts,'failed_jobs':failed,'organization':cfg.organization}

    dist=ROOT/'frontend'/'dist'
    if (dist/'assets').exists(): app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')

    @app.get('/')
    def index():
        if not (dist/'index.html').exists(): return JSONResponse({'detail':'请先构建前端 npm run build'},status_code=503)
        return FileResponse(dist/'index.html')

    return app


app=create_app()
