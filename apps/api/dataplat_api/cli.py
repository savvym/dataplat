"""CLI commands for dataplat_api — S007-F-007, S015-F-015.

Usage (from apps/api/):
    uv run python -m dataplat_api.cli seed-admin --email ADMIN@EXAMPLE.COM --password PW
    uv run python -m dataplat_api.cli seed-operators

Hard invariant #5 compliance: all DB interaction is async.
  - Entry point calls asyncio.run(seed_admin(...)) / asyncio.run(seed_operators()).
  - Inside each seed function, SessionLocal() yields an AsyncSession.
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

from dataplat_api.db.models import Operator, User
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


async def seed_operators() -> None:
    """Insert the canonical operator registry entries into the operator table.

    Idempotent: if an operator with the same (name, version) pair already exists,
    prints a message and returns without modifying the database (exit code 0).

    Hard invariant #5 compliance: all DB interaction uses async session + select().
    No session.query(). No sync session anywhere.
    """
    async with SessionLocal() as session:
        # Idempotency guard — key on (name, version) matching UNIQUE constraint
        # uq_operator_name_version.
        result = await session.execute(
            select(Operator)
            .where(Operator.name == "mineru")
            .where(Operator.version == "0.1.0")
        )
        existing: Operator | None = result.scalars().first()
        if existing is not None:
            print("Operator 'mineru@0.1.0' already exists. Skipping.")
            return

        op = Operator(
            name="mineru",
            version="0.1.0",
            category="extractor",
            input_kind="source",
            output_kind="document",
            # Placeholder image — real MinerU worker image built in F-019.
            # Uses dataplat/<name>:<version> convention so it can be replaced
            # by bumping the version when F-019 ships.
            image="dataplat/mineru:0.1.0",
            description=(
                "MinerU PDF/document extractor — converts raw source files "
                "into structured documents."
            ),
            reference_url="https://github.com/opendatalab/MinerU",
            # Minimal JSON Schema (3 properties, all optional — each has a
            # default).  required:[] is explicit per JSON Schema best-practice
            # so UI form renderers do not have to treat an absent key as [].
            config_schema={
                "type": "object",
                "properties": {
                    "output_format": {
                        "type": "string",
                        "enum": ["markdown", "json"],
                        "default": "markdown",
                        "description": "Output format for extracted document content.",
                    },
                    "language": {
                        "type": "string",
                        "default": "auto",
                        "description": (
                            "Hint for OCR language detection "
                            "(ISO 639-1 code or 'auto')."
                        ),
                    },
                    "enable_ocr": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether to apply OCR on scanned pages.",
                    },
                },
                "required": [],
            },
        )
        session.add(op)
        await session.commit()
        print("Operator 'mineru@0.1.0' created successfully.")


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
    seed_parser.add_argument(
        "--password",
        required=True,
        help="Admin password (plaintext; bcrypt-hashed before storage).",
    )

    # seed-operators sub-command (F-015)
    subparsers.add_parser(
        "seed-operators",
        help="Seed the operator registry with built-in operator definitions (idempotent).",
    )

    args = parser.parse_args()

    if args.command == "seed-admin":
        asyncio.run(seed_admin(args.email, args.password))
    elif args.command == "seed-operators":
        asyncio.run(seed_operators())
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
