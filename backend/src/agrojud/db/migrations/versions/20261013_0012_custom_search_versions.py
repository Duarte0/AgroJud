"""Add versioned user-defined search definitions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261013_0012"
down_revision: str | Sequence[str] | None = "20261012_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custom_searches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("current_version >= 1", name="ck_custom_searches_version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_custom_searches"),
    )
    op.create_index("ix_custom_searches_created", "custom_searches", ["created_at", "id"])

    op.create_table(
        "custom_search_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("custom_search_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("class_codes", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("subject_codes", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("dimension_combination", sa.String(length=3), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_custom_search_versions_positive"),
        sa.CheckConstraint(
            "length(btrim(name)) between 1 and 160", name="ck_custom_search_versions_name"
        ),
        sa.CheckConstraint(
            "description is null or length(description) <= 2000",
            name="ck_custom_search_versions_description",
        ),
        sa.CheckConstraint(
            "cardinality(class_codes) + cardinality(subject_codes) > 0",
            name="ck_custom_search_versions_criteria_nonempty",
        ),
        sa.CheckConstraint(
            "dimension_combination in ('AND', 'OR')",
            name="ck_custom_search_versions_combination",
        ),
        sa.ForeignKeyConstraint(
            ["custom_search_id"],
            ["custom_searches.id"],
            name="fk_custom_search_versions_search",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_custom_search_versions"),
        sa.UniqueConstraint("custom_search_id", "version", name="uq_custom_search_versions_number"),
    )
    op.create_index(
        "ix_custom_search_versions_search",
        "custom_search_versions",
        ["custom_search_id", "version"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_custom_search_version_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'custom search versions are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_custom_search_versions_immutable
        BEFORE UPDATE OR DELETE ON custom_search_versions
        FOR EACH ROW EXECUTE FUNCTION reject_custom_search_version_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_custom_search_versions_immutable ON custom_search_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_custom_search_version_mutation()")
    op.drop_index("ix_custom_search_versions_search", table_name="custom_search_versions")
    op.drop_table("custom_search_versions")
    op.drop_index("ix_custom_searches_created", table_name="custom_searches")
    op.drop_table("custom_searches")
