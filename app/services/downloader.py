from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class PendingJob:
    job_id: str
    source_url: str
    retry_count: int
    source_path: str | None


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


class DownloaderService:
    def __init__(
        self,
        connection_factory: Callable[[], ConnectionProtocol],
        s3_bucket: str,
        proxy_url: str | None = None,
        s3_prefix: str = "sources",
        ytdlp_runner: Callable[[str, str, str | None], Path] | None = None,
        s3_uploader: Callable[[Path, str, str], str] | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._proxy_url = proxy_url
        self._s3_bucket = s3_bucket
        self._s3_prefix = s3_prefix.strip("/")
        self._ytdlp_runner = ytdlp_runner or self._download_with_yt_dlp
        self._s3_uploader = s3_uploader or self._upload_to_s3

    def run_once(self) -> bool:
        with self._connection_factory() as connection:
            self._recover_stuck_downloading_jobs(connection)
            job = self._claim_pending_job(connection)
            if job is None:
                connection.commit()
                return False

            if job.source_path:
                self._mark_analyzing(connection, job.job_id, job.source_path)
                connection.commit()
                return True

            self._mark_downloading(connection, job.job_id)
            connection.commit()

        try:
            with TemporaryDirectory() as tmp_dir:
                local_path = self._ytdlp_runner(job.source_url, tmp_dir, self._proxy_url)
                key = f"{self._s3_prefix}/{job.job_id}/{local_path.name}"
                source_path = self._s3_uploader(local_path, self._s3_bucket, key)
        except Exception as exc:  # noqa: BLE001
            self._handle_failure(job, str(exc))
            return False

        with self._connection_factory() as connection:
            self._mark_analyzing(connection, job.job_id, source_path)
            connection.commit()
        return True

    def _recover_stuck_downloading_jobs(self, connection: ConnectionProtocol) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET status = 'pending', updated_at = NOW()
                WHERE status = 'downloading'
                  AND updated_at < NOW() - INTERVAL '30 minutes'
                """
            )

    def _claim_pending_job(self, connection: ConnectionProtocol) -> PendingJob | None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT job_id, source_url, retry_count, source_path
                FROM jobs
                WHERE status = 'pending'
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return PendingJob(
            job_id=str(row[0]),
            source_url=str(row[1]),
            retry_count=int(row[2]),
            source_path=row[3],
        )

    def _mark_downloading(self, connection: ConnectionProtocol, job_id: str) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET status = 'downloading', updated_at = NOW()
                WHERE job_id = %s
                """,
                (job_id,),
            )

    def _mark_analyzing(self, connection: ConnectionProtocol, job_id: str, source_path: str) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET status = 'analyzing', source_path = %s, error_log = NULL, updated_at = NOW()
                WHERE job_id = %s
                """,
                (source_path, job_id),
            )

    def _handle_failure(self, job: PendingJob, error_message: str) -> None:
        next_retry = job.retry_count + 1
        next_status = "failed" if next_retry >= 3 else "pending"
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

    def _download_with_yt_dlp(self, source_url: str, output_dir: str, proxy_url: str | None) -> Path:
        output_template = str(Path(output_dir) / "video.%(ext)s")
        command = [
            "yt-dlp",
            "--no-playlist",
            "-o",
            output_template,
            source_url,
        ]
        if proxy_url:
            command.extend(["--proxy", proxy_url])

        subprocess.run(command, check=True, capture_output=True, text=True)

        output_path = Path(output_dir)
        files = sorted(path for path in output_path.iterdir() if path.is_file())
        if not files:
            raise RuntimeError("yt-dlp completed but no video file was created")
        return files[0]

    def _upload_to_s3(self, local_path: Path, bucket: str, key: str) -> str:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("boto3 is required for S3 uploads") from exc

        s3_client = boto3.client("s3")
        s3_client.upload_file(str(local_path), bucket, key)
        return f"s3://{bucket}/{key}"
