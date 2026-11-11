"""Initial enterprise schema.

Revision ID: 0001
"""
from alembic import op
import sqlalchemy as sa

revision='0001'
down_revision=None
branch_labels=None
depends_on=None


def upgrade():
    # Initial schema snapshot is intentionally fixed, not imported from live models.
    tables={
        'users':[sa.Column('id',sa.String(32),primary_key=True),sa.Column('email',sa.String(190),nullable=False,unique=True),sa.Column('name',sa.String(80),nullable=False),sa.Column('department',sa.String(40),nullable=False),sa.Column('role',sa.String(20),nullable=False),sa.Column('password',sa.String(255),nullable=False),sa.Column('active',sa.Boolean(),nullable=False),sa.Column('created',sa.Float(53),nullable=False)],
        'auth_sessions':[sa.Column('token_hash',sa.String(64),primary_key=True),sa.Column('user_id',sa.String(32),sa.ForeignKey('users.id'),nullable=False),sa.Column('csrf',sa.String(64),nullable=False),sa.Column('expires',sa.Float(53),nullable=False)],
        'knowledge_documents':[sa.Column('id',sa.String(32),primary_key=True),sa.Column('title',sa.String(240),nullable=False),sa.Column('department',sa.String(40),nullable=False),sa.Column('fabric',sa.String(40),nullable=False),sa.Column('version',sa.String(40),nullable=False),sa.Column('status',sa.String(20),nullable=False),sa.Column('path',sa.Text(),nullable=False),sa.Column('checksum',sa.String(64),nullable=False),sa.Column('owner_id',sa.String(32),sa.ForeignKey('users.id'),nullable=False),sa.Column('approved_by',sa.String(32)),sa.Column('replaces_id',sa.String(32)),sa.Column('error',sa.Text(),nullable=False),sa.Column('chunks',sa.Integer(),nullable=False),sa.Column('created',sa.Float(53),nullable=False),sa.Column('updated',sa.Float(53),nullable=False),sa.Column('embedding_model',sa.String(100),nullable=False)],
        'knowledge_chunks':[sa.Column('id',sa.String(48),primary_key=True),sa.Column('doc_id',sa.String(32),sa.ForeignKey('knowledge_documents.id'),nullable=False),sa.Column('page',sa.Integer(),nullable=False),sa.Column('section',sa.String(240),nullable=False),sa.Column('text',sa.Text(),nullable=False),sa.Column('vector',sa.JSON(),nullable=False)],
        'ingest_jobs':[sa.Column('id',sa.String(32),primary_key=True),sa.Column('doc_id',sa.String(32),sa.ForeignKey('knowledge_documents.id'),nullable=False),sa.Column('status',sa.String(20),nullable=False),sa.Column('attempts',sa.Integer(),nullable=False),sa.Column('lease_until',sa.Float(53),nullable=False),sa.Column('error',sa.Text(),nullable=False),sa.Column('created',sa.Float(53),nullable=False)],
        'conversations':[sa.Column('id',sa.String(32),primary_key=True),sa.Column('user_id',sa.String(32),sa.ForeignKey('users.id'),nullable=False),sa.Column('title',sa.String(160),nullable=False),sa.Column('memory',sa.JSON(),nullable=False),sa.Column('archived',sa.Boolean(),nullable=False),sa.Column('created',sa.Float(53),nullable=False),sa.Column('updated',sa.Float(53),nullable=False)],
        'conversation_turns':[sa.Column('id',sa.String(32),primary_key=True),sa.Column('conversation_id',sa.String(32),sa.ForeignKey('conversations.id'),nullable=False),sa.Column('request_id',sa.String(64),nullable=False,unique=True),sa.Column('question',sa.Text(),nullable=False),sa.Column('answer',sa.Text(),nullable=False),sa.Column('sources',sa.JSON(),nullable=False),sa.Column('trace',sa.JSON(),nullable=False),sa.Column('usage',sa.JSON(),nullable=False),sa.Column('created',sa.Float(53),nullable=False)],
        'tickets':[sa.Column('id',sa.String(32),primary_key=True),sa.Column('user_id',sa.String(32),sa.ForeignKey('users.id'),nullable=False),sa.Column('conversation_id',sa.String(32),sa.ForeignKey('conversations.id'),nullable=False),sa.Column('department',sa.String(40),nullable=False),sa.Column('question',sa.Text(),nullable=False),sa.Column('status',sa.String(20),nullable=False),sa.Column('resolution',sa.Text(),nullable=False),sa.Column('resolved_by',sa.String(32)),sa.Column('created',sa.Float(53),nullable=False)],
        'audit_events':[sa.Column('id',sa.String(32),primary_key=True),sa.Column('actor',sa.String(80),nullable=False),sa.Column('action',sa.String(80),nullable=False),sa.Column('target',sa.String(80),nullable=False),sa.Column('details',sa.JSON(),nullable=False),sa.Column('created',sa.Float(53),nullable=False)],
        'answer_feedback':[sa.Column('id',sa.String(32),primary_key=True),sa.Column('user_id',sa.String(32),sa.ForeignKey('users.id'),nullable=False),sa.Column('turn_id',sa.String(32),sa.ForeignKey('conversation_turns.id'),nullable=False),sa.Column('rating',sa.Integer(),nullable=False),sa.Column('note',sa.Text(),nullable=False),sa.Column('created',sa.Float(53),nullable=False)]
    }
    for name,columns in tables.items(): op.create_table(name,*columns)
    for table,columns in {'auth_sessions':['user_id','expires'],'knowledge_documents':['department','status'],'knowledge_chunks':['doc_id'],'ingest_jobs':['doc_id','status'],'conversations':['user_id','updated'],'conversation_turns':['conversation_id'],'tickets':['user_id'],'audit_events':['actor','created']}.items():
        for col in columns: op.create_index('ix_'+table+'_'+col,table,[col])
    op.create_index('uq_feedback_user_turn','answer_feedback',['user_id','turn_id'],unique=True)


def downgrade():
    for table in ['answer_feedback','audit_events','tickets','conversation_turns','conversations','ingest_jobs','knowledge_chunks','knowledge_documents','auth_sessions','users']:
        op.drop_table(table)
