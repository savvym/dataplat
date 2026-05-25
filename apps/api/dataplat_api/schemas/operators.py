"""Operator schemas — S016-F-016 / S017-F-017.

Schemas:
  - OperatorRead: response item for GET /api/operators (F-016).
      Lean projection: 10 fields covering all verification-required columns plus
      the most operationally useful ones. Large JSONB blobs omitted so the list
      response stays compact.
  - OperatorDetail: response for GET /api/operators/{operator_id} (F-017).
      Full record: all 19 ORM columns exposed. Intended for single-row detail
      fetches where the complete payload is expected and useful.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OperatorRead(BaseModel):
    """Response schema for a single operator row.

    Used by GET /api/operators (F-016).

    Fields included per agreed.md §3.4:
      - id, name, version, category — required by F-016 V2.
      - config_schema — required by F-016 V2; nullable (Optional[dict] in ORM).
      - input_kind, output_kind — useful to callers composing pipelines.
      - image — operators are identified by their container image.
      - description — human-readable label for UI display; nullable.
      - is_active — lets clients reason about operator availability; nullable bool.

    Fields intentionally omitted (internal/operational details deferred to later
    sprints): output_schema, default_config, reference_url, example_input,
    example_output, entrypoint, estimated_cost_per_unit, rate_limit_per_minute,
    created_at.
    """

    id: int
    name: str
    version: str
    category: str
    input_kind: str
    output_kind: str
    image: str
    config_schema: dict | None
    description: str | None
    is_active: bool | None

    model_config = ConfigDict(from_attributes=True)


class OperatorDetail(BaseModel):
    """Full operator record for GET /api/operators/{operator_id} (F-017).

    Exposes all 19 ORM columns. Nullability matches Mapped[Optional[...]] exactly:
      NOT NULL: id, name, version, category, input_kind, output_kind, image.
      nullable:  output_schema, config_schema, default_config, description,
                 reference_url, example_input, example_output, entrypoint,
                 estimated_cost_per_unit, rate_limit_per_minute, is_active, created_at.

    default_config has server_default '{}'::jsonb — a fresh SELECT always returns
    a dict (empty is valid). output_schema is NULL for the MinerU seed row (seed
    never sets it); the key is present in the response but the value is None.
    """

    id: int
    name: str
    version: str
    category: str
    input_kind: str
    output_kind: str
    image: str
    output_schema: dict | None
    config_schema: dict | None
    default_config: dict | None
    description: str | None
    reference_url: str | None
    example_input: dict | None
    example_output: dict | None
    entrypoint: str | None
    estimated_cost_per_unit: dict | None
    rate_limit_per_minute: int | None
    is_active: bool | None
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
