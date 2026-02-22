from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_db
from app.db import get_db_connection
from app.services.watcher import WatcherService

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobListItem(BaseModel):
    job_id: str
    source_url: str
    title: str
    status: str
    created_at: datetime
    channel_name: str


class JobListResponse(BaseModel):
    jobs: list[JobListItem]


class SyncChannelRequest(BaseModel):
    channel_id: str


class SyncChannelResponse(BaseModel):
    channel_id: str
    created_jobs: int


@router.get("", response_model=JobListResponse)
def list_jobs(db: Any = Depends(get_db)) -> JobListResponse:
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT j.job_id, j.source_url, j.status, j.created_at, j.clip_manifest, c.name
            FROM jobs j
            JOIN channels c ON c.id = j.channel_id
            ORDER BY j.created_at DESC
            """
        )
        rows = cursor.fetchall()

    jobs: list[JobListItem] = []
    for row in rows:
        clip_manifest = row[4] if isinstance(row[4], list) else []
        top_title = ""
        if clip_manifest and isinstance(clip_manifest[0], dict):
            top_title = str(clip_manifest[0].get("headline", ""))

        jobs.append(
            JobListItem(
                job_id=str(row[0]),
                source_url=str(row[1]),
                title=top_title,
                status=str(row[2]),
                created_at=row[3],
                channel_name=str(row[5]),
            )
        )

    return JobListResponse(jobs=jobs)


@router.post("/sync", response_model=SyncChannelResponse)
def sync_channel(payload: SyncChannelRequest) -> SyncChannelResponse:
    watcher = WatcherService(
        connection_factory=get_db_connection,
        youtube_api_key="",
    )
    created_jobs = watcher.run_once(channel_id=payload.channel_id)
    return SyncChannelResponse(channel_id=payload.channel_id, created_jobs=created_jobs)
