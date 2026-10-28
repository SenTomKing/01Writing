"""不可变的账户内资料索引与原文片段；扩展由管理员预装。"""
from alembic import op
import sqlalchemy as sa
from writing_server.vector_type import Vector

revision='v4_vectors'
down_revision='v3_research'
branch_labels=None
depends_on=None


def upgrade():
    if not op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')")):
        raise RuntimeError('请先由数据库管理员安装pgvector扩展：CREATE EXTENSION vector WITH SCHEMA public；应用角色无需管理员权限')
    op.create_table('wa_source_indexes',
        sa.Column('id',sa.String(36),primary_key=True),sa.Column('user_id',sa.String(36),sa.ForeignKey('wa_users.id'),nullable=False),
        sa.Column('source_id',sa.String(36),nullable=False),sa.Column('fingerprint',sa.String(64),nullable=False),
        sa.Column('content_hash',sa.String(64),nullable=False),sa.Column('title',sa.String(200),nullable=False),
        sa.Column('embedding_id',sa.String(64),nullable=False),sa.Column('config',sa.JSON(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('user_id','fingerprint',name='wa_source_index_fingerprint'),
        sa.UniqueConstraint('id','user_id',name='wa_source_index_owner'))
    op.create_table('wa_source_chunks',
        sa.Column('index_id',sa.String(36),primary_key=True),sa.Column('offset',sa.Integer(),primary_key=True),
        sa.Column('user_id',sa.String(36),nullable=False),sa.Column('content',sa.Text(),nullable=False),
        sa.Column('page',sa.Integer()),sa.Column('embedding',Vector()),
        sa.ForeignKeyConstraint(['index_id','user_id'],['wa_source_indexes.id','wa_source_indexes.user_id'],ondelete='CASCADE'),
        sa.CheckConstraint('"offset">=0 AND length(content)>0',name='wa_chunk_content'))
    op.create_index('wa_chunk_owner_index','wa_source_chunks',['user_id','index_id'])
    # 暂不建ANN：精确检索先作为生产正确性基线，ANN评测另在隔离表中执行。


def downgrade():
    op.drop_table('wa_source_chunks');op.drop_table('wa_source_indexes')
