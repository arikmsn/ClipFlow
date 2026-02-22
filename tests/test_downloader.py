import unittest
from pathlib import Path

from app.services.downloader import DownloaderService


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self._result = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, query: str, params=None) -> None:
        query = " ".join(query.split())

        if query.startswith("UPDATE jobs SET status = 'pending', updated_at = NOW() WHERE status = 'downloading'"):
            for job in self.connection.jobs:
                if job["status"] == "downloading" and job.get("updated_minutes_ago", 0) > 30:
                    job["status"] = "pending"
            return

        if query.startswith("SELECT job_id, source_url, retry_count, source_path FROM jobs"):
            pending = [job for job in self.connection.jobs if job["status"] == "pending"]
            self._result = pending[0] if pending else None
            return

        if query.startswith("UPDATE jobs SET status = 'downloading'"):
            job = self.connection._job(params[0])
            job["status"] = "downloading"
            return

        if query.startswith("UPDATE jobs SET status = 'analyzing', source_path = %s"):
            source_path, job_id = params
            job = self.connection._job(job_id)
            job["status"] = "analyzing"
            job["source_path"] = source_path
            job["error_log"] = None
            return

        if query.startswith("UPDATE jobs SET status = %s,"):
            status, retry_count, error_log, job_id = params
            job = self.connection._job(job_id)
            job["status"] = status
            job["retry_count"] = retry_count
            job["error_log"] = error_log
            return

        raise AssertionError(f"Unexpected query: {query}")

    def fetchone(self):
        if self._result is None:
            return None
        return (
            self._result["job_id"],
            self._result["source_url"],
            self._result["retry_count"],
            self._result["source_path"],
        )


class FakeConnection:
    def __init__(self, jobs):
        self.jobs = jobs

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def _job(self, job_id):
        for job in self.jobs:
            if job["job_id"] == job_id:
                return job
        raise AssertionError(f"Unknown job id: {job_id}")


class DownloaderServiceTests(unittest.TestCase):
    def test_run_once_recovers_stuck_downloading_job(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "source_url": "https://youtube.com/watch?v=abc",
                "retry_count": 0,
                "status": "downloading",
                "source_path": None,
                "error_log": None,
                "updated_minutes_ago": 31,
            }
        ]
        connection = FakeConnection(jobs)

        def ytdlp_runner(source_url: str, output_dir: str, proxy_url: str | None) -> Path:
            file_path = Path(output_dir) / "video.mp4"
            file_path.write_text("dummy", encoding="utf-8")
            return file_path

        service = DownloaderService(
            connection_factory=lambda: connection,
            s3_bucket="clipflow-bucket",
            ytdlp_runner=ytdlp_runner,
            s3_uploader=lambda _path, bucket, key: f"s3://{bucket}/{key}",
        )

        result = service.run_once()

        self.assertTrue(result)
        self.assertEqual(jobs[0]["status"], "analyzing")
        self.assertTrue(str(jobs[0]["source_path"]).startswith("s3://clipflow-bucket/sources/job-1/"))

    def test_run_once_downloads_uploads_and_marks_analyzing(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "source_url": "https://youtube.com/watch?v=abc",
                "retry_count": 0,
                "status": "pending",
                "source_path": None,
                "error_log": None,
            }
        ]
        connection = FakeConnection(jobs)

        def ytdlp_runner(source_url: str, output_dir: str, proxy_url: str | None) -> Path:
            self.assertEqual(source_url, "https://youtube.com/watch?v=abc")
            self.assertEqual(proxy_url, "http://proxy.local:8080")
            file_path = Path(output_dir) / "video.mp4"
            file_path.write_text("dummy", encoding="utf-8")
            return file_path

        def s3_uploader(local_path: Path, bucket: str, key: str) -> str:
            self.assertEqual(local_path.name, "video.mp4")
            self.assertEqual(bucket, "clipflow-bucket")
            self.assertIn("job-1", key)
            return f"s3://{bucket}/{key}"

        service = DownloaderService(
            connection_factory=lambda: connection,
            s3_bucket="clipflow-bucket",
            proxy_url="http://proxy.local:8080",
            ytdlp_runner=ytdlp_runner,
            s3_uploader=s3_uploader,
        )

        result = service.run_once()

        self.assertTrue(result)
        self.assertEqual(jobs[0]["status"], "analyzing")
        self.assertTrue(str(jobs[0]["source_path"]).startswith("s3://clipflow-bucket/sources/job-1/"))

    def test_run_once_retries_pending_job_on_failure(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "source_url": "https://youtube.com/watch?v=abc",
                "retry_count": 1,
                "status": "pending",
                "source_path": None,
                "error_log": None,
            }
        ]
        connection = FakeConnection(jobs)

        def failing_runner(source_url: str, output_dir: str, proxy_url: str | None) -> Path:
            raise RuntimeError("download failed")

        service = DownloaderService(
            connection_factory=lambda: connection,
            s3_bucket="clipflow-bucket",
            ytdlp_runner=failing_runner,
            s3_uploader=lambda *_: "s3://unused",
        )

        result = service.run_once()

        self.assertFalse(result)
        self.assertEqual(jobs[0]["status"], "pending")
        self.assertEqual(jobs[0]["retry_count"], 2)
        self.assertEqual(jobs[0]["error_log"], "download failed")

    def test_run_once_marks_job_failed_after_third_retry(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "source_url": "https://youtube.com/watch?v=abc",
                "retry_count": 2,
                "status": "pending",
                "source_path": None,
                "error_log": None,
            }
        ]
        connection = FakeConnection(jobs)

        service = DownloaderService(
            connection_factory=lambda: connection,
            s3_bucket="clipflow-bucket",
            ytdlp_runner=lambda *_: (_ for _ in ()).throw(RuntimeError("download failed")),
            s3_uploader=lambda *_: "s3://unused",
        )

        result = service.run_once()

        self.assertFalse(result)
        self.assertEqual(jobs[0]["status"], "failed")
        self.assertEqual(jobs[0]["retry_count"], 3)

    def test_run_once_is_idempotent_when_source_path_exists(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "source_url": "https://youtube.com/watch?v=abc",
                "retry_count": 0,
                "status": "pending",
                "source_path": "s3://clipflow-bucket/sources/job-1/video.mp4",
                "error_log": "old",
            }
        ]
        connection = FakeConnection(jobs)

        called = {"download": False}

        def should_not_run(*_):
            called["download"] = True
            raise AssertionError("download should not run when source_path already exists")

        service = DownloaderService(
            connection_factory=lambda: connection,
            s3_bucket="clipflow-bucket",
            ytdlp_runner=should_not_run,
            s3_uploader=lambda *_: "unused",
        )

        result = service.run_once()

        self.assertTrue(result)
        self.assertFalse(called["download"])
        self.assertEqual(jobs[0]["status"], "analyzing")
        self.assertIsNone(jobs[0]["error_log"])


if __name__ == "__main__":
    unittest.main()
