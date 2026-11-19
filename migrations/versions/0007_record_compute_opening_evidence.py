"""Record the actual Compute opening and its evidence."""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("compute_provisions", sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("compute_provisions", sa.Column("delivery_owner", sa.String(200), nullable=True))
    op.add_column("compute_provisions", sa.Column("opening_attachment_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_compute_provision_opening_attachment", "compute_provisions", "attachments",
        ["opening_attachment_id"], ["id"],
    )


def downgrade():
    op.drop_constraint("fk_compute_provision_opening_attachment", "compute_provisions", type_="foreignkey")
    for column in ("opening_attachment_id", "delivery_owner", "opened_at"):
        op.drop_column("compute_provisions", column)
