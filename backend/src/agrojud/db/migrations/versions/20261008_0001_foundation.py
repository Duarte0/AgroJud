"""Initial foundation revision; it intentionally creates no product tables."""

from collections.abc import Sequence

revision: str = "20261008_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Let Alembic record the revision without creating domain tables."""


def downgrade() -> None:
    """Alembic removes its version table when downgrading this initial revision."""
