import unittest
from datetime import datetime, timedelta, timezone

from app.services.watcher import ChannelRecord, VideoRecord, WatcherService


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self._results = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, query: str, params=None) -> None:
        query = " ".join(query.split())
        if "FROM channels" in query:
            self._results = [
                (
                    "channel-1",
                    "yt-channel-id",
                    "https://www.youtube.com/channel/yt-channel-id",
                    ["clipflow", "virality"],
                    1000,
                    600,
                )
            ]
            return

        if "INSERT INTO jobs" in query:
            source_url = params[0]
            if source_url in self.connection.inserted_urls:
                self._results = []
            else:
                self.connection.inserted_urls.add(source_url)
                self._results = [("job-1",)]
            return

        raise AssertionError(f"Unexpected query: {query}")

    def fetchall(self):
        return self._results

    def fetchone(self):
        return self._results[0] if self._results else None


class FakeConnection:
    def __init__(self) -> None:
        self.inserted_urls = set()
        self.committed = False

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        return None


class WatcherServiceTests(unittest.TestCase):
    def test_run_once_creates_pending_job_for_matching_video(self) -> None:
        connection = FakeConnection()

        def youtube_fetcher(channel: ChannelRecord, _: str):
            return [
                VideoRecord(
                    url="https://www.youtube.com/watch?v=match1",
                    title="ClipFlow virality strategy",
                    description="A deep dive",
                    view_count=5000,
                    duration_seconds=900,
                    published_at=datetime.now(tz=timezone.utc) - timedelta(minutes=30),
                ),
                VideoRecord(
                    url="https://www.youtube.com/watch?v=too_old",
                    title="ClipFlow virality strategy",
                    description="A deep dive",
                    view_count=7000,
                    duration_seconds=900,
                    published_at=datetime.now(tz=timezone.utc) - timedelta(hours=3),
                ),
                VideoRecord(
                    url="https://www.youtube.com/watch?v=no_keyword",
                    title="Unrelated title",
                    description="No trigger words",
                    view_count=7000,
                    duration_seconds=900,
                    published_at=datetime.now(tz=timezone.utc) - timedelta(minutes=20),
                ),
            ]

        watcher = WatcherService(
            connection_factory=lambda: connection,
            youtube_api_key="test-key",
            youtube_fetcher=youtube_fetcher,
        )

        created = watcher.run_once()

        self.assertEqual(created, 1)
        self.assertIn("https://www.youtube.com/watch?v=match1", connection.inserted_urls)
        self.assertNotIn("https://www.youtube.com/watch?v=too_old", connection.inserted_urls)
        self.assertNotIn("https://www.youtube.com/watch?v=no_keyword", connection.inserted_urls)
        self.assertTrue(connection.committed)

    def test_run_once_is_idempotent_for_same_video_url(self) -> None:
        connection = FakeConnection()

        def youtube_fetcher(channel: ChannelRecord, _: str):
            return [
                VideoRecord(
                    url="https://www.youtube.com/watch?v=duplicate",
                    title="ClipFlow market analysis",
                    description="Contains virality keyword",
                    view_count=5000,
                    duration_seconds=900,
                    published_at=datetime.now(tz=timezone.utc) - timedelta(minutes=10),
                )
            ]

        watcher = WatcherService(
            connection_factory=lambda: connection,
            youtube_api_key="test-key",
            youtube_fetcher=youtube_fetcher,
        )

        first_created = watcher.run_once()
        second_created = watcher.run_once()

        self.assertEqual(first_created, 1)
        self.assertEqual(second_created, 0)
        self.assertEqual(len(connection.inserted_urls), 1)


if __name__ == "__main__":
    unittest.main()
