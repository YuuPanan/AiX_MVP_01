"""Record receiving-side proof of bank credit."""

import sqlalchemy as sa
from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

SCOPE = (
    "(order_id IS NOT NULL AND supplier_id IS NULL AND kind IN "
    "('CONTRACT', 'PAYMENT', 'DELIVERY', 'DISPUTE', 'REFUND'{bank_credit})) OR "
    "(order_id IS NULL AND supplier_id IS NOT NULL AND kind = 'SUPPLIER_EVIDENCE')"
)


def upgrade():
    op.add_column("order_payments", sa.Column("bank_credited_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("ck_attachment_scope_kind", "attachments", type_="check")
    op.create_check_constraint("ck_attachment_scope_kind", "attachments", SCOPE.format(bank_credit=", 'BANK_CREDIT'"))


def downgrade():
    op.drop_constraint("ck_attachment_scope_kind", "attachments", type_="check")
    # Reject the downgrade if receiving-side files exist, preserving their private access.
    op.create_check_constraint("ck_attachment_scope_kind", "attachments", SCOPE.format(bank_credit=""))
    op.drop_column("order_payments", "bank_credited_at")
