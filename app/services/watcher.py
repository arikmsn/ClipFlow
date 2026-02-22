from __future__ import annotations

import json
import logging
import re
import traceback
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Protocol

from app.utils import normalize_keyword_triggers

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelRecord:
    id: str
    source_channel_id: str | None
    source_channel_url: str | None
    keyword_triggers: tuple[str, ...]
    view_velocity_threshold: int
    min_video_length_seconds: int


@dataclass(frozen=True)
class VideoRecord:
    url: str
    title: str
    description: str
    view_count: int
    duration_seconds: int
    published_at: datetime


class CursorProtocol(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None: ...

    def fetchall(self) -> list[Any]: ...

    def fetchone(self) -> Any: ...

    def __enter__(self) -> "CursorProtocol": ...

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...


class ConnectionProtocol(Protocol):
    def cursor(self) -> CursorProtocol: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def __enter__(self) -> "ConnectionProtocol": ...

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...


class WatcherService:
    def __init__(
        self,
        connection_factory: Callable[[], ConnectionProtocol],
        youtube_api_key: str,
        youtube_fetcher: Callable[[ChannelRecord, str], list[VideoRecord]] | None = None,
        rss_fetcher: Callable[[ChannelRecord], list[str]] | None = None,
    ) -> None:
        if not youtube_api_key:
            raise ValueError("youtube_api_key is required for watcher filtering")
        self._connection_factory = connection_factory
        self._youtube_api_key = youtube_api_key
        self._youtube_fetcher = youtube_fetcher or self._fetch_youtube_videos
        self._rss_fetcher = rss_fetcher or self._fetch_rss_video_urls

    def run_once(self) -> int:
        created_jobs = 0
        with self._connection_factory() as connection:
            channels = self._load_active_channels(connection)
            for channel in channels:
                videos = self._videos_for_channel(channel)
                for video in videos:
                    if not self._passes_filters(video, channel):
                        continue
                    if self._create_pending_job(connection, channel.id, video.url):
                        created_jobs += 1
            connection.commit()
        return created_jobs

    def _load_active_channels(self, connection: ConnectionProtocol) -> list[ChannelRecord]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, source_channel_id, source_channel_url,
                       keyword_triggers, view_velocity_threshold, min_video_length_seconds
                FROM channels
                WHERE active = TRUE
                """
            )
            rows = cursor.fetchall()

        return [
            ChannelRecord(
                id=str(row[0]),
                source_channel_id=row[1],
                source_channel_url=row[2],
                keyword_triggers=normalize_keyword_triggers(row[3]),
                view_velocity_threshold=int(row[4]),
                min_video_length_seconds=int(row[5]),
            )
            for row in rows
        ]

    def _videos_for_channel(self, channel: ChannelRecord) -> list[VideoRecord]:
        try:
            return self._youtube_fetcher(channel, self._youtube_api_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("YouTube Data API failed for channel %s, using RSS fallback: %s", channel.id, exc)

        urls = self._rss_fetcher(channel)
        video_ids = [video_id for video_id in (_extract_video_id(url) for url in urls) if video_id]
        if not video_ids:
            return []
        return self._fetch_video_details(video_ids)

    def _passes_filters(self, video: VideoRecord, channel: ChannelRecord) -> bool:
        within_velocity_window = video.published_at >= datetime.now(tz=timezone.utc) - timedelta(hours=2)
        keyword_match = _has_keyword_match(video.title, video.description, channel.keyword_triggers)
        return (
            within_velocity_window
            and video.view_count >= channel.view_velocity_threshold
            and video.duration_seconds >= channel.min_video_length_seconds
            and keyword_match
        )

    def _create_pending_job(self, connection: ConnectionProtocol, channel_id: str, source_url: str) -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO jobs (source_url, channel_id, status)
                VALUES (%s, %s, 'pending')
                ON CONFLICT (source_url) DO NOTHING
                RETURNING job_id
                """,
                (source_url, channel_id),
            )
            return cursor.fetchone() is not None

    def _fetch_youtube_videos(self, channel: ChannelRecord, api_key: str) -> list[VideoRecord]:
        if not channel.source_channel_id:
            raise ValueError("channel source_channel_id is required for YouTube API fetch")
        query = urllib.parse.urlencode(
            {
                "part": "id",
                "channelId": channel.source_channel_id,
                "order": "date",
                "maxResults": 10,
                "type": "video",
                "key": api_key,
            }
        )
        url = f"https://www.googleapis.com/youtube/v3/search?{query}"
        payload = _load_json(url)
        video_ids = [item["id"]["videoId"] for item in payload.get("items", []) if item.get("id", {}).get("videoId")]
        if not video_ids:
            return []
        return self._fetch_video_details(video_ids)

    def _fetch_video_details(self, video_ids: Iterable[str]) -> list[VideoRecord]:
        ids = [item for item in video_ids if item]
        if not ids:
            return []

        query = urllib.parse.urlencode(
            {
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(ids),
                "key": self._youtube_api_key,
            }
        )
        url = f"https://www.googleapis.com/youtube/v3/videos?{query}"
        payload = _load_json(url)
        videos: list[VideoRecord] = []
        for item in payload.get("items", []):
            video_id = item.get("id")
            if not video_id:
                continue
            snippet = item.get("snippet", {})
            published_raw = snippet.get("publishedAt")
            published_at = _parse_youtube_datetime(published_raw)
            videos.append(
                VideoRecord(
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    title=str(snippet.get("title", "")),
                    description=str(snippet.get("description", "")),
                    view_count=int(item.get("statistics", {}).get("viewCount", 0)),
                    duration_seconds=_parse_iso8601_duration(
                        item.get("contentDetails", {}).get("duration", "PT0S")
                    ),
                    published_at=published_at,
                )
            )
        return videos

    def _fetch_rss_video_urls(self, channel: ChannelRecord) -> list[str]:
        channel_id = channel.source_channel_id or _extract_channel_id(channel.source_channel_url)
        if not channel_id:
            return []

        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        with urllib.request.urlopen(feed_url, timeout=10) as response:  # noqa: S310
            xml_bytes = response.read()

        root = ET.fromstring(xml_bytes)
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        links: list[str] = []
        for entry in root.findall("atom:entry", namespace):
            link = entry.find("atom:link", namespace)
            if link is not None:
                href = link.attrib.get("href")
                if href:
                    links.append(href)
        return links


def _load_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _extract_video_id(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname and "youtu.be" in parsed.hostname:
        return parsed.path.strip("/") or None
    query = urllib.parse.parse_qs(parsed.query)
    values = query.get("v")
    return values[0] if values else None


def _extract_channel_id(channel_url: str | None) -> str | None:
    if not channel_url:
        return None
    match = re.search(r"/channel/([A-Za-z0-9_-]+)", channel_url)
    if match:
        return match.group(1)
    return None


def _has_keyword_match(title: str, description: str, keywords: tuple[str, ...]) -> bool:
    if not keywords:
        return True
    text = f"{title} {description}".lower()
    return any(keyword in text for keyword in keywords)


def _parse_iso8601_duration(duration: str) -> int:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return (hours * 3600) + (minutes * 60) + seconds


def _parse_youtube_datetime(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(tz=timezone.utc)
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
