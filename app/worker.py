"""Infinite loop worker that runs all 5 pipeline services sequentially."""

import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable, Protocol

import boto3
import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

logger = logging.getLogger(__name__)


class CursorProtocol(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None: ...

    def fetchone(self) -> Any: ...

    def __enter__(self) -> "CursorProtocol": ...

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None: ...


class ConnectionProtocol(Protocol):
    def cursor(self) -> CursorProtocol: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def __enter__(self) -> "ConnectionProtocol": ...

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None: ...


_scripts_dir = str(Path.home() / "AppData/Roaming/Python/Python313/Scripts")
if _scripts_dir not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _scripts_dir + os.pathsep + os.environ.get("PATH", "")


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def get_db_connection() -> ConnectionProtocol:
    db_url = os.environ["DATABASE_URL"].replace(":6543/", ":5432/")
    return psycopg2.connect(db_url)


def make_s3_uploader() -> Callable[[Path, str, str], str]:
    endpoint_url = os.getenv("S3_ENDPOINT_URL")
    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_DEFAULT_REGION", "auto"),
    )

    def upload(local_path: Path, bucket: str, key: str) -> str:
        s3_client.upload_file(str(local_path), bucket, key)
        return f"s3://{bucket}/{key}"

    return upload


class RealDeepgramClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def transcribe(self, source_path: str) -> list:
        from deepgram import Deepgram

        dg_client = Deepgram(self._api_key)
        with open(source_path, "rb") as f:
            response = dg_client.transcription.prerecorded(
                {"buffer": f, "mimetype": "video/mp4"},
                {"punctuate": True, "utterances": True},
            )
        words = []
        for result in response.get("results", {}).get("channels", []):
            for word_data in result.get("alternatives", []).get("words", []):
                words.append(
                    type("WordTiming", (), {"start": word_data.get("start", 0), "end": word_data.get("end", 0), "word": word_data.get("word", "")})()
                )
        return words


class RealScoringClient:
    def __init__(self, openai_api_key: str) -> None:
        self._openai_api_key = openai_api_key

    def score_segments(self, segments: list, keyword_triggers: tuple) -> list:
        import openai

        client = openai.OpenAI(api_key=self._openai_api_key)
        scored = []
        for segment in segments:
            prompt = f"""Rate this video clip from 0-10 for virality potential:
Title/Description: {segment.text}
Keywords to match: {', '.join(keyword_triggers)}

Respond with ONLY a JSON object: {{"viral_score": 0.0-10.0, "hook_score": 0.0-10.0, "headline": "brief title"}}"""

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            import json

            result = json.loads(response.choices[0].message.content)
            scored.append(
                type(
                    "ScoredSegment",
                    (),
                    {
                        "clip_start": segment.clip_start,
                        "clip_end": segment.clip_end,
                        "headline": result.get("headline", ""),
                        "viral_score": float(result.get("viral_score", 0)),
                        "hook_score": float(result.get("hook_score", 0)),
                    },
                )()
            )
        return scored


class RealFaceTracker:
    def track(self, source_path: str, clip_start: float, clip_end: float):
        return type("CropCoordinates", (), {"x": 0, "y": 0, "width": 1080, "height": 1920})()


class RealFfmpegRunner:
    def run(
        self,
        source_path: str,
        clip_start: float,
        clip_end: float,
        crop_coords: Any,
        output_dir: str,
    ) -> Path:
        import subprocess

        duration = clip_end - clip_start
        output_path = Path(output_dir) / "rendered.mp4"
        command = [
            "ffmpeg",
            "-y",
            "-i", source_path,
            "-ss", str(clip_start),
            "-t", str(duration),
            "-vf", f"crop={crop_coords.width}:{crop_coords.height}:{crop_coords.x}:{crop_coords.y},scale=1080:1920",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            str(output_path),
        ]
        subprocess.run(command, check=True, capture_output=True)
        return output_path


class RealAyrshareClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def post(self, render_path: str, caption: str, platforms: list[str]) -> dict[str, Any]:
        import requests

        headers = {"Authorization": f"Bearer {self._api_key}"}
        data = {"post": caption, "platforms": platforms}
        response = requests.post("https://api.ayrshare.com/api/post", headers=headers, json=data)
        return response.json()


def make_watcher_service() -> Any:
    from app.services.watcher import WatcherService

    return WatcherService(
        connection_factory=get_db_connection,
        youtube_api_key=_require_env("YOUTUBE_API_KEY"),
    )


def make_downloader_service() -> Any:
    from app.services.downloader import DownloaderService

    return DownloaderService(
        connection_factory=get_db_connection,
        s3_bucket=os.environ["S3_BUCKET"],
        proxy_url=os.getenv("PROXY_URL"),
        s3_uploader=make_s3_uploader(),
    )


def make_analyzer_service() -> Any:
    from app.services.analyzer import AnalyzerService

    return AnalyzerService(
        connection_factory=get_db_connection,
        deepgram_client=RealDeepgramClient(_require_env("DEEPGRAM_API_KEY")),
        scoring_client=RealScoringClient(_require_env("OPENAI_API_KEY")),
    )


def make_renderer_service() -> Any:
    from app.services.renderer import RendererService

    return RendererService(
        connection_factory=get_db_connection,
        tracker=RealFaceTracker(),
        ffmpeg_runner=RealFfmpegRunner(),
        s3_uploader=make_s3_uploader(),
        s3_bucket=os.environ["S3_BUCKET"],
    )


def make_publisher_service() -> Any:
    from app.services.publisher import PublisherService

    return PublisherService(
        connection_factory=get_db_connection,
        ayrshare_client=RealAyrshareClient(_require_env("AYRSHARE_API_KEY")),
    )


class Worker:
    def __init__(self, poll_interval: int | None = None) -> None:
        self._poll_interval = poll_interval or int(os.getenv("WORKER_POLL_INTERVAL", "30"))
        self._shutdown_requested = False

        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        logger.info("Worker initialized with poll_interval=%d seconds", self._poll_interval)

    def _signal_handler(self, signum: int, frame: Any) -> None:
        logger.info("Received signal %d, initiating graceful shutdown", signum)
        self._shutdown_requested = True

    def run(self) -> None:
        logger.info("Starting ClipFlow worker main loop")

        while not self._shutdown_requested:
            iteration_start = time.time()

            self._run_iteration()

            elapsed = time.time() - iteration_start
            sleep_duration = max(0, self._poll_interval - elapsed)

            if not self._shutdown_requested and sleep_duration > 0:
                logger.debug("Sleeping for %.2f seconds until next poll", sleep_duration)
                time.sleep(sleep_duration)

        logger.info("Worker shutdown complete")

    def _run_iteration(self) -> None:
        services = [
            ("watcher", make_watcher_service),
            ("downloader", make_downloader_service),
            ("analyzer", make_analyzer_service),
            ("renderer", make_renderer_service),
            ("publisher", make_publisher_service),
        ]

        for name, service_factory in services:
            if self._shutdown_requested:
                break

            try:
                service = service_factory()
                result = service.run_once()
                logger.info(
                    "%s.run_once() returned %s",
                    name.capitalize(),
                    result,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "%s raised an exception, continuing to next service",
                    name.capitalize(),
                )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    poll_interval = os.getenv("WORKER_POLL_INTERVAL")
    if poll_interval:
        poll_interval = int(poll_interval)

    worker = Worker(poll_interval=poll_interval)
    worker.run()


if __name__ == "__main__":
    main()
