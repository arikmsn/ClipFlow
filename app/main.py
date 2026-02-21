from fastapi import FastAPI

from app.api.jobs import router as jobs_router
from app.config import settings

app = FastAPI(title=settings.app_name)
app.include_router(jobs_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}
