"""Keep a durable record of every email delivery attempt."""

import sqlalchemy as sa
from alembic import op

revision = "c20f25da1021"
down_revision = "b19f24d9a010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("notification_id", sa.String(36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
    )
    op.create_index("ix_delivery_attempts_notification_id", "delivery_attempts", ["notification_id"])


def downgrade():
    op.drop_index("ix_delivery_attempts_notification_id", table_name="delivery_attempts")
    op.drop_table("delivery_attempts")
