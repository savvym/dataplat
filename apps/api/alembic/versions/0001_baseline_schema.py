"""Baseline schema — all 8 business tables per docs/data_platform_design.md §4.1.

Revision ID: 0001
Revises:
Create Date: 2026-05-22

Tables created (in dependency order):
  users → source_collection → source → document_variant
  operator → recipe → dataset → run

Downgrade drops in reverse FK dependency order to avoid constraint violations.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. users ─────────────────────────────────────────────────────────────
    # §4.1: id BIGSERIAL PK, email TEXT UNIQUE NOT NULL, name TEXT,
    #       created_at TIMESTAMPTZ DEFAULT NOW()
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )

    # ── 2. source_collection ─────────────────────────────────────────────────
    # §4.1: id BIGSERIAL PK, name TEXT UNIQUE NOT NULL,
    #       owner_id BIGINT REFERENCES users(id),
    #       dataset_card_md TEXT,
    #       created_at TIMESTAMPTZ DEFAULT NOW(),
    #       updated_at TIMESTAMPTZ DEFAULT NOW()
    op.create_table(
        "source_collection",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column(
            "owner_id",
            sa.BigInteger,
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("dataset_card_md", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )

    # ── 3. source ────────────────────────────────────────────────────────────
    # §4.1: id BIGSERIAL PK,
    #       collection_id BIGINT REFERENCES source_collection(id) ON DELETE CASCADE,
    #       kind TEXT NOT NULL, original_name TEXT NOT NULL,
    #       storage_uri TEXT NOT NULL, sha256 TEXT NOT NULL,
    #       size BIGINT, mime_type TEXT, license TEXT,
    #       source_metadata JSONB DEFAULT '{}',
    #       dagster_partition_key TEXT NOT NULL UNIQUE,
    #       preferred_extractor TEXT,
    #       uploaded_at TIMESTAMPTZ DEFAULT NOW()
    op.create_table(
        "source",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "collection_id",
            sa.BigInteger,
            sa.ForeignKey("source_collection.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("original_name", sa.Text, nullable=False),
        sa.Column("storage_uri", sa.Text, nullable=False),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("size", sa.BigInteger, nullable=True),
        sa.Column("mime_type", sa.Text, nullable=True),
        sa.Column("license", sa.Text, nullable=True),
        sa.Column(
            "source_metadata",
            postgresql.JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
        sa.Column("dagster_partition_key", sa.Text, nullable=False, unique=True),
        sa.Column("preferred_extractor", sa.Text, nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.create_index("idx_source_collection", "source", ["collection_id"])
    op.create_index("idx_source_sha256", "source", ["sha256"])

    # ── 4. document_variant ──────────────────────────────────────────────────
    # §4.1: id BIGSERIAL PK,
    #       source_id BIGINT REFERENCES source(id) ON DELETE CASCADE,
    #       extractor_name TEXT NOT NULL, extractor_version TEXT NOT NULL,
    #       config_hash TEXT NOT NULL,
    #       storage_prefix TEXT NOT NULL,
    #       page_count INT, image_count INT,
    #       is_canonical BOOLEAN DEFAULT FALSE,
    #       materialized_at TIMESTAMPTZ DEFAULT NOW(),   ← has DEFAULT (finding 1)
    #       dagster_run_id TEXT,
    #       UNIQUE (source_id, extractor_name, config_hash)
    op.create_table(
        "document_variant",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "source_id",
            sa.BigInteger,
            sa.ForeignKey("source.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("extractor_name", sa.Text, nullable=False),
        sa.Column("extractor_version", sa.Text, nullable=False),
        sa.Column("config_hash", sa.Text, nullable=False),
        sa.Column("storage_prefix", sa.Text, nullable=False),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("image_count", sa.Integer, nullable=True),
        sa.Column(
            "is_canonical",
            sa.Boolean,
            server_default=sa.text("false"),
            nullable=True,
        ),
        sa.Column(
            "materialized_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("dagster_run_id", sa.Text, nullable=True),
        sa.UniqueConstraint(
            "source_id", "extractor_name", "config_hash",
            name="uq_document_variant_source_extractor_config",
        ),
    )
    op.create_index("idx_doc_variant_source", "document_variant", ["source_id"])
    op.create_index(
        "idx_doc_canonical",
        "document_variant",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("is_canonical"),
    )

    # ── 5. operator ──────────────────────────────────────────────────────────
    # §4.1: 19 columns total (id + 18)
    # id, name, version, category, input_kind, output_kind,
    # output_schema (nullable JSONB), config_schema (nullable JSONB),
    # default_config JSONB DEFAULT '{}',
    # description, reference_url,
    # example_input (nullable JSONB), example_output (nullable JSONB),
    # image TEXT NOT NULL, entrypoint,
    # estimated_cost_per_unit (nullable JSONB),
    # rate_limit_per_minute INT,
    # is_active BOOLEAN DEFAULT TRUE,
    # created_at TIMESTAMPTZ DEFAULT NOW()
    # UNIQUE (name, version)
    op.create_table(
        "operator",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("input_kind", sa.Text, nullable=False),
        sa.Column("output_kind", sa.Text, nullable=False),
        sa.Column("output_schema", postgresql.JSONB, nullable=True),
        sa.Column("config_schema", postgresql.JSONB, nullable=True),
        sa.Column(
            "default_config",
            postgresql.JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("reference_url", sa.Text, nullable=True),
        sa.Column("example_input", postgresql.JSONB, nullable=True),
        sa.Column("example_output", postgresql.JSONB, nullable=True),
        sa.Column("image", sa.Text, nullable=False),
        sa.Column("entrypoint", sa.Text, nullable=True),
        sa.Column("estimated_cost_per_unit", postgresql.JSONB, nullable=True),
        sa.Column("rate_limit_per_minute", sa.Integer, nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean,
            server_default=sa.text("true"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.UniqueConstraint("name", "version", name="uq_operator_name_version"),
    )
    op.create_index(
        "idx_operator_category",
        "operator",
        ["category", "is_active"],
    )

    # ── 6. recipe ────────────────────────────────────────────────────────────
    # §4.1: id BIGSERIAL PK,
    #       name TEXT UNIQUE NOT NULL,
    #       description TEXT,
    #       owner_id BIGINT REFERENCES users(id),
    #       definition JSONB NOT NULL,   ← NOT NULL, no default (reviewer note 4)
    #       schema_template_operator_id BIGINT REFERENCES operator(id),
    #       created_at TIMESTAMPTZ DEFAULT NOW(),
    #       updated_at TIMESTAMPTZ DEFAULT NOW()
    op.create_table(
        "recipe",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "owner_id",
            sa.BigInteger,
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("definition", postgresql.JSONB, nullable=False),
        sa.Column(
            "schema_template_operator_id",
            sa.BigInteger,
            sa.ForeignKey("operator.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )

    # ── 7. dataset ───────────────────────────────────────────────────────────
    # §4.1: id BIGSERIAL PK,
    #       recipe_id BIGINT REFERENCES recipe(id),
    #       recipe_snapshot JSONB NOT NULL,  ← NOT NULL, no default
    #       version_tag TEXT NOT NULL,
    #       hf_repo_uri TEXT NOT NULL,
    #       dataset_card_md TEXT,
    #       sample_count BIGINT, size_bytes BIGINT,
    #       stats JSONB,  ← nullable, no default
    #       status TEXT NOT NULL,
    #       materialized_by BIGINT REFERENCES users(id),
    #       materialized_at TIMESTAMPTZ,   ← nullable, no default
    #       dagster_run_id TEXT,
    #       UNIQUE (recipe_id, version_tag)
    op.create_table(
        "dataset",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "recipe_id",
            sa.BigInteger,
            sa.ForeignKey("recipe.id"),
            nullable=True,
        ),
        sa.Column("recipe_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("version_tag", sa.Text, nullable=False),
        sa.Column("hf_repo_uri", sa.Text, nullable=False),
        sa.Column("dataset_card_md", sa.Text, nullable=True),
        sa.Column("sample_count", sa.BigInteger, nullable=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("stats", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column(
            "materialized_by",
            sa.BigInteger,
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dagster_run_id", sa.Text, nullable=True),
        sa.UniqueConstraint(
            "recipe_id", "version_tag", name="uq_dataset_recipe_version",
        ),
    )
    op.create_index("idx_dataset_recipe", "dataset", ["recipe_id"])

    # ── 8. run ───────────────────────────────────────────────────────────────
    # §4.1: id BIGSERIAL PK,
    #       dagster_run_id TEXT UNIQUE NOT NULL,
    #       kind TEXT NOT NULL,
    #       asset_keys TEXT[] NOT NULL,
    #       partition_keys TEXT[] DEFAULT '{}',  ← no NOT NULL (finding 2)
    #       source_collection_id BIGINT REFERENCES source_collection(id),
    #       dataset_id BIGINT REFERENCES dataset(id),
    #       recipe_id BIGINT REFERENCES recipe(id),
    #       config JSONB,  ← nullable, no default
    #       status TEXT NOT NULL,
    #       started_at TIMESTAMPTZ,   ← nullable, no default
    #       ended_at TIMESTAMPTZ,     ← nullable, no default
    #       triggered_by BIGINT REFERENCES users(id),
    #       trigger_context JSONB    ← nullable, no default
    op.create_table(
        "run",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("dagster_run_id", sa.Text, nullable=False, unique=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("asset_keys", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column(
            "partition_keys",
            postgresql.ARRAY(sa.Text),
            server_default=sa.text("'{}'"),
            nullable=True,
        ),
        sa.Column(
            "source_collection_id",
            sa.BigInteger,
            sa.ForeignKey("source_collection.id"),
            nullable=True,
        ),
        sa.Column(
            "dataset_id",
            sa.BigInteger,
            sa.ForeignKey("dataset.id"),
            nullable=True,
        ),
        sa.Column(
            "recipe_id",
            sa.BigInteger,
            sa.ForeignKey("recipe.id"),
            nullable=True,
        ),
        sa.Column("config", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "triggered_by",
            sa.BigInteger,
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("trigger_context", postgresql.JSONB, nullable=True),
    )
    op.create_index(
        "idx_run_status",
        "run",
        ["status", sa.text("started_at DESC")],
    )
    op.create_index(
        "idx_run_triggered",
        "run",
        ["triggered_by", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    # Drop in reverse FK dependency order to avoid FK constraint violations.
    # run → dataset → recipe → operator → document_variant → source
    #   → source_collection → users
    # Indexes on dropped tables are removed automatically by Postgres.
    op.drop_table("run")
    op.drop_table("dataset")
    op.drop_table("recipe")
    op.drop_table("operator")
    op.drop_table("document_variant")
    op.drop_table("source")
    op.drop_table("source_collection")
    op.drop_table("users")
