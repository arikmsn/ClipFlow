import unittest
from pathlib import Path

from app.services.renderer import CropCoordinates, RendererService


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

        if query.startswith("UPDATE jobs SET status = CASE WHEN retry_count + 1 >= 3 THEN 'failed' ELSE 'rendering' END"):
            for job in self.connection.jobs:
                if job["status"] == "rendering" and job.get("updated_minutes_ago", 0) > 30:
                    next_retry = job["retry_count"] + 1
                    job["retry_count"] = next_retry
                    job["error_log"] = "Renderer timed out after 30 minutes"
                    job["status"] = "failed" if next_retry >= 3 else "rendering"
            return

        if query.startswith("SELECT job_id, source_path, clip_manifest, retry_count, render_path FROM jobs"):
            rows = [job for job in self.connection.jobs if job["status"] == "rendering" and job["clip_manifest"] is not None]
            self._result = rows[0] if rows else None
            return

        if query.startswith("UPDATE jobs SET status = 'scheduled',"):
            render_path, job_id = params
            job = self.connection._job(job_id)
            job["status"] = "scheduled"
            job["render_path"] = render_path
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
            self._result["source_path"],
            self._result["clip_manifest"],
            self._result["retry_count"],
            self._result["render_path"],
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


class FakeTracker:
    def track(self, source_path: str, clip_start: float, clip_end: float) -> CropCoordinates:
        self.called_with = (source_path, clip_start, clip_end)
        return CropCoordinates(x=100, y=200, width=720, height=1280)


class FakeFfmpegRunner:
    def run(self, source_path: str, clip_start: float, clip_end: float, crop_coords: CropCoordinates, output_dir: str) -> Path:
        self.called_with = (source_path, clip_start, clip_end, crop_coords)
        output = Path(output_dir) / "render.mp4"
        output.write_text("rendered", encoding="utf-8")
        return output


class FakeUploader:
    def upload(self, local_path: Path, bucket: str, key: str) -> str:
        self.called_with = (local_path, bucket, key)
        return f"s3://{bucket}/{key}"


class RendererServiceTests(unittest.TestCase):
    def test_run_once_renders_and_marks_scheduled(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "source_path": "s3://bucket/sources/job-1/video.mp4",
                "clip_manifest": [{"clip_start": 5.0, "clip_end": 50.0}],
                "retry_count": 0,
                "status": "rendering",
                "render_path": None,
                "error_log": "old",
            }
        ]
        connection = FakeConnection(jobs)
        tracker = FakeTracker()
        ffmpeg = FakeFfmpegRunner()
        uploader = FakeUploader()

        service = RendererService(
            connection_factory=lambda: connection,
            tracker=tracker,
            ffmpeg_runner=ffmpeg,
            s3_uploader=uploader,
            s3_bucket="clipflow-bucket",
        )

        result = service.run_once()

        self.assertTrue(result)
        self.assertEqual(jobs[0]["status"], "scheduled")
        self.assertTrue(str(jobs[0]["render_path"]).startswith("s3://clipflow-bucket/renders/job-1/"))
        self.assertEqual(tracker.called_with, ("s3://bucket/sources/job-1/video.mp4", 5.0, 50.0))

    def test_run_once_retries_on_failure(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "source_path": "s3://bucket/sources/job-1/video.mp4",
                "clip_manifest": [{"clip_start": 5.0, "clip_end": 50.0}],
                "retry_count": 1,
                "status": "rendering",
                "render_path": None,
                "error_log": None,
            }
        ]
        connection = FakeConnection(jobs)

        class FailingTracker:
            def track(self, source_path: str, clip_start: float, clip_end: float) -> CropCoordinates:
                raise RuntimeError("tracker failed")

        service = RendererService(
            connection_factory=lambda: connection,
            tracker=FailingTracker(),
            ffmpeg_runner=FakeFfmpegRunner(),
            s3_uploader=FakeUploader(),
            s3_bucket="clipflow-bucket",
        )

        result = service.run_once()

        self.assertFalse(result)
        self.assertEqual(jobs[0]["status"], "rendering")
        self.assertEqual(jobs[0]["retry_count"], 2)
        self.assertEqual(jobs[0]["error_log"], "tracker failed")

    def test_run_once_marks_failed_after_third_retry(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "source_path": "s3://bucket/sources/job-1/video.mp4",
                "clip_manifest": [{"clip_start": 5.0, "clip_end": 50.0}],
                "retry_count": 2,
                "status": "rendering",
                "render_path": None,
                "error_log": None,
            }
        ]
        connection = FakeConnection(jobs)

        class FailingRunner:
            def run(self, source_path: str, clip_start: float, clip_end: float, crop_coords: CropCoordinates, output_dir: str) -> Path:
                raise RuntimeError("ffmpeg failed")

        service = RendererService(
            connection_factory=lambda: connection,
            tracker=FakeTracker(),
            ffmpeg_runner=FailingRunner(),
            s3_uploader=FakeUploader(),
            s3_bucket="clipflow-bucket",
        )

        result = service.run_once()

        self.assertFalse(result)
        self.assertEqual(jobs[0]["status"], "failed")
        self.assertEqual(jobs[0]["retry_count"], 3)

    def test_run_once_recovers_stuck_rendering_job(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "source_path": "s3://bucket/sources/job-1/video.mp4",
                "clip_manifest": [{"clip_start": 5.0, "clip_end": 50.0}],
                "retry_count": 0,
                "status": "rendering",
                "render_path": None,
                "error_log": None,
                "updated_minutes_ago": 31,
            }
        ]
        connection = FakeConnection(jobs)

        service = RendererService(
            connection_factory=lambda: connection,
            tracker=FakeTracker(),
            ffmpeg_runner=FakeFfmpegRunner(),
            s3_uploader=FakeUploader(),
            s3_bucket="clipflow-bucket",
        )

        result = service.run_once()

        self.assertTrue(result)
        self.assertEqual(jobs[0]["status"], "scheduled")
        self.assertEqual(jobs[0]["retry_count"], 1)


if __name__ == "__main__":
    unittest.main()