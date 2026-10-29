"""Keep the verified-record evidence separate from latest failed lookup evidence."""

import sqlalchemy as sa
from alembic import op

revision = "b19f24d9a010"
down_revision = "ae0d637c92e0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("source_states", sa.Column("verified_evidence", sa.String(240), nullable=True))
    op.execute("UPDATE source_states SET verified_evidence=evidence WHERE outcome IN ('FOUND','NO_DEBT')")


def downgrade():
    op.drop_column("source_states", "verified_evidence")
