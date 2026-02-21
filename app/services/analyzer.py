from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from app.utils import normalize_keyword_triggers


@dataclass(frozen=True)
class AnalyzingJob:
    job_id: str
    source_path: str
    retry_count: int
    keyword_triggers: tuple[str, ...]


@dataclass(frozen=True)
class WordTiming:
    start: float
    end: float
    word: str


@dataclass(frozen=True)
class Segment:
    clip_start: float
    clip_end: float
    text: str


@dataclass(frozen=True)
class ScoredSegment:
    clip_start: float
    clip_end: float
    headline: str
    viral_score: float
    hook_score: float


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


class DeepgramClientProtocol(Protocol):
    def transcribe(self, source_path: str) -> list[WordTiming]: ...


class ScoringClientProtocol(Protocol):
    def score_segments(self, segments: list[Segment], keyword_triggers: tuple[str, ...]) -> list[ScoredSegment]: ...


class AnalyzerService:
    def __init__(
        self,
        connection_factory: Callable[[], ConnectionProtocol],
        deepgram_client: DeepgramClientProtocol,
        scoring_client: ScoringClientProtocol,
    ) -> None:
        self._connection_factory = connection_factory
        self._deepgram_client = deepgram_client
        self._scoring_client = scoring_client

    def run_once(self) -> bool:
        with self._connection_factory() as connection:
            self._recover_stuck_analyzing_jobs(connection)
            job = self._claim_analyzing_job(connection)
            connection.commit()

        if job is None:
            return False

        try:
            words = self._deepgram_client.transcribe(job.source_path)
            segments = _build_segments(words)
            scored = self._scoring_client.score_segments(segments, job.keyword_triggers)
            manifest = _build_manifest(scored, job.keyword_triggers)
        except Exception as exc:  # noqa: BLE001
            self._handle_failure(job, str(exc))
            return False

        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = 'approval',
                        clip_manifest = %s,
                        error_log = NULL,
                        updated_at = NOW()
                    WHERE job_id = %s
                    """,
                    (manifest, job.job_id),
                )
            connection.commit()
        return True

    def _recover_stuck_analyzing_jobs(self, connection: ConnectionProtocol) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET status = CASE WHEN retry_count + 1 >= 3 THEN 'failed' ELSE 'analyzing' END,
                    retry_count = retry_count + 1,
                    error_log = 'Analyzer timed out after 30 minutes',
                    updated_at = NOW()
                WHERE status = 'analyzing'
                  AND updated_at < NOW() - INTERVAL '30 minutes'
                """
            )

    def _claim_analyzing_job(self, connection: ConnectionProtocol) -> AnalyzingJob | None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT j.job_id, j.source_path, j.retry_count, c.keyword_triggers
                FROM jobs j
                JOIN channels c ON c.id = j.channel_id
                WHERE j.status = 'analyzing'
                  AND j.source_path IS NOT NULL
                ORDER BY j.updated_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return AnalyzingJob(
            job_id=str(row[0]),
            source_path=str(row[1]),
            retry_count=int(row[2]),
            keyword_triggers=normalize_keyword_triggers(row[3]),
        )

    def _handle_failure(self, job: AnalyzingJob, error_message: str) -> None:
        next_retry = job.retry_count + 1
        next_status = "failed" if next_retry >= 3 else "analyzing"
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



def _build_segments(words: list[WordTiming]) -> list[Segment]:
    if not words:
        return []

    segments: list[Segment] = []
    timeline_start = words[0].start
    timeline_end = words[-1].end
    window_start = timeline_start

    while window_start < timeline_end:
        window_end = min(window_start + 60.0, timeline_end)
        if window_end - window_start < 30.0 and segments:
            break

        window_words = [w.word for w in words if w.start >= window_start and w.end <= window_end]
        if window_words:
            segments.append(
                Segment(
                    clip_start=round(window_start, 2),
                    clip_end=round(window_end, 2),
                    text=" ".join(window_words),
                )
            )
        window_start += 30.0

    return segments


def _build_manifest(scored_segments: list[ScoredSegment], keyword_triggers: tuple[str, ...]) -> list[dict[str, Any]]:
    ranked = sorted(scored_segments, key=lambda segment: (segment.viral_score, segment.hook_score), reverse=True)[:5]
    manifest: list[dict[str, Any]] = []
    for segment in ranked:
        matched_keywords = _matched_keywords(
            headline=segment.headline,
            keyword_triggers=keyword_triggers,
        )
        manifest.append(
            {
                "clip_start": segment.clip_start,
                "clip_end": segment.clip_end,
                "headline": segment.headline,
                "viral_score": segment.viral_score,
                "hook_score": segment.hook_score,
                "keyword_triggers_matched": matched_keywords,
            }
        )
    return manifest


def _matched_keywords(headline: str, keyword_triggers: tuple[str, ...]) -> list[str]:
    normalized = headline.lower()
    return [keyword for keyword in keyword_triggers if keyword in normalized]
