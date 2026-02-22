import psycopg2
from contextlib import contextmanager
from app.config import settings

@contextmanager
def get_db_connection():
    """Context manager for PostgreSQL connections."""
    conn = None
    try:
        conn = psycopg2.connect(settings.database_url)
        yield conn
    except Exception as e:
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()