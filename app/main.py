from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.jobs import router as jobs_router
from app.config import settings

app = FastAPI(title=settings.app_name)
app.include_router(jobs_router)

try:
    templates = Jinja2Templates(directory="app/templates")
except AssertionError:  # pragma: no cover
    templates = None


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    if templates is None:
        return HTMLResponse("<html><body><h1>ClipFlow Dashboard</h1></body></html>")
    return templates.TemplateResponse(request, "index.html", {"app_name": settings.app_name})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}
