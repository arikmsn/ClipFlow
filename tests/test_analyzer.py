import unittest

from app.services.analyzer import AnalyzerService, ScoredSegment, Segment, WordTiming


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

        if query.startswith("UPDATE jobs SET status = CASE WHEN retry_count + 1 >= 3 THEN 'failed' ELSE 'analyzing' END"):
            for job in self.connection.jobs:
                if job["status"] == "analyzing" and job.get("updated_minutes_ago", 0) > 30:
                    next_retry = job["retry_count"] + 1
                    job["retry_count"] = next_retry
                    job["error_log"] = "Analyzer timed out after 30 minutes"
                    job["status"] = "failed" if next_retry >= 3 else "analyzing"
            return

        if query.startswith("SELECT j.job_id, j.source_path, j.retry_count, c.keyword_triggers"):
            analyzing = [job for job in self.connection.jobs if job["status"] == "analyzing" and job["source_path"] is not None]
            self._result = analyzing[0] if analyzing else None
            return

        if query.startswith("UPDATE jobs SET status = 'approval',"):
            clip_manifest, job_id = params
            job = self.connection._job(job_id)
            job["status"] = "approval"
            job["clip_manifest"] = clip_manifest
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
            self._result["retry_count"],
            self._result["keyword_triggers"],
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


class FakeDeepgramClient:
    def transcribe(self, source_path: str):
        self.last_source_path = source_path
        return [
            WordTiming(start=0.0, end=5.0, word="clipflow"),
            WordTiming(start=5.0, end=12.0, word="growth"),
            WordTiming(start=35.0, end=40.0, word="retention"),
            WordTiming(start=65.0, end=70.0, word="hooks"),
        ]


class FakeScoringClient:
    def score_segments(self, segments: list[Segment], keyword_triggers: tuple[str, ...]):
        self.last_segments = segments
        self.last_keyword_triggers = keyword_triggers
        return [
            ScoredSegment(0.0, 55.0, "ClipFlow growth hooks", 0.95, 0.93),
            ScoredSegment(30.0, 90.0, "Retention tactics", 0.91, 0.80),
            ScoredSegment(60.0, 120.0, "Audience hooks", 0.89, 0.84),
            ScoredSegment(90.0, 150.0, "Momentum strategies", 0.88, 0.78),
            ScoredSegment(120.0, 180.0, "Scale with systems", 0.86, 0.77),
            ScoredSegment(150.0, 210.0, "Extra segment", 0.70, 0.60),
        ]


class AnalyzerServiceTests(unittest.TestCase):
    def test_run_once_success_sets_approval_and_top5_manifest(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "source_path": "s3://bucket/sources/job-1/video.mp4",
                "retry_count": 0,
                "status": "analyzing",
                "error_log": "old",
                "clip_manifest": None,
                "keyword_triggers": ["clipflow", "hooks"],
            }
        ]
        connection = FakeConnection(jobs)
        deepgram = FakeDeepgramClient()
        scoring = FakeScoringClient()

        service = AnalyzerService(
            connection_factory=lambda: connection,
            deepgram_client=deepgram,
            scoring_client=scoring,
        )

        result = service.run_once()

        self.assertTrue(result)
        self.assertEqual(jobs[0]["status"], "approval")
        self.assertIsNone(jobs[0]["error_log"])
        self.assertEqual(len(jobs[0]["clip_manifest"]), 5)
        self.assertEqual(jobs[0]["clip_manifest"][0]["headline"], "ClipFlow growth hooks")
        self.assertEqual(jobs[0]["clip_manifest"][0]["keyword_triggers_matched"], ["clipflow", "hooks"])
        self.assertEqual(scoring.last_keyword_triggers, ("clipflow", "hooks"))

    def test_run_once_retries_on_failure(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "source_path": "s3://bucket/sources/job-1/video.mp4",
                "retry_count": 1,
                "status": "analyzing",
                "error_log": None,
                "clip_manifest": None,
                "keyword_triggers": [],
            }
        ]
        connection = FakeConnection(jobs)

        class FailingDeepgram:
            def transcribe(self, source_path: str):
                raise RuntimeError("deepgram failed")

        service = AnalyzerService(
            connection_factory=lambda: connection,
            deepgram_client=FailingDeepgram(),
            scoring_client=FakeScoringClient(),
        )

        result = service.run_once()

        self.assertFalse(result)
        self.assertEqual(jobs[0]["status"], "analyzing")
        self.assertEqual(jobs[0]["retry_count"], 2)
        self.assertEqual(jobs[0]["error_log"], "deepgram failed")

    def test_run_once_marks_failed_on_third_retry(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "source_path": "s3://bucket/sources/job-1/video.mp4",
                "retry_count": 2,
                "status": "analyzing",
                "error_log": None,
                "clip_manifest": None,
                "keyword_triggers": [],
            }
        ]
        connection = FakeConnection(jobs)

        class FailingScorer:
            def score_segments(self, segments: list[Segment], keyword_triggers: tuple[str, ...]):
                raise RuntimeError("scoring failed")

        service = AnalyzerService(
            connection_factory=lambda: connection,
            deepgram_client=FakeDeepgramClient(),
            scoring_client=FailingScorer(),
        )

        result = service.run_once()

        self.assertFalse(result)
        self.assertEqual(jobs[0]["status"], "failed")
        self.assertEqual(jobs[0]["retry_count"], 3)

    def test_run_once_recovers_stuck_analyzing_job(self) -> None:
        jobs = [
            {
                "job_id": "job-1",
                "source_path": "s3://bucket/sources/job-1/video.mp4",
                "retry_count": 0,
                "status": "analyzing",
                "error_log": None,
                "clip_manifest": None,
                "keyword_triggers": ["clipflow"],
                "updated_minutes_ago": 31,
            }
        ]
        connection = FakeConnection(jobs)

        service = AnalyzerService(
            connection_factory=lambda: connection,
            deepgram_client=FakeDeepgramClient(),
            scoring_client=FakeScoringClient(),
        )

        result = service.run_once()

        self.assertTrue(result)
        self.assertEqual(jobs[0]["status"], "approval")
        self.assertEqual(jobs[0]["retry_count"], 1)


if __name__ == "__main__":
    unittest.main()
