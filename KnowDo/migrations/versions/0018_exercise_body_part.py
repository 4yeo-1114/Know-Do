"""add optional body part to exercise sets

Revision ID: 0018_exercise_body_part
Revises: 0017_agent_message_sequence
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_exercise_body_part"
down_revision = "0017_agent_message_sequence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "exercise_sets",
        sa.Column("body_part", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("exercise_sets", "body_part")
