import os
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import List, Dict, Any
from app.db import get_db_connection
from app.services.watcher import WatcherService
import logging

# הגדרת ראוטר
router = APIRouter(prefix="/api/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)

@router.get("/")
async def get_jobs():
    """שליפת כל הג'ובים מהמסד"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT job_id, source_url, status, created_at, render_path 
                    FROM jobs 
                    ORDER BY created_at DESC
                """)
                rows = cur.fetchall()
                
                return {"jobs": [{
                    "job_id": str(row[0]),
                    "source_url": row[1],
                    "status": row[2],
                    "created_at": row[3].isoformat() if row[3] else None,
                    "render_path": row[4],
                    "title": "YouTube Video",
                    "channel_name": "Global Citizen"
                } for row in rows]}
    except Exception as e:
        logger.error(f"Error in get_jobs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sync")
async def sync_jobs(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    """הפעלת ה-Watcher בצורה הנכונה"""
    channel_id = payload.get("channel_id")
    api_key = os.getenv("YOUTUBE_API_KEY")
    
    if not api_key:
        raise HTTPException(status_code=500, detail="YouTube API Key missing in .env")

    print(f"--- DEBUG: Sync requested for channel: {channel_id}")

    try:
        # יצירת ה-Watcher עם הפרמטרים שהוא מצפה להם
        # אנחנו מעבירים לו את get_db_connection כ-factory
        watcher = WatcherService(
            connection_factory=get_db_connection,
            youtube_api_key=api_key
        )
        
        # הפעלה של run_once (השם האמיתי של הפונקציה ב-Watcher שלך)
        background_tasks.add_task(watcher.run_once, channel_id)
        
        return {
            "status": "success", 
            "message": f"סנכרון הופעל עבור {channel_id}"
        }
    except Exception as e:
        print(f"--- ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))