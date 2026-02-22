"""One-off script to run DownloaderService.run_once() with real dependencies."""

import os
import sys
from contextlib import contextmanager
from pathlib import Path

# Allow imports from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

# Ensure user-installed Python Scripts (yt-dlp) are on PATH
_scripts_dir = str(Path.home() / "AppData/Roaming/Python/Python313/Scripts")
if _scripts_dir not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _scripts_dir + os.pathsep + os.environ.get("PATH", "")

import boto3
import psycopg2

from app.services.downloader import DownloaderService


@contextmanager
def get_db_connection():
    """Context manager that yields a psycopg2 connection."""
    db_url = os.environ["DATABASE_URL"].replace(":6543/", ":5432/")
    conn = psycopg2.connect(db_url)
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def make_s3_uploader():
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


def main():
    bucket = os.environ["S3_BUCKET"]

    service = DownloaderService(
        connection_factory=get_db_connection,
        s3_bucket=bucket,
        proxy_url=os.getenv("PROXY_URL"),
        s3_uploader=make_s3_uploader(),
    )

    result = service.run_once()
    print(f"run_once() returned: {result}")


if __name__ == "__main__":
    main()
