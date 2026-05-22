"""ORM models for all 8 business tables — docs/data_platform_design.md §4.1.

SQLAlchemy 2.x declarative style with Mapped[T] + mapped_column().
No sync queries anywhere — this module is import-only; sessions live in
session.py and are always AsyncSession.

Hard invariant: never use session.query() — always select() + await session.execute().
"""

from __future__ import annotations

from typing import List, Optional

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ── 1. users ──────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    name: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[Optional[sa.DateTime]] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=text("now()"),
        nullable=True,
    )
    # Added S007-F-007: bcrypt hash of the user's password.
    # server_default='' matches the 0002 migration DDL DEFAULT ''; application
    # code always supplies a real hash — the empty string is a sentinel only.
    hashed_password: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default=text("''"),
    )


# ── 2. source_collection ──────────────────────────────────────────────────────

class SourceCollection(Base):
    __tablename__ = "source_collection"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    owner_id: Mapped[Optional[int]] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id"), nullable=True
    )
    dataset_card_md: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[Optional[sa.DateTime]] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=text("now()"),
        nullable=True,
    )
    updated_at: Mapped[Optional[sa.DateTime]] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=text("now()"),
        nullable=True,
    )


# ── 3. source ─────────────────────────────────────────────────────────────────

class Source(Base):
    __tablename__ = "source"

    __table_args__ = (
        sa.Index("idx_source_collection", "collection_id"),
        sa.Index("idx_source_sha256", "sha256"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    collection_id: Mapped[Optional[int]] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("source_collection.id", ondelete="CASCADE"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    original_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    storage_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sha256: Mapped[str] = mapped_column(sa.Text, nullable=False)
    size: Mapped[Optional[int]] = mapped_column(sa.BigInteger, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    license: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    source_metadata: Mapped[Optional[dict]] = mapped_column(
        postgresql.JSONB,
        server_default=text("'{}'::jsonb"),
        nullable=True,
    )
    dagster_partition_key: Mapped[str] = mapped_column(
        sa.Text, nullable=False, unique=True
    )
    preferred_extractor: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    uploaded_at: Mapped[Optional[sa.DateTime]] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=text("now()"),
        nullable=True,
    )


# ── 4. document_variant ───────────────────────────────────────────────────────

class DocumentVariant(Base):
    __tablename__ = "document_variant"

    __table_args__ = (
        sa.UniqueConstraint(
            "source_id", "extractor_name", "config_hash",
            name="uq_document_variant_source_extractor_config",
        ),
        sa.Index("idx_doc_variant_source", "source_id"),
        sa.Index(
            "idx_doc_canonical",
            "source_id",
            unique=True,
            postgresql_where=text("is_canonical"),
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    source_id: Mapped[Optional[int]] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("source.id", ondelete="CASCADE"),
        nullable=True,
    )
    extractor_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    extractor_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    storage_prefix: Mapped[str] = mapped_column(sa.Text, nullable=False)
    page_count: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    image_count: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    is_canonical: Mapped[Optional[bool]] = mapped_column(
        sa.Boolean,
        server_default=text("false"),
        nullable=True,
    )
    # materialized_at has DEFAULT NOW() per §4.1 line 277 — must NOT be default-free
    materialized_at: Mapped[Optional[sa.DateTime]] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=text("now()"),
        nullable=True,
    )
    dagster_run_id: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)


# ── 5. operator ───────────────────────────────────────────────────────────────
# 19 columns total: id + 18 from §4.1 lines 286-316

class Operator(Base):
    __tablename__ = "operator"

    __table_args__ = (
        sa.UniqueConstraint("name", "version", name="uq_operator_name_version"),
        sa.Index("idx_operator_category", "category", "is_active"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    category: Mapped[str] = mapped_column(sa.Text, nullable=False)
    input_kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    output_kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    output_schema: Mapped[Optional[dict]] = mapped_column(
        postgresql.JSONB, nullable=True
    )
    config_schema: Mapped[Optional[dict]] = mapped_column(
        postgresql.JSONB, nullable=True
    )
    default_config: Mapped[Optional[dict]] = mapped_column(
        postgresql.JSONB,
        server_default=text("'{}'::jsonb"),
        nullable=True,
    )
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    reference_url: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    example_input: Mapped[Optional[dict]] = mapped_column(
        postgresql.JSONB, nullable=True
    )
    example_output: Mapped[Optional[dict]] = mapped_column(
        postgresql.JSONB, nullable=True
    )
    image: Mapped[str] = mapped_column(sa.Text, nullable=False)
    entrypoint: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    estimated_cost_per_unit: Mapped[Optional[dict]] = mapped_column(
        postgresql.JSONB, nullable=True
    )
    rate_limit_per_minute: Mapped[Optional[int]] = mapped_column(
        sa.Integer, nullable=True
    )
    is_active: Mapped[Optional[bool]] = mapped_column(
        sa.Boolean,
        server_default=text("true"),
        nullable=True,
    )
    created_at: Mapped[Optional[sa.DateTime]] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=text("now()"),
        nullable=True,
    )


# ── 6. recipe ─────────────────────────────────────────────────────────────────

class Recipe(Base):
    __tablename__ = "recipe"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    owner_id: Mapped[Optional[int]] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id"), nullable=True
    )
    # definition is JSONB NOT NULL — every INSERT must supply a value; no default
    definition: Mapped[dict] = mapped_column(postgresql.JSONB, nullable=False)
    schema_template_operator_id: Mapped[Optional[int]] = mapped_column(
        sa.BigInteger, sa.ForeignKey("operator.id"), nullable=True
    )
    created_at: Mapped[Optional[sa.DateTime]] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=text("now()"),
        nullable=True,
    )
    updated_at: Mapped[Optional[sa.DateTime]] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=text("now()"),
        nullable=True,
    )


# ── 7. dataset ────────────────────────────────────────────────────────────────

class Dataset(Base):
    __tablename__ = "dataset"

    __table_args__ = (
        sa.UniqueConstraint(
            "recipe_id", "version_tag", name="uq_dataset_recipe_version"
        ),
        sa.Index("idx_dataset_recipe", "recipe_id"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    recipe_id: Mapped[Optional[int]] = mapped_column(
        sa.BigInteger, sa.ForeignKey("recipe.id"), nullable=True
    )
    # recipe_snapshot is JSONB NOT NULL — every INSERT must supply a value; no default
    recipe_snapshot: Mapped[dict] = mapped_column(postgresql.JSONB, nullable=False)
    version_tag: Mapped[str] = mapped_column(sa.Text, nullable=False)
    hf_repo_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    dataset_card_md: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    sample_count: Mapped[Optional[int]] = mapped_column(sa.BigInteger, nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(sa.BigInteger, nullable=True)
    stats: Mapped[Optional[dict]] = mapped_column(postgresql.JSONB, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    materialized_by: Mapped[Optional[int]] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id"), nullable=True
    )
    # materialized_at on dataset has NO default per §4.1 line 345
    materialized_at: Mapped[Optional[sa.DateTime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    dagster_run_id: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)


# ── 8. run ────────────────────────────────────────────────────────────────────

class Run(Base):
    __tablename__ = "run"

    __table_args__ = (
        sa.Index("idx_run_status", "status", sa.text("started_at DESC")),
        sa.Index("idx_run_triggered", "triggered_by", sa.text("started_at DESC")),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    dagster_run_id: Mapped[str] = mapped_column(
        sa.Text, nullable=False, unique=True
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    asset_keys: Mapped[List[str]] = mapped_column(
        postgresql.ARRAY(sa.Text), nullable=False
    )
    # partition_keys has no NOT NULL per §4.1 — nullable=True, default '{}'
    partition_keys: Mapped[Optional[List[str]]] = mapped_column(
        postgresql.ARRAY(sa.Text),
        server_default=text("'{}'"),
        nullable=True,
    )
    source_collection_id: Mapped[Optional[int]] = mapped_column(
        sa.BigInteger, sa.ForeignKey("source_collection.id"), nullable=True
    )
    dataset_id: Mapped[Optional[int]] = mapped_column(
        sa.BigInteger, sa.ForeignKey("dataset.id"), nullable=True
    )
    recipe_id: Mapped[Optional[int]] = mapped_column(
        sa.BigInteger, sa.ForeignKey("recipe.id"), nullable=True
    )
    config: Mapped[Optional[dict]] = mapped_column(postgresql.JSONB, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    started_at: Mapped[Optional[sa.DateTime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[Optional[sa.DateTime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    triggered_by: Mapped[Optional[int]] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id"), nullable=True
    )
    trigger_context: Mapped[Optional[dict]] = mapped_column(
        postgresql.JSONB, nullable=True
    )
