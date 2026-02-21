from __future__ import annotations

import unittest
from datetime import datetime, timezone

import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    TestClient = None

from app.api.deps import get_db
from app.main import app


class FakeCursor:
    def __init__(self, db: "FakeDB") -> None:
        self.db = db
        self.result_one = None
        self.result_many = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, query: str, params=None) -> None:
        compact = " ".join(query.split())

        if compact.startswith("SELECT j.job_id, j.status, j.viral_score, j.clip_manifest, c.name"):
            status, limit = params
            rows = [job for job in self.db.jobs.values() if job["status"] == status][:limit]
            self.result_many = [
                (job["job_id"], job["status"], job["viral_score"], job["clip_manifest"], self.db.channels[job["channel_id"]])
                for job in rows
            ]
            return

        if compact.startswith("SELECT j.job_id, j.source_url, j.channel_id, c.name, j.status"):
            job_id = params[0]
            job = self.db.jobs.get(job_id)
            if not job:
                self.result_one = None
                return
            self.result_one = (
                job["job_id"],
                job["source_url"],
                job["channel_id"],
                self.db.channels[job["channel_id"]],
                job["status"],
                job["viral_score"],
                job["clip_manifest"],
                job["render_path"],
                job["published_urls"],
                job["error_log"],
                job["retry_count"],
                job["created_at"],
                job["updated_at"],
            )
            return

        if compact.startswith("UPDATE jobs SET status = 'rendering'"):
            job_id = params[0]
            job = self.db.jobs.get(job_id)
            if not job:
                self.result_one = None
                return
            job["status"] = "rendering"
            job["error_log"] = None
            self.result_one = (job_id, "rendering")
            return

        if compact.startswith("UPDATE jobs SET status = 'rejected'"):
            reason, job_id = params
            job = self.db.jobs.get(job_id)
            if not job:
                self.result_one = None
                return
            job["status"] = "rejected"
            job["error_log"] = reason
            self.result_one = (job_id, "rejected")
            return

        if compact.startswith("SELECT clip_manifest FROM jobs WHERE job_id = %s"):
            job_id = params[0]
            job = self.db.jobs.get(job_id)
            self.result_one = (job["clip_manifest"],) if job else None
            return

        if compact.startswith("UPDATE jobs SET clip_manifest = %s"):
            manifest, job_id = params
            self.db.jobs[job_id]["clip_manifest"] = manifest
            return

        raise AssertionError(f"Unexpected query: {compact}")

    def fetchone(self):
        return self.result_one

    def fetchall(self):
        return self.result_many


class FakeDB:
    def __init__(self) -> None:
        now = datetime.now(tz=timezone.utc)
        self.channels = {"channel-1": "Growth Clips"}
        self.jobs = {
            "job-1": {
                "job_id": "job-1",
                "source_url": "https://youtube.com/watch?v=abc",
                "channel_id": "channel-1",
                "status": "approval",
                "viral_score": 0.93,
                "clip_manifest": [
                    {
                        "clip_start": 12.0,
                        "clip_end": 55.0,
                        "headline": "Original headline",
                        "viral_score": 0.93,
                        "hook_score": 0.91,
                        "keyword_triggers_matched": ["growth"],
                    }
                ],
                "render_path": None,
                "published_urls": [],
                "error_log": "old",
                "retry_count": 0,
                "created_at": now,
                "updated_at": now,
            }
        }

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        return None


class JobsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        if TestClient is None:
            self.skipTest("fastapi TestClient dependency is unavailable in this environment")

        self.db = FakeDB()

        def _get_db_override():
            return self.db

        app.dependency_overrides[get_db] = _get_db_override
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_list_jobs_returns_summary(self) -> None:
        response = self.client.get("/jobs", params={"status": "approval", "limit": 20})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["jobs"]), 1)
        self.assertEqual(payload["jobs"][0]["channel_name"], "Growth Clips")
        self.assertEqual(payload["jobs"][0]["candidate_count"], 1)

    def test_get_job_detail_returns_manifest(self) -> None:
        response = self.client.get("/jobs/job-1")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["job_id"], "job-1")
        self.assertEqual(payload["clip_manifest"][0]["headline"], "Original headline")

    def test_approve_endpoint_updates_status(self) -> None:
        response = self.client.post("/jobs/job-1/approve")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.jobs["job-1"]["status"], "rendering")
        self.assertIsNone(self.db.jobs["job-1"]["error_log"])

    def test_reject_endpoint_sets_reason(self) -> None:
        response = self.client.post("/jobs/job-1/reject", json={"reason": "low quality"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.jobs["job-1"]["status"], "rejected")
        self.assertEqual(self.db.jobs["job-1"]["error_log"], "low quality")

    def test_patch_headline_updates_top_candidate(self) -> None:
        response = self.client.patch("/jobs/job-1/headline", json={"headline": "New headline"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.jobs["job-1"]["clip_manifest"][0]["headline"], "New headline")


if __name__ == "__main__":
    unittest.main()
