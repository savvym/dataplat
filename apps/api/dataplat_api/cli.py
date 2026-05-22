"""CLI commands for dataplat_api — S007-F-007.

Usage (from apps/api/):
    uv run python -m dataplat_api.cli seed-admin --email ADMIN@EXAMPLE.COM --password PW

Hard invariant #5 compliance: all DB interaction is async.
  - Entry point calls asyncio.run(seed_admin(...)).
  - Inside seed_admin, SessionLocal() yields an AsyncSession.
  - All queries use await session.execute(select(...)).
  - No session.query(), no sync session anywhere.

# TODO: migrate to typer if CLI grows beyond 2 commands.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import bcrypt
from sqlalchemy import select

from dataplat_api.db.models import User
from dataplat_api.db.session import SessionLocal


async def seed_admin(email: str, password: str) -> None:
    """Insert a single admin user row into the users table.

    Idempotent: if a user with the given email already exists, prints a message
    and returns without modifying the database (exit code 0).

    Args:
        email:    Email address to use as login username.
        password: Plaintext password; hashed with bcrypt before storage.
    """
    async with SessionLocal() as session:
        # Check for existing user — idempotency guard.
        result = await session.execute(select(User).where(User.email == email))
        existing: User | None = result.scalars().first()
        if existing is not None:
            print(f"Admin user {email!r} already exists. Skipping.")
            return

        # Hash the password with bcrypt (rounds=12).
        hashed = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt(rounds=12),
        ).decode("utf-8")

        user = User(email=email, hashed_password=hashed)
        session.add(user)
        await session.commit()
        print(f"Admin user {email!r} created successfully.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="dataplat_api CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # seed-admin sub-command
    seed_parser = subparsers.add_parser(
        "seed-admin",
        help="Create the admin user in the users table (idempotent).",
    )
    seed_parser.add_argument("--email", required=True, help="Admin email address.")
    seed_parser.add_argument("--password", required=True, help="Admin password (plaintext; bcrypt-hashed before storage).")

    args = parser.parse_args()

    if args.command == "seed-admin":
        asyncio.run(seed_admin(args.email, args.password))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
