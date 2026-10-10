"""Add fake-only tenant classification claims and results."""

from alembic import op
import sqlalchemy as sa

revision = "0007_classification"
down_revision = "0006_outbox_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "classification_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("requesting_actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("opening_message_id", sa.Uuid(), nullable=True),
        *[
            sa.Column(name, sa.String(length), nullable=False)
            for name, length in (
                ("input_fingerprint", 64),
                ("config_digest", 64),
                ("configuration_version", 100),
                ("input_policy_version", 100),
                ("taxonomy_version", 100),
                ("schema_version", 100),
                ("prompt_version", 100),
                ("adapter_version", 100),
                ("provider", 100),
                ("model", 100),
                ("mode", 10),
                ("state", 20),
            )
        ],
        sa.Column("outcome", sa.String(30), nullable=True),
        sa.Column("category", sa.String(30), nullable=True),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(18, 8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_classification_ticket",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "requesting_actor_user_id"],
            ["organization_members.organization_id", "organization_members.user_id"],
            name="fk_classification_actor_membership",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "ticket_id",
            "input_fingerprint",
            "config_digest",
            name="uq_classification_identity",
        ),
        sa.CheckConstraint(
            "state IN ('in_progress','succeeded','stale','failed','unknown')",
            name="state",
        ),
        sa.CheckConstraint("mode = 'fake'", name="fake_only"),
        sa.CheckConstraint("attempt_count IN (0,1)", name="attempt_count"),
        sa.CheckConstraint("elapsed_ms IS NULL OR elapsed_ms >= 0", name="elapsed_ms"),
        sa.CheckConstraint(
            "input_tokens IS NULL AND output_tokens IS NULL AND cost IS NULL",
            name="fake_usage_unmeasured",
        ),
        sa.CheckConstraint(
            "(state = 'succeeded' AND error_code IS NULL AND completed_at IS NOT NULL "
            "AND ((outcome = 'classified' AND category IS NOT NULL AND category IN "
            "('account_access','billing','technical_issue','how_to','feature_request','other')) "
            "OR (outcome = 'insufficient_context' AND category IS NULL))) "
            "OR (state <> 'succeeded' AND outcome IS NULL AND category IS NULL)",
            name="result_pair",
        ),
        sa.CheckConstraint(
            "state <> 'succeeded' OR outcome IS NOT NULL", name="success_outcome"
        ),
    )
    op.create_index(
        "ix_classification_ticket_created",
        "classification_operations",
        ["organization_id", "ticket_id", "created_at", "id"],
    )


def downgrade() -> None:
    # Claims may represent uncertain work. Do not erase unreconciled evidence.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM classification_operations) "
        "THEN RAISE EXCEPTION 'Classification evidence remains; archive/reconcile before downgrade'; END IF; END $$"
    )
    op.drop_index(
        "ix_classification_ticket_created", table_name="classification_operations"
    )
    op.drop_table("classification_operations")
