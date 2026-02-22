from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class ScheduledJob:
    job_id: str
    channel_id: str
    render_path: str
    retry_count: int
    clip_manifest: list[dict[str, Any]]


@dataclass(frozen=True)
class SocialAccount:
    account_id: str
    platform: str


@dataclass(frozen=True)
class SocialPostTarget:
    account_id: str
    platform: str
    caption_text: str


class CursorProtocol(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None: ...

    def fetchone(self) -> Any: ...

    def fetchall(self) -> list[Any]: ...

    def __enter__(self) -> "CursorProtocol": ...

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...


class ConnectionProtocol(Protocol):
    def cursor(self) -> CursorProtocol: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def __enter__(self) -> "ConnectionProtocol": ...

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...


class AyrshareClientProtocol(Protocol):
    def post(self, render_path: str, caption: str, platforms: list[str]) -> dict[str, Any]: ...


class PublisherService:
    def __init__(
        self,
        connection_factory: Callable[[], ConnectionProtocol],
        ayrshare_client: AyrshareClientProtocol,
    ) -> None:
        self._connection_factory = connection_factory
        self._ayrshare_client = ayrshare_client

    def run_once(self) -> bool:
        with self._connection_factory() as connection:
            self._recover_stuck_scheduled_jobs(connection)
            job = self._claim_scheduled_job(connection)
            connection.commit()

        if job is None:
            return False

        accounts = self._load_channel_accounts(job.channel_id)
        if not accounts:
            self._handle_failure(job, "No active social accounts configured")
            return False

        caption = _default_caption(job.clip_manifest)
        self._ensure_social_posts(job.job_id, accounts, caption)

        targets = self._load_unpublished_targets(job.job_id)
        had_failure = False
        for target in targets:
            try:
                response = self._ayrshare_client.post(job.render_path, target.caption_text, [target.platform])
                post_url, platform_post_id = _extract_post_metadata(response, target.platform)
                if not post_url:
                    raise RuntimeError(f"No post URL returned for platform {target.platform}")
                self._mark_social_post_published(job.job_id, target.account_id, target.platform, post_url, platform_post_id)
            except Exception as exc:  # noqa: BLE001
                had_failure = True
                self._mark_social_post_failed(job.job_id, target.account_id, target.platform, str(exc))

        all_published = self._all_platform_posts_published(job.job_id, len(accounts))
        published_urls = self._build_published_urls_cache(job.job_id)
        self._update_job_published_urls(job.job_id, published_urls)

        if all_published:
            self._mark_job_published(job.job_id)
            return True

        if had_failure:
            self._handle_failure(job, "One or more platform publishes failed")
            return False

        return False

    def _recover_stuck_scheduled_jobs(self, connection: ConnectionProtocol) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET status = CASE WHEN retry_count + 1 >= 3 THEN 'failed' ELSE 'scheduled' END,
                    retry_count = retry_count + 1,
                    error_log = 'Publisher timed out after 30 minutes',
                    updated_at = NOW()
                WHERE status = 'scheduled'
                  AND updated_at < NOW() - INTERVAL '30 minutes'
                """
            )

    def _claim_scheduled_job(self, connection: ConnectionProtocol) -> ScheduledJob | None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT job_id, channel_id, render_path, retry_count, clip_manifest
                FROM jobs
                WHERE status = 'scheduled'
                  AND render_path IS NOT NULL
                ORDER BY updated_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return ScheduledJob(
            job_id=str(row[0]),
            channel_id=str(row[1]),
            render_path=str(row[2]),
            retry_count=int(row[3]),
            clip_manifest=list(row[4] or []),
        )

    def _load_channel_accounts(self, channel_id: str) -> list[SocialAccount]:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, platform
                    FROM social_accounts
                    WHERE channel_id = %s
                      AND status = 'active'
                    ORDER BY platform, id
                    """,
                    (channel_id,),
                )
                rows = cursor.fetchall()
            connection.commit()

        return [SocialAccount(account_id=str(row[0]), platform=str(row[1])) for row in rows]

    def _ensure_social_posts(self, job_id: str, accounts: list[SocialAccount], caption: str) -> None:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                for account in accounts:
                    cursor.execute(
                        """
                        INSERT INTO social_posts (job_id, platform, account_id, status, caption_text)
                        VALUES (%s, %s, %s, 'queued', %s)
                        ON CONFLICT (job_id, platform, account_id) DO NOTHING
                        """,
                        (job_id, account.platform, account.account_id, caption),
                    )
            connection.commit()

    def _load_unpublished_targets(self, job_id: str) -> list[SocialPostTarget]:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT account_id, platform, COALESCE(caption_text, '')
                    FROM social_posts
                    WHERE job_id = %s
                      AND status <> 'published'
                    ORDER BY platform, account_id
                    """,
                    (job_id,),
                )
                rows = cursor.fetchall()
            connection.commit()

        return [
            SocialPostTarget(account_id=str(row[0]), platform=str(row[1]), caption_text=str(row[2]))
            for row in rows
        ]

    def _mark_social_post_published(
        self,
        job_id: str,
        account_id: str,
        platform: str,
        post_url: str,
        platform_post_id: str | None,
    ) -> None:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE social_posts
                    SET status = 'published',
                        post_url = %s,
                        platform_post_id = %s,
                        published_at = NOW(),
                        last_error = NULL,
                        attempt_count = attempt_count + 1,
                        last_attempt_at = NOW(),
                        first_attempt_at = COALESCE(first_attempt_at, NOW()),
                        updated_at = NOW()
                    WHERE job_id = %s
                      AND account_id = %s
                      AND platform = %s
                    """,
                    (post_url, platform_post_id, job_id, account_id, platform),
                )
            connection.commit()

    def _mark_social_post_failed(self, job_id: str, account_id: str, platform: str, error_message: str) -> None:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE social_posts
                    SET status = 'failed',
                        last_error = %s,
                        attempt_count = attempt_count + 1,
                        last_attempt_at = NOW(),
                        first_attempt_at = COALESCE(first_attempt_at, NOW()),
                        updated_at = NOW()
                    WHERE job_id = %s
                      AND account_id = %s
                      AND platform = %s
                    """,
                    (error_message, job_id, account_id, platform),
                )
            connection.commit()

    def _all_platform_posts_published(self, job_id: str, expected_count: int) -> bool:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM social_posts
                    WHERE job_id = %s
                      AND status = 'published'
                    """,
                    (job_id,),
                )
                row = cursor.fetchone()
            connection.commit()

        published_count = int(row[0]) if row is not None else 0
        return published_count == expected_count

    def _build_published_urls_cache(self, job_id: str) -> list[dict[str, str]]:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT platform, post_url
                    FROM social_posts
                    WHERE job_id = %s
                      AND status = 'published'
                      AND post_url IS NOT NULL
                    ORDER BY platform
                    """,
                    (job_id,),
                )
                rows = cursor.fetchall()
            connection.commit()

        return [{"platform": str(row[0]), "post_url": str(row[1])} for row in rows]

    def _update_job_published_urls(self, job_id: str, published_urls: list[dict[str, str]]) -> None:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET published_urls = %s,
                        updated_at = NOW()
                    WHERE job_id = %s
                    """,
                    (published_urls, job_id),
                )
            connection.commit()

    def _mark_job_published(self, job_id: str) -> None:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = 'published',
                        error_log = NULL,
                        updated_at = NOW()
                    WHERE job_id = %s
                    """,
                    (job_id,),
                )
            connection.commit()

    def _handle_failure(self, job: ScheduledJob, error_message: str) -> None:
        next_retry = job.retry_count + 1
        next_status = "failed" if next_retry >= 3 else "scheduled"
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


def _default_caption(clip_manifest: list[dict[str, Any]]) -> str:
    if not clip_manifest:
        return ""
    headline = clip_manifest[0].get("headline")
    return str(headline) if headline else ""


def _extract_post_metadata(response: dict[str, Any], platform: str) -> tuple[str | None, str | None]:
    platform_payload = response.get(platform)
    if platform_payload is None:
        return None, None

    if isinstance(platform_payload, str):
        return platform_payload, None

    if isinstance(platform_payload, dict):
        post_url = platform_payload.get("post_url") or platform_payload.get("url")
        post_id = platform_payload.get("platform_post_id") or platform_payload.get("id")
        return str(post_url) if post_url else None, str(post_id) if post_id else None

    return None, None
