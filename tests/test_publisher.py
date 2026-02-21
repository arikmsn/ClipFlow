import unittest

from app.services.publisher import PublisherService


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

        if query.startswith("UPDATE jobs SET status = CASE WHEN retry_count + 1 >= 3 THEN 'failed' ELSE 'scheduled' END"):
            for job in self.connection.jobs.values():
                if job["status"] == "scheduled" and job.get("updated_minutes_ago", 0) > 30:
                    next_retry = job["retry_count"] + 1
                    job["retry_count"] = next_retry
                    job["error_log"] = "Publisher timed out after 30 minutes"
                    job["status"] = "failed" if next_retry >= 3 else "scheduled"
            return

        if query.startswith("SELECT job_id, channel_id, render_path, retry_count, clip_manifest FROM jobs"):
            candidates = [
                job
                for job in self.connection.jobs.values()
                if job["status"] == "scheduled" and job["render_path"] is not None
            ]
            self._result = candidates[0] if candidates else None
            return

        if query.startswith("SELECT id, platform FROM social_accounts"):
            channel_id = params[0]
            rows = [
                (account["id"], account["platform"])
                for account in self.connection.accounts
                if account["channel_id"] == channel_id and account["status"] == "active"
            ]
            self._result = rows
            return

        if query.startswith("INSERT INTO social_posts"):
            job_id, platform, account_id, caption = params
            key = (job_id, platform, account_id)
            if key not in self.connection.social_posts:
                self.connection.social_posts[key] = {
                    "job_id": job_id,
                    "platform": platform,
                    "account_id": account_id,
                    "status": "queued",
                    "caption_text": caption,
                    "post_url": None,
                    "platform_post_id": None,
                    "last_error": None,
                    "attempt_count": 0,
                }
            return

        if query.startswith("SELECT account_id, platform, COALESCE(caption_text, '') FROM social_posts"):
            job_id = params[0]
            rows = [
                (post["account_id"], post["platform"], post["caption_text"])
                for post in self.connection.social_posts.values()
                if post["job_id"] == job_id and post["status"] != "published"
            ]
            self._result = rows
            return

        if query.startswith("UPDATE social_posts SET status = 'published'"):
            post_url, post_id, job_id, account_id, platform = params
            key = (job_id, platform, account_id)
            post = self.connection.social_posts[key]
            post["status"] = "published"
            post["post_url"] = post_url
            post["platform_post_id"] = post_id
            post["last_error"] = None
            post["attempt_count"] += 1
            return

        if query.startswith("UPDATE social_posts SET status = 'failed'"):
            error_message, job_id, account_id, platform = params
            key = (job_id, platform, account_id)
            post = self.connection.social_posts[key]
            post["status"] = "failed"
            post["last_error"] = error_message
            post["attempt_count"] += 1
            return

        if query.startswith("SELECT COUNT(*) FROM social_posts"):
            job_id = params[0]
            count = sum(
                1
                for post in self.connection.social_posts.values()
                if post["job_id"] == job_id and post["status"] == "published"
            )
            self._result = (count,)
            return

        if query.startswith("SELECT platform, post_url FROM social_posts"):
            job_id = params[0]
            rows = [
                (post["platform"], post["post_url"])
                for post in self.connection.social_posts.values()
                if post["job_id"] == job_id and post["status"] == "published" and post["post_url"] is not None
            ]
            rows.sort(key=lambda item: item[0])
            self._result = rows
            return

        if query.startswith("UPDATE jobs SET published_urls = %s"):
            published_urls, job_id = params
            self.connection.jobs[job_id]["published_urls"] = published_urls
            return

        if query.startswith("UPDATE jobs SET status = 'published'"):
            job_id = params[0]
            self.connection.jobs[job_id]["status"] = "published"
            self.connection.jobs[job_id]["error_log"] = None
            return

        if query.startswith("UPDATE jobs SET status = %s,"):
            status, retry_count, error_log, job_id = params
            job = self.connection.jobs[job_id]
            job["status"] = status
            job["retry_count"] = retry_count
            job["error_log"] = error_log
            return

        raise AssertionError(f"Unexpected query: {query}")

    def fetchone(self):
        if isinstance(self._result, dict):
            row = self._result
            return (
                row["job_id"],
                row["channel_id"],
                row["render_path"],
                row["retry_count"],
                row["clip_manifest"],
            )
        return self._result

    def fetchall(self):
        return self._result or []


class FakeConnection:
    def __init__(self, jobs, accounts, social_posts=None):
        self.jobs = jobs
        self.accounts = accounts
        self.social_posts = social_posts or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        return None

    def rollback(self):
        return None


class FakeAyrshareClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def post(self, render_path: str, caption: str, platforms: list[str]):
        platform = platforms[0]
        self.calls.append((render_path, caption, platform))
        response = self.responses.get(platform)
        if isinstance(response, Exception):
            raise response
        return {platform: response}


class PublisherServiceTests(unittest.TestCase):
    def test_run_once_publishes_all_platforms_and_marks_job_published(self):
        jobs = {
            "job-1": {
                "job_id": "job-1",
                "channel_id": "channel-1",
                "status": "scheduled",
                "render_path": "s3://bucket/renders/job-1/render.mp4",
                "retry_count": 0,
                "clip_manifest": [{"headline": "Top hook"}],
                "published_urls": [],
                "error_log": None,
            }
        }
        accounts = [
            {"id": "acc-1", "channel_id": "channel-1", "platform": "tiktok", "status": "active"},
            {"id": "acc-2", "channel_id": "channel-1", "platform": "instagram_reels", "status": "active"},
        ]
        ayrshare = FakeAyrshareClient(
            {
                "tiktok": {"post_url": "https://tiktok.com/post/1", "id": "tt-1"},
                "instagram_reels": {"post_url": "https://instagram.com/reel/1", "id": "ig-1"},
            }
        )
        connection = FakeConnection(jobs, accounts)

        service = PublisherService(connection_factory=lambda: connection, ayrshare_client=ayrshare)

        result = service.run_once()

        self.assertTrue(result)
        self.assertEqual(jobs["job-1"]["status"], "published")
        self.assertEqual(len(jobs["job-1"]["published_urls"]), 2)
        self.assertEqual(len(connection.social_posts), 2)
        self.assertEqual(connection.social_posts[("job-1", "tiktok", "acc-1")]["status"], "published")

    def test_run_once_partial_failure_marks_row_failed_and_keeps_job_scheduled(self):
        jobs = {
            "job-1": {
                "job_id": "job-1",
                "channel_id": "channel-1",
                "status": "scheduled",
                "render_path": "s3://bucket/renders/job-1/render.mp4",
                "retry_count": 0,
                "clip_manifest": [{"headline": "Top hook"}],
                "published_urls": [],
                "error_log": None,
            }
        }
        accounts = [
            {"id": "acc-1", "channel_id": "channel-1", "platform": "tiktok", "status": "active"},
            {"id": "acc-2", "channel_id": "channel-1", "platform": "instagram_reels", "status": "active"},
        ]
        ayrshare = FakeAyrshareClient(
            {
                "tiktok": {"post_url": "https://tiktok.com/post/1", "id": "tt-1"},
                "instagram_reels": RuntimeError("ig failure"),
            }
        )
        connection = FakeConnection(jobs, accounts)

        service = PublisherService(connection_factory=lambda: connection, ayrshare_client=ayrshare)

        result = service.run_once()

        self.assertFalse(result)
        self.assertEqual(jobs["job-1"]["status"], "scheduled")
        self.assertEqual(jobs["job-1"]["retry_count"], 1)
        self.assertEqual(connection.social_posts[("job-1", "tiktok", "acc-1")]["status"], "published")
        self.assertEqual(connection.social_posts[("job-1", "instagram_reels", "acc-2")]["status"], "failed")

    def test_run_once_recovers_stuck_scheduled_job(self):
        jobs = {
            "job-1": {
                "job_id": "job-1",
                "channel_id": "channel-1",
                "status": "scheduled",
                "render_path": "s3://bucket/renders/job-1/render.mp4",
                "retry_count": 0,
                "clip_manifest": [{"headline": "Top hook"}],
                "published_urls": [],
                "error_log": None,
                "updated_minutes_ago": 31,
            }
        }
        accounts = [{"id": "acc-1", "channel_id": "channel-1", "platform": "tiktok", "status": "active"}]
        ayrshare = FakeAyrshareClient({"tiktok": {"post_url": "https://tiktok.com/post/1", "id": "tt-1"}})
        connection = FakeConnection(jobs, accounts)

        service = PublisherService(connection_factory=lambda: connection, ayrshare_client=ayrshare)

        result = service.run_once()

        self.assertTrue(result)
        self.assertEqual(jobs["job-1"]["retry_count"], 1)
        self.assertEqual(jobs["job-1"]["status"], "published")


if __name__ == "__main__":
    unittest.main()
