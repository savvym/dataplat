"""Recipe schemas — S037-F-037.

Schemas:
  - RecipeName: validated name type (strip whitespace, 1–255 chars).
  - RecipeCreate: request body for POST /api/recipes (F-037).
  - RecipeOut: response for POST /api/recipes (F-037).

``definition`` JSONB schema policy:
  Passthrough validation at the API boundary — any JSON object is accepted
  without structural inspection.  The field is typed as ``dict[str, Any]``
  so Pydantic rejects non-object JSON (bare string, array, null) with 422,
  but accepts any well-formed JSON object.

  Rationale: The design doc (§2.5, §4.2) describes ``definition`` as a
  synthesis blueprint whose internal shape is determined by the
  processor/operator pipeline configured at synthesis time.  Enforcing a
  fixed schema here would either over-constrain early users or require a
  versioned validation registry — both deferred per MVP boundary rules.
  Synthesis-time validation (e.g. against the operator's ``config_schema``
  JSON Schema) is the correct enforcement point and is explicitly out-of-scope
  for this sprint (targeted at F-082).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

# Validated name type: strip whitespace, require 1–255 chars after stripping.
# Using Annotated + StringConstraints because Field() does not accept
# strip_whitespace in pydantic v2.
RecipeName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]


class RecipeCreate(BaseModel):
    """Request body for POST /api/recipes."""

    model_config = ConfigDict(extra="ignore")

    name: RecipeName
    # No size/depth guard at the API boundary — intentionally deferred to synthesis-time
    # validation (F-082). A Starlette body-size limit can be enforced at the uvicorn/nginx
    # layer if pathological payloads become a concern.
    definition: dict[str, Any]
    description: str | None = None


class RecipeOut(BaseModel):
    """Response schema for a single recipe row.

    Used by POST /api/recipes (F-037).  Returns the full record (id, name,
    description, owner_id, definition, created_at, updated_at) so that the
    client has all fields in one round trip — consistent with SourceCollectionOut.

    ``schema_template_operator_id`` is omitted for MVP; it will be added as an
    optional field in a later sprint when operator-linking semantics are defined.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    owner_id: int | None
    definition: dict[str, Any]
    created_at: datetime | None
    updated_at: datetime | None
