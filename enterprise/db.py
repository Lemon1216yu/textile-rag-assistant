import time
import uuid
from contextlib import contextmanager

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def uid(): return uuid.uuid4().hex


class Base(DeclarativeBase): pass


class User(Base):
    __tablename__='users'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    email:Mapped[str]=mapped_column(String(190),unique=True)
    name:Mapped[str]=mapped_column(String(80))
    department:Mapped[str]=mapped_column(String(40))
    role:Mapped[str]=mapped_column(String(20),default='member')
    password:Mapped[str]=mapped_column(String(255))
    active:Mapped[bool]=mapped_column(Boolean,default=True)
    created:Mapped[float]=mapped_column(Float(53),default=time.time)


class AuthSession(Base):
    __tablename__='auth_sessions'
    token_hash:Mapped[str]=mapped_column(String(64),primary_key=True)
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id'),index=True)
    csrf:Mapped[str]=mapped_column(String(64))
    expires:Mapped[float]=mapped_column(Float(53),index=True)


class Document(Base):
    __tablename__='knowledge_documents'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    title:Mapped[str]=mapped_column(String(240))
    department:Mapped[str]=mapped_column(String(40),index=True)
    fabric:Mapped[str]=mapped_column(String(40),default='')
    version:Mapped[str]=mapped_column(String(40),default='1.0')
    status:Mapped[str]=mapped_column(String(20),default='queued',index=True)
    path:Mapped[str]=mapped_column(Text)
    checksum:Mapped[str]=mapped_column(String(64))
    owner_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    approved_by:Mapped[str|None]=mapped_column(String(32),nullable=True)
    replaces_id:Mapped[str|None]=mapped_column(String(32),nullable=True)
    error:Mapped[str]=mapped_column(Text,default='')
    chunks:Mapped[int]=mapped_column(Integer,default=0)
    created:Mapped[float]=mapped_column(Float(53),default=time.time)
    updated:Mapped[float]=mapped_column(Float(53),default=time.time)
    embedding_model:Mapped[str]=mapped_column(String(100),default='')


class IndexOptions(Base):
    __tablename__='document_index_options'
    doc_id:Mapped[str]=mapped_column(ForeignKey('knowledge_documents.id'),primary_key=True)
    chunk_size:Mapped[int]=mapped_column(Integer)
    overlap:Mapped[int]=mapped_column(Integer)
    dimension:Mapped[int|None]=mapped_column(Integer,nullable=True)


class Chunk(Base):
    __tablename__='knowledge_chunks'
    id:Mapped[str]=mapped_column(String(48),primary_key=True)
    doc_id:Mapped[str]=mapped_column(ForeignKey('knowledge_documents.id'),index=True)
    page:Mapped[int]=mapped_column(Integer)
    section:Mapped[str]=mapped_column(String(240))
    text:Mapped[str]=mapped_column(Text)
    vector:Mapped[list]=mapped_column(JSON,default=list)


class IngestJob(Base):
    __tablename__='ingest_jobs'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    doc_id:Mapped[str]=mapped_column(ForeignKey('knowledge_documents.id'),index=True)
    status:Mapped[str]=mapped_column(String(20),default='queued',index=True)
    attempts:Mapped[int]=mapped_column(Integer,default=0)
    lease_until:Mapped[float]=mapped_column(Float(53),default=0)
    error:Mapped[str]=mapped_column(Text,default='')
    created:Mapped[float]=mapped_column(Float(53),default=time.time)


class Conversation(Base):
    __tablename__='conversations'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id'),index=True)
    title:Mapped[str]=mapped_column(String(160),default='新对话')
    memory:Mapped[dict]=mapped_column(JSON,default=dict)
    archived:Mapped[bool]=mapped_column(Boolean,default=False)
    created:Mapped[float]=mapped_column(Float(53),default=time.time)
    updated:Mapped[float]=mapped_column(Float(53),default=time.time,index=True)


class Turn(Base):
    __tablename__='conversation_turns'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    conversation_id:Mapped[str]=mapped_column(ForeignKey('conversations.id'),index=True)
    request_id:Mapped[str]=mapped_column(String(64),unique=True)
    question:Mapped[str]=mapped_column(Text)
    answer:Mapped[str]=mapped_column(Text)
    sources:Mapped[list]=mapped_column(JSON,default=list)
    trace:Mapped[dict]=mapped_column(JSON,default=dict)
    usage:Mapped[dict]=mapped_column(JSON,default=dict)
    created:Mapped[float]=mapped_column(Float(53),default=time.time)


class Ticket(Base):
    __tablename__='tickets'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id'),index=True)
    conversation_id:Mapped[str]=mapped_column(ForeignKey('conversations.id'))
    department:Mapped[str]=mapped_column(String(40))
    question:Mapped[str]=mapped_column(Text)
    status:Mapped[str]=mapped_column(String(20),default='open')
    resolution:Mapped[str]=mapped_column(Text,default='')
    resolved_by:Mapped[str|None]=mapped_column(String(32),nullable=True)
    created:Mapped[float]=mapped_column(Float(53),default=time.time)


class Audit(Base):
    __tablename__='audit_events'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    actor:Mapped[str]=mapped_column(String(80),index=True)
    action:Mapped[str]=mapped_column(String(80))
    target:Mapped[str]=mapped_column(String(80),default='')
    details:Mapped[dict]=mapped_column(JSON,default=dict)
    created:Mapped[float]=mapped_column(Float(53),default=time.time,index=True)


class Feedback(Base):
    __tablename__='answer_feedback'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    turn_id:Mapped[str]=mapped_column(ForeignKey('conversation_turns.id'))
    rating:Mapped[int]=mapped_column(Integer)
    note:Mapped[str]=mapped_column(Text,default='')
    created:Mapped[float]=mapped_column(Float(53),default=time.time)
    __table_args__=(Index('uq_feedback_user_turn','user_id','turn_id',unique=True),)


class Database:
    def __init__(self,url):
        kwargs={'pool_pre_ping':True}
        if url.startswith('sqlite'):
            kwargs['connect_args']={'check_same_thread':False,'timeout':30}
        self.engine=create_engine(url,**kwargs)
        self.factory=sessionmaker(self.engine,expire_on_commit=False)

    @contextmanager
    def session(self):
        with self.factory() as session:
            with session.begin():
                yield session

    def audit(self,actor,action,target='',details=None):
        with self.session() as s:
            s.add(Audit(actor=actor,action=action,target=target,details=details or {}))
