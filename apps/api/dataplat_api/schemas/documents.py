"""Document schemas — S022-F-022 / document variant rendering.

Schemas:
  - DocumentRenderResponse: Not used (we return plain markdown with special Content-Type)
    The endpoint returns a FastAPI Response with media_type="text/markdown".
"""

from __future__ import annotations

__all__: list[str] = []
