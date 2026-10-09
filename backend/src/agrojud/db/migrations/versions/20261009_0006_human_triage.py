"""Persist human triage separately from source data and preserve every change."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0006"
down_revision: str | Sequence[str] | None = "20261009_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "process_triage",
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=12), nullable=False),
        sa.Column("rural_link", sa.String(length=12), nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision in ('pending', 'relevant', 'discarded')", name="ck_process_triage_decision"
        ),
        sa.CheckConstraint(
            "rural_link in ('unconfirmed', 'confirmed')", name="ck_process_triage_rural_link"
        ),
        sa.CheckConstraint("length(note) <= 5000", name="ck_process_triage_note_length"),
        sa.CheckConstraint(
            "rural_link <> 'confirmed' or length(btrim(note)) > 0",
            name="ck_process_triage_confirmation_note",
        ),
        sa.CheckConstraint("version >= 1", name="ck_process_triage_version_positive"),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_process_triage_process_id_processes",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("process_id", name="pk_process_triage"),
    )
    op.create_index("ix_process_triage_decision", "process_triage", ["decision", "process_id"])
    op.create_index("ix_process_triage_rural_link", "process_triage", ["rural_link", "process_id"])

    op.create_table(
        "process_triage_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("new_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("origin", sa.String(length=12), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_process_triage_history_version_positive"),
        sa.CheckConstraint("origin = 'manual'", name="ck_process_triage_history_origin_manual"),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_triage_history_process",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_process_triage_history"),
        sa.UniqueConstraint("process_id", "version", name="uq_process_triage_history_version"),
    )
    op.execute(
        """
        CREATE FUNCTION reject_process_triage_history_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'process_triage_history is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_process_triage_history_append_only
        BEFORE UPDATE OR DELETE ON process_triage_history
        FOR EACH ROW EXECUTE FUNCTION reject_process_triage_history_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_process_triage_history_append_only ON process_triage_history"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_process_triage_history_mutation()")
    op.drop_table("process_triage_history")
    op.drop_index("ix_process_triage_rural_link", table_name="process_triage")
    op.drop_index("ix_process_triage_decision", table_name="process_triage")
    op.drop_table("process_triage")
