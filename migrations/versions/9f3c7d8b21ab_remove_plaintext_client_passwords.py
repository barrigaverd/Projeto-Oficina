"""remove plaintext client passwords

Revision ID: 9f3c7d8b21ab
Revises: 71f4f6dccc9e
Create Date: 2026-04-17 18:40:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "9f3c7d8b21ab"
down_revision = "71f4f6dccc9e"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE cliente SET senha_plana_temporaria = NULL")


def downgrade():
    pass
