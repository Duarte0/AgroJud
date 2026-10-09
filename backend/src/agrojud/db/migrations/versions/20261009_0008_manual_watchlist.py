"""Persist the unique manual watch entry and its append-only history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0008"
down_revision: str | Sequence[str] | None = "20261009_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "process_watchlist_entries",
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "included_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(active and removed_at is null) or (not active and removed_at is not null)",
            name="ck_process_watchlist_active_removed_at",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_process_watchlist_process",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("process_id", name="pk_process_watchlist_entries"),
    )
    op.create_index(
        "ix_process_watchlist_active_included",
        "process_watchlist_entries",
        ["included_at", "process_id"],
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "process_watchlist_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=12), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("action in ('included', 'removed')", name="ck_watchlist_history_action"),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["processes.id"],
            name="fk_watchlist_history_process",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_process_watchlist_history"),
    )
    op.create_index(
        "ix_watchlist_history_process_created",
        "process_watchlist_history",
        ["process_id", "created_at", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_process_watchlist_history_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'process_watchlist_history is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_process_watchlist_history_append_only
        BEFORE UPDATE OR DELETE ON process_watchlist_history
        FOR EACH ROW EXECUTE FUNCTION reject_process_watchlist_history_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_process_watchlist_history_append_only "
        "ON process_watchlist_history"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_process_watchlist_history_mutation()")
    op.drop_index("ix_watchlist_history_process_created", table_name="process_watchlist_history")
    op.drop_table("process_watchlist_history")
    op.drop_index("ix_process_watchlist_active_included", table_name="process_watchlist_entries")
    op.drop_table("process_watchlist_entries")
