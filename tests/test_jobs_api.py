from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

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

        if compact.startswith("SELECT j.job_id, j.source_url, j.status, j.created_at, j.clip_manifest, c.name"):
            ordered = sorted(self.db.jobs.values(), key=lambda item: item["created_at"], reverse=True)
            self.result_many = [
                (
                    item["job_id"],
                    item["source_url"],
                    item["status"],
                    item["created_at"],
                    item["clip_manifest"],
                    self.db.channels[item["channel_id"]],
                )
                for item in ordered
            ]
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
                "status": "pending",
                "clip_manifest": [{"headline": "Clip A"}],
                "created_at": now,
            }
        }

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


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

    def test_dashboard_page_renders(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ClipFlow Dashboard", response.text)

    def test_list_jobs_returns_rows(self) -> None:
        response = self.client.get("/api/jobs")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["jobs"]), 1)
        self.assertEqual(payload["jobs"][0]["title"], "Clip A")

    def test_sync_channel_triggers_watcher(self) -> None:
        with patch("app.api.jobs.WatcherService.run_once", return_value=2) as run_once:
            response = self.client.post("/api/jobs/sync", json={"channel_id": "channel-1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["queued"], True)
        run_once.assert_called_once_with(channel_id="channel-1")


if __name__ == "__main__":
    unittest.main()
