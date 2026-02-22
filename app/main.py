import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.api.jobs import router as jobs_router
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="ClipFlow AI")

# בדיקה איפה הקוד חושב שהתיקייה נמצאת
base_dir = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.join(base_dir, "templates")

print(f"--- DEBUG: Looking for templates in: {template_path}")
if not os.path.exists(template_path):
    print("--- ERROR: Templates directory NOT FOUND!")

templates = Jinja2Templates(directory=template_path)

app.include_router(jobs_router)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    print("--- DEBUG: Root route accessed, trying to render index.html")
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "app_name": "ClipFlow AI"
    })

@app.get("/health")
def health_check():
    return {"status": "ok"}