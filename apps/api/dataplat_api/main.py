from fastapi import FastAPI

from dataplat_api.routers.health import router as health_router

app = FastAPI(title="Dataplat API", version="0.1.0")

app.include_router(health_router)
