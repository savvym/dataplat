"""Add hashed_password column to users table — S007-F-007.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-22

The baseline schema (0001) created the `users` table with only four columns:
id, email, name, created_at.  F-007 adds password-based authentication and
requires a column to store the bcrypt hash of the admin user's password.

Column semantics:
  - NOT NULL: every user that can authenticate must have a password hash.
  - DEFAULT '': migration-time-only default so that `alembic upgrade head`
    succeeds even when the `users` table already contains rows (e.g., if a
    pre-F-007 migration left orphan rows).  The empty string is an unambiguous
    sentinel — bcrypt.hashpw() never produces an empty string — so
    hashed_password = '' means "no password set yet".  Application code
    (seed CLI, auth endpoint) never inserts an empty string intentionally;
    any user with hashed_password = '' cannot authenticate until re-seeded.

Downgrade: drops the column, restoring the 0001 schema.  Safe to run before
F-008 ships because no production code depends on hashed_password yet.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "hashed_password",
            sa.Text,
            nullable=False,
            server_default=sa.text("''"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "hashed_password")
