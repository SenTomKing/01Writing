"""每账户凭证只保存认证加密密文，不迁入部署者的全局密钥。"""
from alembic import op
import sqlalchemy as sa

revision = 'v6_credentials'
down_revision = 'v4_vectors'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('wa_user_credentials',
        sa.Column('user_id', sa.String(36), sa.ForeignKey('wa_users.id'), primary_key=True),
        sa.Column('revision', sa.String(36), nullable=False),
        sa.Column('ciphertext', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('wa_user_credentials')
