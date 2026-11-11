"""Persist upload-time split settings and actual indexed vector dimensions."""
from alembic import op
import sqlalchemy as sa

revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('document_index_options',
        sa.Column('doc_id',sa.String(32),sa.ForeignKey('knowledge_documents.id'),primary_key=True),
        sa.Column('chunk_size',sa.Integer,nullable=False),
        sa.Column('overlap',sa.Integer,nullable=False),
        sa.Column('dimension',sa.Integer,nullable=True))

def downgrade():
    op.drop_table('document_index_options')
