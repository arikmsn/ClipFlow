from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class ApprovalJob:
    job_id: str
    source_path: str
    clip_manifest: list[dict[str, Any]]
    retry_count: int
    render_path: str | None


@dataclass(frozen=True)
class CropCoordinates:
    x: int
    y: int
    width: int
    height: int


class CursorProtocol(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None: ...

    def fetchone(self) -> Any: ...

    def __enter__(self) -> "CursorProtocol": ...

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...


class ConnectionProtocol(Protocol):
    def cursor(self) -> CursorProtocol: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def __enter__(self) -> "ConnectionProtocol": ...

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...


class FaceTrackerProtocol(Protocol):
    def track(self, source_path: str, clip_start: float, clip_end: float) -> CropCoordinates: ...


class FfmpegRunnerProtocol(Protocol):
    def run(
        self,
        source_path: str,
        clip_start: float,
        clip_end: float,
        crop_coords: CropCoordinates,
        output_dir: str,
    ) -> Path: ...


class S3UploaderProtocol(Protocol):
    def upload(self, local_path: Path, bucket: str, key: str) -> str: ...


class RendererService:
    def __init__(
        self,
        connection_factory: Callable[[], ConnectionProtocol],
        tracker: FaceTrackerProtocol,
        ffmpeg_runner: FfmpegRunnerProtocol,
        s3_uploader: S3UploaderProtocol,
        s3_bucket: str,
        s3_prefix: str = "renders",
    ) -> None:
        self._connection_factory = connection_factory
        self._tracker = tracker
        self._ffmpeg_runner = ffmpeg_runner
        self._s3_uploader = s3_uploader
        self._s3_bucket = s3_bucket
        self._s3_prefix = s3_prefix.strip("/")

    def run_once(self) -> bool:
        with self._connection_factory() as connection:
            self._recover_stuck_approval_jobs(connection)
            job = self._claim_approval_job(connection)
            connection.commit()

        if job is None:
            return False

        if job.render_path:
            self._mark_scheduled(job.job_id, job.render_path)
            return True

        candidate = _select_primary_candidate(job.clip_manifest)
        if candidate is None:
            self._handle_failure(job, "clip_manifest is empty")
            return False

        try:
            clip_start = float(candidate["clip_start"])
            clip_end = float(candidate["clip_end"])
            crop_coords = self._tracker.track(job.source_path, clip_start, clip_end)
            with TemporaryDirectory() as temp_dir:
                render_file = self._ffmpeg_runner.run(
                    source_path=job.source_path,
                    clip_start=clip_start,
                    clip_end=clip_end,
                    crop_coords=crop_coords,
                    output_dir=temp_dir,
                )
                key = f"{self._s3_prefix}/{job.job_id}/{render_file.name}"
                render_path = self._s3_uploader.upload(render_file, self._s3_bucket, key)
        except Exception as exc:  # noqa: BLE001
            self._handle_failure(job, str(exc))
            return False

        self._mark_scheduled(job.job_id, render_path)
        return True

    def _recover_stuck_approval_jobs(self, connection: ConnectionProtocol) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET status = CASE WHEN retry_count + 1 >= 3 THEN 'failed' ELSE 'approval' END,
                    retry_count = retry_count + 1,
                    error_log = 'Renderer timed out after 30 minutes',
                    updated_at = NOW()
                WHERE status = 'approval'
                  AND updated_at < NOW() - INTERVAL '30 minutes'
                """
            )

    def _claim_approval_job(self, connection: ConnectionProtocol) -> ApprovalJob | None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT job_id, source_path, clip_manifest, retry_count, render_path
                FROM jobs
                WHERE status = 'approval'
                  AND clip_manifest IS NOT NULL
                ORDER BY updated_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return ApprovalJob(
            job_id=str(row[0]),
            source_path=str(row[1] or ""),
            clip_manifest=list(row[2] or []),
            retry_count=int(row[3]),
            render_path=row[4],
        )

    def _mark_scheduled(self, job_id: str, render_path: str) -> None:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = 'scheduled',
                        render_path = %s,
                        error_log = NULL,
                        updated_at = NOW()
                    WHERE job_id = %s
                    """,
                    (render_path, job_id),
                )
            connection.commit()

    def _handle_failure(self, job: ApprovalJob, error_message: str) -> None:
        next_retry = job.retry_count + 1
        next_status = "failed" if next_retry >= 3 else "approval"
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = %s,
                        retry_count = %s,
                        error_log = %s,
                        updated_at = NOW()
                    WHERE job_id = %s
                    """,
                    (next_status, next_retry, error_message, job.job_id),
                )
            connection.commit()


def _select_primary_candidate(clip_manifest: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not clip_manifest:
        return None
    return clip_manifest[0]
