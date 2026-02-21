from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_db

router = APIRouter(prefix="/jobs", tags=["jobs"])


class ClipCandidate(BaseModel):
    clip_start: float
    clip_end: float
    headline: str
    viral_score: float | None = None
    hook_score: float | None = None
    keyword_triggers_matched: list[str] = Field(default_factory=list)


class JobSummary(BaseModel):
    job_id: str
    status: str
    viral_score: float | None = None
    channel_name: str
    candidate_count: int
    top_headline: str | None = None


class JobListResponse(BaseModel):
    jobs: list[JobSummary]


class JobDetailResponse(BaseModel):
    job_id: str
    source_url: str
    channel_id: str
    channel_name: str
    status: str
    viral_score: float | None = None
    clip_manifest: list[ClipCandidate]
    render_path: str | None = None
    published_urls: list[dict[str, str]] = Field(default_factory=list)
    error_log: str | None = None
    retry_count: int
    created_at: datetime
    updated_at: datetime


class JobActionResponse(BaseModel):
    job_id: str
    status: str


class RejectRequest(BaseModel):
    reason: str | None = None


class HeadlinePatchRequest(BaseModel):
    headline: str


class HeadlinePatchResponse(BaseModel):
    job_id: str
    headline: str


@router.get("", response_model=JobListResponse)
def list_jobs(
    status: str = Query("approval"),
    limit: int = Query(20, ge=1, le=100),
    db: Any = Depends(get_db),
) -> JobListResponse:
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT j.job_id, j.status, j.viral_score, j.clip_manifest, c.name
            FROM jobs j
            JOIN channels c ON c.id = j.channel_id
            WHERE j.status = %s
            ORDER BY j.updated_at ASC
            LIMIT %s
            """,
            (status, limit),
        )
        rows = cursor.fetchall()

    jobs: list[JobSummary] = []
    for row in rows:
        manifest = _normalize_manifest(row[3])
        jobs.append(
            JobSummary(
                job_id=str(row[0]),
                status=str(row[1]),
                viral_score=row[2],
                channel_name=str(row[4]),
                candidate_count=len(manifest),
                top_headline=manifest[0].get("headline") if manifest else None,
            )
        )
    return JobListResponse(jobs=jobs)


@router.get("/{job_id}", response_model=JobDetailResponse)
def get_job_detail(job_id: str, db: Any = Depends(get_db)) -> JobDetailResponse:
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT j.job_id, j.source_url, j.channel_id, c.name, j.status, j.viral_score,
                   j.clip_manifest, j.render_path, j.published_urls, j.error_log,
                   j.retry_count, j.created_at, j.updated_at
            FROM jobs j
            JOIN channels c ON c.id = j.channel_id
            WHERE j.job_id = %s
            """,
            (job_id,),
        )
        row = cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    manifest = [ClipCandidate(**candidate) for candidate in _normalize_manifest(row[6])]
    published_urls = row[8] if isinstance(row[8], list) else []

    return JobDetailResponse(
        job_id=str(row[0]),
        source_url=str(row[1]),
        channel_id=str(row[2]),
        channel_name=str(row[3]),
        status=str(row[4]),
        viral_score=row[5],
        clip_manifest=manifest,
        render_path=row[7],
        published_urls=published_urls,
        error_log=row[9],
        retry_count=int(row[10]),
        created_at=row[11],
        updated_at=row[12],
    )


@router.post("/{job_id}/approve", response_model=JobActionResponse)
def approve_job(job_id: str, db: Any = Depends(get_db)) -> JobActionResponse:
    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE jobs
            SET status = 'rendering', error_log = NULL, updated_at = NOW()
            WHERE job_id = %s
            RETURNING job_id, status
            """,
            (job_id,),
        )
        row = cursor.fetchone()
    db.commit()

    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobActionResponse(job_id=str(row[0]), status=str(row[1]))


@router.post("/{job_id}/reject", response_model=JobActionResponse)
def reject_job(job_id: str, payload: RejectRequest, db: Any = Depends(get_db)) -> JobActionResponse:
    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE jobs
            SET status = 'rejected', error_log = %s, updated_at = NOW()
            WHERE job_id = %s
            RETURNING job_id, status
            """,
            (payload.reason, job_id),
        )
        row = cursor.fetchone()
    db.commit()

    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobActionResponse(job_id=str(row[0]), status=str(row[1]))


@router.patch("/{job_id}/headline", response_model=HeadlinePatchResponse)
def patch_headline(job_id: str, payload: HeadlinePatchRequest, db: Any = Depends(get_db)) -> HeadlinePatchResponse:
    with db.cursor() as cursor:
        cursor.execute("SELECT clip_manifest FROM jobs WHERE job_id = %s", (job_id,))
        row = cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    manifest = _normalize_manifest(row[0])
    if not manifest:
        raise HTTPException(status_code=400, detail="clip_manifest is empty")

    manifest[0]["headline"] = payload.headline

    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE jobs
            SET clip_manifest = %s, updated_at = NOW()
            WHERE job_id = %s
            """,
            (manifest, job_id),
        )
    db.commit()

    return HeadlinePatchResponse(job_id=job_id, headline=payload.headline)


def _normalize_manifest(raw_manifest: Any) -> list[dict[str, Any]]:
    if isinstance(raw_manifest, list):
        return [item for item in raw_manifest if isinstance(item, dict)]
    return []
