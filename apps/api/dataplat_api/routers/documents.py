"""Documents router — S022-F-022 GET /api/documents/{variant_id}/render.

Provides:
  GET /api/documents/{variant_id}/render — markdown representation of DoclingDocument (F-022).

Auth enforcement (Depends(get_current_user)) is the F-008 deliverable and
MUST NOT be removed from this handler.

Ownership-scoping (F-022 agreed.md §4.1):
  A document variant is accessible if its source either:
    (a) belongs to a collection owned by the caller, or
    (b) has no collection (collection_id IS NULL).
  Return 404 for both "not found" and "not accessible" to prevent enumeration.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.config import settings
from dataplat_api.db.models import DocumentVariant, Source, SourceCollection, User
from dataplat_api.db.session import get_session
from dataplat_api.storage.s3 import get_s3_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _render_docling_to_markdown(doc_dict: dict[str, Any]) -> str:
    """Linearize a DoclingDocument JSON dict to markdown text.

    Simple MVP implementation: extract all text nodes and join them with newlines.
    Production version could preserve more structure (headings, tables, lists).

    The DoclingDocument structure (from docling-core):
      {
        "name": "...",
        "pages": {
          "1": { "page_no": 1, "size": {...}, "children": [...]},
          ...
        },
        "children": [...],  # document-level content nodes
      }

    Each node can be:
      - TextElement: has "text" field
      - TableElement: has table structure
      - SectionHeader: has "title"
      - ImageElement: reference to image
      etc.

    For MVP, we do a recursive walk and extract all text.
    """
    lines: list[str] = []

    # Document title
    if "name" in doc_dict:
        lines.append(f"# {doc_dict['name']}")
        lines.append("")

    def walk_node(node: dict[str, Any], depth: int = 0) -> None:
        """Recursively walk node tree and extract text."""
        if not isinstance(node, dict):
            return

        node_type = node.get("type", "unknown")

        # Text node
        if node_type == "text" and "text" in node:
            lines.append(node["text"])

        # Section header
        elif node_type == "section_header" and "title" in node:
            level = min(6, depth + 2)  # Cap at h6
            lines.append(f"{'#' * level} {node['title']}")
            lines.append("")

        # Table
        elif node_type == "table":
            if "data" in node:
                lines.append("| " + " | ".join(node["data"].get("rows", [[]])[0]) + " |")
                lines.append("|" + "|".join(["---|"] * len(node["data"].get("rows", [[]])[0])))
                for row in node["data"].get("rows", [])[1:]:
                    lines.append("| " + " | ".join(row) + " |")
                lines.append("")

        # Image reference (placeholder)
        elif node_type == "image":
            image_id = node.get("id", "img")
            lines.append(f"[Image: {image_id}]")

        # Code block
        elif node_type == "code" and "text" in node:
            lang = node.get("language", "")
            lines.append(f"```{lang}")
            lines.append(node["text"])
            lines.append("```")
            lines.append("")

        # List
        elif node_type == "list":
            for item in node.get("items", []):
                if isinstance(item, dict) and "text" in item:
                    lines.append(f"- {item['text']}")
            lines.append("")

        # Recursively process children
        for child in node.get("children", []):
            walk_node(child, depth + 1)

    # Walk document-level children
    if "children" in doc_dict:
        for child in doc_dict["children"]:
            walk_node(child)

    # Walk all pages
    for page in doc_dict.get("pages", {}).values():
        if isinstance(page, dict):
            for child in page.get("children", []):
                walk_node(child)

    # Clean up empty lines at the end
    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines) + "\n"


@router.get("/{variant_id}/render", response_class=Response)
async def render_document_variant(
    variant_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    s3: Any = Depends(get_s3_client),
) -> Response:
    """Render a document variant as markdown.

    Returns the canonical DoclingDocument for the given variant as formatted markdown,
    suitable for display in a browser preview pane.

    Ownership-scoping (same as F-020):
      Step 1 — Verify the variant exists AND its source is accessible to the caller.
               If it does not exist or belongs to another user's collection → 404.
      Step 2 — Fetch the DoclingDocument JSON from MinIO.
      Step 3 — Render to markdown and return with Content-Type: text/markdown.

    Returns 200 (markdown text), 404 (variant not found or not accessible), 401 (no auth).
    """
    # Step 1: variant ownership check (LEFT JOIN + OR logic, same as F-020/F-021).
    result = await session.execute(
        select(DocumentVariant)
        .join(Source)
        .join(SourceCollection, Source.collection_id == SourceCollection.id, isouter=True)
        .where(DocumentVariant.id == variant_id)
        .where(
            or_(
                SourceCollection.owner_id == current_user.id,
                Source.collection_id.is_(None),
            )
        )
    )
    variant = result.scalar_one_or_none()
    if variant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document variant not found",
        )

    # Step 2: fetch DoclingDocument JSON from MinIO.
    # storage_prefix is like "s3://documents/7/extract_mineru/"
    # Remove "s3://documents/" prefix to get the S3 key.
    if variant.storage_prefix.startswith("s3://documents/"):
        s3_path_prefix = variant.storage_prefix[len("s3://documents/") :]
    else:
        # Fallback for unusual prefixes
        s3_path_prefix = variant.storage_prefix.replace("s3://", "").replace("documents/", "", 1)

    s3_key = f"documents/{s3_path_prefix}doc.docling.json"

    try:
        response = await s3.get_object(
            Bucket=settings.MINIO_DOCUMENTS_BUCKET,
            Key=s3_key,
        )
        doc_bytes = await response["Body"].read()
        doc_dict = json.loads(doc_bytes)
    except Exception as exc:
        logger.warning(
            "F-022: Failed to fetch DoclingDocument for variant %d from s3://%s/%s: %s",
            variant_id,
            settings.MINIO_DOCUMENTS_BUCKET,
            s3_key,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve document; it may be incomplete or corrupt",
        ) from exc

    # Step 3: render to markdown.
    markdown_text = _render_docling_to_markdown(doc_dict)

    # Return markdown with correct Content-Type.
    return Response(content=markdown_text, media_type="text/markdown")
