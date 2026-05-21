from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    """Health check endpoint. No auth required. No DB query."""
    return {"status": "ok"}
