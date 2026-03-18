"""add brew_methods column to coffees

Revision ID: 0001
Revises:
Create Date: 2026-03-17
"""

from alembic import op
import sqlalchemy as sa

# Revision identifiers used by Alembic
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("coffees", sa.Column("brew_methods", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("coffees", "brew_methods")
