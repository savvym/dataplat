"""Operator schemas — S016-F-016.

Schemas:
  - OperatorRead: response item for GET /api/operators (F-016).
      Covers the five verification-required fields (id, name, version, category,
      config_schema) plus additional columns worth exposing to API callers
      (input_kind, output_kind, image, description, is_active).
"""

from __future__ import annotations

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
